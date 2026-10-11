"""Local web server for browser microphone capture and CUDA transcription."""

from __future__ import annotations

import asyncio
import difflib
import gc
import html
import json
import logging
import math
import os
import re
import tempfile
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import av
import numpy as np
import requests
import torch
import yt_dlp
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub import snapshot_download
from pydantic import BaseModel, Field

from realtime_asr import (
    MODEL_ID,
    MODEL_SAMPLE_RATE,
    GpuTranscriber,
    Transcript,
    resample_to_model_rate,
    rms,
)


ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("romanian_asr")
WEB_ROOT = ROOT / "web"
PORTABLE_ROOT = Path(os.environ.get("VOICE_PORTABLE_ROOT", ROOT.parent)).resolve()
MODEL_CONFIG_PATH = PORTABLE_ROOT / "voice-models.json"
VOICE_SETTINGS_PATH = PORTABLE_ROOT / "voice-settings.json"
DEFAULT_VOICE_SETTINGS = {
    "max_segment_seconds": 7.0,
    "silence_seconds": 0.60,
    "voice_threshold": 0.010,
}
MODEL_PRESETS = [
    {
        "id": MODEL_ID,
        "label": "NVIDIA Parakeet TDT 0.6B v3",
        "source": "preset",
        "note": "Implicit rapid pentru CUDA. Bun pentru răspuns live.",
    },
    {
        "id": "openai/whisper-large-v3-turbo",
        "label": "Whisper Large v3 Turbo",
        "source": "preset",
        "note": "Model multilingv. Mai mare și mai lent, dar flexibil.",
    },
    {
        "id": "openai/whisper-small",
        "label": "Whisper Small",
        "source": "preset",
        "note": "Model multilingv mai mic pentru testare.",
    },
]
UI_LANGUAGE = os.environ.get("APP_UI_LANGUAGE", "ro").strip().lower()
if UI_LANGUAGE not in {"ro", "en"}:
    UI_LANGUAGE = "ro"
MAX_AUDIO_SECONDS = 2 * 60 * 60
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
RESURSE_SEARCH_URL = "https://www.resursecrestine.ro/web-api-search"
RESURSE_BIBLE_URL = "https://biblia.resursecrestine.ro"
PPT_FINDER_URL = os.environ.get("PPT_FINDER_URL", "http://127.0.0.1:8080").rstrip("/")
VERSE_NOTIFY_COOLDOWN = float(os.environ.get("VERSE_NOTIFY_COOLDOWN", "30") or "30")
LAST_NOTIFIED_VERSE = {"reference": "", "timestamp": 0.0}
CONFIGURED_SAMPLE_PATH = (
    Path(os.environ["ROMANIAN_ASR_SAMPLE"]).resolve()
    if os.environ.get("ROMANIAN_ASR_SAMPLE")
    else None
)


def _clean_model_id(value: str) -> str:
    model_id = str(value or "").strip().strip('"')
    if not model_id or len(model_id) > 500 or any(character in model_id for character in "\r\n\t"):
        raise ValueError("Model invalid.")
    return model_id


def read_model_config() -> dict[str, Any]:
    config = {"selected": os.environ.get("ROMANIAN_ASR_MODEL", MODEL_ID), "models": []}
    try:
        if MODEL_CONFIG_PATH.is_file():
            loaded = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if isinstance(loaded.get("selected"), str):
                    config["selected"] = loaded["selected"]
                if isinstance(loaded.get("models"), list):
                    config["models"] = [item for item in loaded["models"] if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read model config %s", MODEL_CONFIG_PATH)
    return config


def write_model_config(config: dict[str, Any]) -> None:
    MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def model_label(model_id: str) -> str:
    if os.path.isdir(model_id):
        return Path(model_id).name
    return model_id.rsplit("/", 1)[-1] or model_id


def normalize_model_config(config: dict[str, Any]) -> dict[str, Any]:
    selected = _clean_model_id(str(config.get("selected") or MODEL_ID))
    models: dict[str, dict[str, Any]] = {}
    for item in MODEL_PRESETS + list(config.get("models") or []):
        try:
            model_id = _clean_model_id(str(item.get("id") or ""))
        except ValueError:
            continue
        models[model_id] = {
            "id": model_id,
            "label": str(item.get("label") or model_label(model_id)).strip()[:120],
            "source": str(item.get("source") or "custom").strip()[:40],
            "note": str(item.get("note") or "").strip()[:400],
            "installed": bool(item.get("installed")) or item.get("source") in {"preset", "local"},
        }
    if selected not in models:
        models[selected] = {
            "id": selected,
            "label": model_label(selected),
            "source": "custom",
            "note": "Model selectat din configurație.",
            "installed": os.path.isdir(selected),
        }
    return {"selected": selected, "models": list(models.values())}


def active_model_name() -> str:
    try:
        return normalize_model_config(read_model_config())["selected"]
    except ValueError:
        return os.environ.get("ROMANIAN_ASR_MODEL", MODEL_ID)


MODEL_NAME = active_model_name()


def clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(max(number, minimum), maximum)


def normalize_voice_settings(settings: dict[str, Any] | None = None) -> dict[str, float]:
    raw = settings or {}
    return {
        "max_segment_seconds": clamp_float(raw.get("max_segment_seconds"), DEFAULT_VOICE_SETTINGS["max_segment_seconds"], 2.0, 15.0),
        "silence_seconds": clamp_float(raw.get("silence_seconds"), DEFAULT_VOICE_SETTINGS["silence_seconds"], 0.10, 1.00),
        "voice_threshold": clamp_float(raw.get("voice_threshold"), DEFAULT_VOICE_SETTINGS["voice_threshold"], 0.001, 0.030),
    }


def read_voice_settings() -> dict[str, float]:
    try:
        if VOICE_SETTINGS_PATH.is_file():
            loaded = json.loads(VOICE_SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return normalize_voice_settings(loaded)
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read voice settings %s", VOICE_SETTINGS_PATH)
    return normalize_voice_settings(DEFAULT_VOICE_SETTINGS)


def write_voice_settings(settings: dict[str, Any]) -> dict[str, float]:
    normalized = normalize_voice_settings(settings)
    VOICE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_SETTINGS_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def load_transcriber(model_name: str):
    transcriber = GpuTranscriber(model_name, fp16=True)
    warmup = transcriber.warm_up()
    return transcriber, warmup, torch.cuda.get_device_name(0)


BOOK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("1 Samuel", "1-samuel", r"(?:\b1|\bunu|\bintai|\bintaia)\s*sam(?:uel|u\s*el|bu\s*el)"),
    ("2 Samuel", "2-samuel", r"(?:\b2|\bdoi|\ba\s*doua)\s*sam(?:uel|u\s*el|bu\s*el)"),
    ("1 Corinteni", "1-corinteni", r"(?:\b1|\bunu|\bintai|\bintaia)\s*corint\w*"),
    ("2 Corinteni", "2-corinteni", r"(?:\b2|\bdoi|\ba\s*doua)\s*corint\w*"),
    ("1 Timotei", "1-timotei", r"(?:\b1|\bunu|\bonu|\boni|\bintai|\bintaia)\s*timote\w*"),
    ("2 Timotei", "2-timotei", r"(?:\b2|\bdoi|\ba\s*doua)\s*timote\w*"),
    ("1 Petru", "1-petru", r"(?:\b1|\bunu|\bintai|\bintaia)\s*p(?:etru|retru)\w*"),
    ("2 Petru", "2-petru", r"(?:\b2|\bdoi|\ba\s*doua)\s*petru\w*"),
    ("1 Ioan", "1-ioan", r"(?:\b1|\bunu|\bintai|\bintaia)\s*(?:ioan|oam)\w*"),
    ("2 Ioan", "2-ioan", r"(?:\b2|\bdoi|\ba\s*doua)\s*ioan\w*"),
    ("3 Ioan", "3-ioan", r"(?:\b3|\btrei|\ba\s*treia)\s*ioan\w*"),
    ("Geneza", "geneza", r"\bgeneza\w*"),
    ("Exodul", "exodul", r"\bexod\w*"),
    ("Numeri", "numeri", r"\bnumeri\w*"),
    ("Deuteronom", "deuteronom", r"\bdeuteronom\w*"),
    ("Iosua", "iosua", r"\biosua\w*"),
    ("Judecători", "judecatori", r"\bjudecator\w*"),
    ("Rut", "rut", r"\brut\b"),
    ("Neemia", "neemia", r"\bneemia\w*"),
    ("Estera", "estera", r"\bestera\w*"),
    ("Iov", "iov", r"\biov\b"),
    ("Psalmii", "psalmii", r"\bpsalm\w*"),
    ("Proverbe", "proverbe", r"\bproverb\w*"),
    ("Eclesiastul", "eclesiastul", r"\beclesiast\w*"),
    ("Isaia", "isaia", r"\bisaia\w*"),
    ("Ieremia", "ieremia", r"\b(?:ieremia|eremia|irenia|eremina)\w*"),
    ("Ezechiel", "ezechiel", r"\bezechiel\w*"),
    ("Daniel", "daniel", r"\bdaniel\w*"),
    ("Osea", "osea", r"\bosea\w*"),
    ("Ioel", "ioel", r"\bioel\w*"),
    ("Amos", "amos", r"\bamos\b"),
    ("Iona", "iona", r"\biona\w*"),
    ("Mica", "mica", r"\bmica\b"),
    ("Naum", "naum", r"\bnaum\b"),
    ("Habacuc", "habacuc", r"\bhabacuc\w*"),
    ("Țefania", "tefania", r"\btefania\w*"),
    ("Hagai", "hagai", r"\bhagai\w*"),
    ("Zaharia", "zaharia", r"\bzaharia\w*"),
    ("Maleahi", "maleahi", r"\bmaleahi\w*"),
    ("Matei", "matei", r"\b(?:matei|mati)\w*"),
    ("Marcu", "marcu", r"\bmarcu\w*"),
    ("Luca", "luca", r"\bluca\b"),
    ("Ioan", "ioan", r"\bioan\w*"),
    ("Faptele apostolilor", "faptele-apostolilor", r"\bfaptele(?:\s+apostolilor)?"),
    ("Romani", "romani", r"\bromani\w*"),
    ("Galateni", "galateni", r"\bgalateni\w*"),
    ("Efeseni", "efeseni", r"\befeseni\w*"),
    ("Filipeni", "filipeni", r"\bfilipeni\w*"),
    ("Coloseni", "coloseni", r"\bcoloseni\w*"),
    ("Evrei", "evrei", r"\bevrei\w*"),
    ("Iacov", "iacov", r"\biacov\w*"),
    ("Iuda", "iuda", r"\biuda\b"),
    ("Apocalipsa", "apocalipsa", r"\bapocalips\w*"),
)


def _number_aliases() -> dict[int, tuple[str, ...]]:
    aliases: dict[int, tuple[str, ...]] = {
        1: ("unu", "una", "intai", "intaia", "onu"),
        2: ("doi", "doua"),
        3: ("trei",),
        4: ("patru",),
        5: ("cinci",),
        6: ("sase",),
        7: ("sapte",),
        8: ("opt",),
        9: ("noua", "nou"),
        10: ("zece",),
        11: ("unsprezece", "unspe"),
        12: ("doisprezece", "doispe"),
        13: ("treisprezece", "treispe"),
        14: ("paisprezece", "paispe", "paisluceu"),
        15: ("cincisprezece", "cinsprezece", "cinspe"),
        16: ("saisprezece", "saispe"),
        17: ("saptesprezece", "saptespe"),
        18: ("optsprezece", "optusprezece", "optusprzece", "optspe"),
        19: ("nouasprezece", "nouaspruzece", "nouaspe"),
    }
    ones = {1: "unu", 2: "doi", 3: "trei", 4: "patru", 5: "cinci", 6: "sase", 7: "sapte", 8: "opt", 9: "noua"}
    tens = {20: "douazeci", 30: "treizeci", 40: "patruzeci", 50: "cincizeci", 60: "saizeci", 70: "saptezeci", 80: "optzeci", 90: "nouazeci"}
    for base, word in tens.items():
        aliases[base] = (word,)
        for digit, one in ones.items():
            aliases[base + digit] = (f"{word} si {one}", f"{word} {one}")
    return aliases


NUMBER_ALIASES = _number_aliases()
CHAPTER_MARKER_RE = r"capi(?:tol|otol|tolo)(?:ul|o)?"
VERSE_MARKER_RE = r"v(?:erset|eset|efet)(?:ul|ele|e)?"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One worker keeps CUDA operations serialized even if a client disconnects
    # while an inference is still running in its worker thread.
    app.state.gpu_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ro-asr-gpu")
    app.state.transcriber = None
    app.state.model_state = "loading"
    app.state.startup_error = None
    app.state.source_lock = asyncio.Lock()
    app.state.last_sample_result = None
    app.state.model_name = MODEL_NAME
    app.state.voice_settings = read_voice_settings()

    async def initialize():
        try:
            transcriber, warmup, gpu_name = await asyncio.get_running_loop().run_in_executor(
                app.state.gpu_executor, load_transcriber, app.state.model_name
            )
            app.state.transcriber = transcriber
            app.state.warmup_seconds = warmup.inference_seconds
            app.state.gpu_name = gpu_name
            app.state.model_state = "ready"
            logger.info("Voice app ready on %s; warm-up %.2fs", gpu_name, warmup.inference_seconds)
        except Exception:
            app.state.model_state = "error"
            app.state.startup_error = (
                "Modelul vocal nu a putut porni. Verifică jurnalul aplicației, "
                "placa NVIDIA și fișierele modelului, apoi repornește aplicația vocală."
            )
            logger.exception("Could not initialize the voice model")

    # Serve the UI and readiness endpoint during model loading or GPU failure.
    initialization = asyncio.create_task(initialize())
    try:
        yield
    finally:
        initialization.cancel()
        await asyncio.gather(initialization, return_exceptions=True)
        app.state.gpu_executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Transcriere RO Live", lifespan=lifespan)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.error("Request failed: %s", request.url.path, exc_info=(type(error), error, error.__traceback__))
    return JSONResponse(
        {"detail": "Operația nu a reușit. Textul afișat este păstrat; poți încerca din nou."},
        status_code=500,
    )


def require_ready() -> None:
    if getattr(app.state, "model_state", "loading") != "ready":
        raise HTTPException(503, getattr(app.state, "startup_error", None) or "Modelul vocal se încarcă. Așteaptă starea pregătit.")


async def reserve_audio_source():
    require_ready()
    if app.state.source_lock.locked():
        raise HTTPException(409, "O sesiune vocală este deja activă. Oprește microfonul sau redarea înainte de o altă transcriere.")
    async with app.state.source_lock:
        yield


async def infer_audio(audio: np.ndarray) -> Transcript:
    require_ready()
    return await asyncio.get_running_loop().run_in_executor(
        app.state.gpu_executor, app.state.transcriber.transcribe, audio
    )


@app.get("/api/status")
async def status() -> JSONResponse:
    ready = getattr(app.state, "model_state", "loading") == "ready"
    payload: dict[str, Any] = {
        "service": "romanian-realtime-asr",
        "ready": ready,
        "state": getattr(app.state, "model_state", "loading"),
        "error": getattr(app.state, "startup_error", None),
        "busy": app.state.source_lock.locked(),
        "model": getattr(app.state, "model_name", MODEL_NAME),
        "sample_rate": MODEL_SAMPLE_RATE,
        "settings": getattr(app.state, "voice_settings", read_voice_settings()),
    }
    if ready:
        payload.update(
            {
                "gpu": app.state.gpu_name,
                "dtype": "FP16",
                "vram_mb": round(torch.cuda.memory_allocated() / (1024**2)),
                "warmup_ms": round(app.state.warmup_seconds * 1000),
            }
        )
    if CONFIGURED_SAMPLE_PATH and CONFIGURED_SAMPLE_PATH.is_file():
        payload["sample"] = {
            "name": CONFIGURED_SAMPLE_PATH.name,
            "audio_url": "/api/sample-audio",
        }
    return JSONResponse(payload)


@app.get("/api/ui-config")
async def ui_config() -> JSONResponse:
    return JSONResponse({"language": UI_LANGUAGE})


@app.get("/api/sample-audio")
async def configured_sample_audio() -> FileResponse:
    if not CONFIGURED_SAMPLE_PATH or not CONFIGURED_SAMPLE_PATH.is_file():
        raise HTTPException(404, "Nu este configurat niciun eșantion local.")
    return FileResponse(CONFIGURED_SAMPLE_PATH, media_type="audio/mpeg", filename=CONFIGURED_SAMPLE_PATH.name)


@app.get("/api/last-sample-result")
async def last_sample_result() -> JSONResponse:
    result = getattr(app.state, "last_sample_result", None)
    if not result:
        raise HTTPException(404, "Nu există încă un rezultat pentru eșantion.")
    return JSONResponse(result)


class TextPayload(BaseModel):
    text: str = Field(max_length=16000)


class YoutubePayload(BaseModel):
    url: str = Field(max_length=4096)


class ModelSelectionPayload(BaseModel):
    model: str = Field(max_length=500)


class ModelInstallPayload(BaseModel):
    model: str = Field(max_length=500)
    label: str = Field(default="", max_length=120)


class VoiceSettingsPayload(BaseModel):
    max_segment_seconds: float = Field(ge=2.0, le=15.0)
    silence_seconds: float = Field(ge=0.10, le=1.00)
    voice_threshold: float = Field(ge=0.001, le=0.030)


@app.get("/api/settings")
async def voice_settings() -> JSONResponse:
    settings = read_voice_settings()
    app.state.voice_settings = settings
    return JSONResponse({"settings": settings, "config_path": str(VOICE_SETTINGS_PATH)})


@app.post("/api/settings")
async def save_voice_settings(payload: VoiceSettingsPayload) -> JSONResponse:
    settings = write_voice_settings(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    app.state.voice_settings = settings
    return JSONResponse({"success": True, "settings": settings, "config_path": str(VOICE_SETTINGS_PATH)})


@app.get("/api/models")
async def models_config() -> JSONResponse:
    config = normalize_model_config(read_model_config())
    active = getattr(app.state, "model_name", MODEL_NAME)
    return JSONResponse(
        {
            "active": active,
            "selected": config["selected"],
            "loading": getattr(app.state, "model_state", "loading") == "loading",
            "config_path": str(MODEL_CONFIG_PATH),
            "models": config["models"],
        }
    )


@app.post("/api/models/select")
async def select_model(payload: ModelSelectionPayload) -> JSONResponse:
    if app.state.source_lock.locked():
        raise HTTPException(409, "Oprește microfonul sau redarea înainte de schimbarea modelului.")
    model_id = _clean_model_id(payload.model)
    if getattr(app.state, "model_state", "loading") == "loading":
        raise HTTPException(409, "Așteaptă finalizarea încărcării modelului curent.")
    config = normalize_model_config(read_model_config())
    known = {item["id"] for item in config["models"]}
    if model_id not in known and not os.path.isdir(model_id):
        raise HTTPException(404, "Modelul nu este în listă. Instalează-l sau adaugă-l mai întâi.")
    if model_id == getattr(app.state, "model_name", MODEL_NAME) and getattr(app.state, "model_state", "") == "ready":
        config["selected"] = model_id
        write_model_config(config)
        return JSONResponse({"success": True, "selected": model_id, "active": model_id, "loaded": True})

    app.state.model_state = "loading"
    app.state.startup_error = None
    old_transcriber = app.state.transcriber
    app.state.transcriber = None
    try:
        del old_transcriber
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        transcriber, warmup, gpu_name = await asyncio.get_running_loop().run_in_executor(
            app.state.gpu_executor, load_transcriber, model_id
        )
    except Exception as error:
        app.state.model_state = "error"
        app.state.startup_error = f"Modelul nu a putut fi încărcat: {error}"
        logger.exception("Could not switch voice model to %s", model_id)
        raise HTTPException(500, app.state.startup_error) from error

    config["selected"] = model_id
    write_model_config(config)
    app.state.transcriber = transcriber
    app.state.model_name = model_id
    app.state.warmup_seconds = warmup.inference_seconds
    app.state.gpu_name = gpu_name
    app.state.model_state = "ready"
    app.state.last_sample_result = None
    logger.info("Voice model switched live to %s; warm-up %.2fs", model_id, warmup.inference_seconds)
    return JSONResponse(
        {
            "success": True,
            "selected": model_id,
            "active": model_id,
            "loaded": True,
            "warmup_ms": round(warmup.inference_seconds * 1000),
        }
    )


@app.post("/api/models/install")
async def install_model(payload: ModelInstallPayload) -> JSONResponse:
    if app.state.source_lock.locked():
        raise HTTPException(409, "Oprește microfonul sau redarea înainte de instalarea modelului.")
    model_id = _clean_model_id(payload.model)
    label = payload.label.strip() or model_label(model_id)
    source = "custom"
    note = "Model Hugging Face descărcat în cache."

    if os.path.isdir(model_id):
        source = "local"
        note = "Folder local adăugat. Mută folderul împreună cu aplicația dacă schimbi calculatorul."
    else:
        try:
            await asyncio.to_thread(
                snapshot_download,
                repo_id=model_id,
                local_files_only=False,
                resume_download=True,
            )
        except Exception as error:
            logger.warning("Could not install model %s: %s", model_id, error)
            raise HTTPException(400, f"Modelul nu a putut fi instalat: {error}") from error

    config = normalize_model_config(read_model_config())
    models = [item for item in config["models"] if item["id"] != model_id]
    models.append({"id": model_id, "label": label, "source": source, "note": note, "installed": True})
    config["models"] = models
    write_model_config(config)
    return JSONResponse({"success": True, "model": model_id, "models": normalize_model_config(config)["models"]})


def decode_audio_file(path: Path) -> np.ndarray:
    parts: list[np.ndarray] = []
    total_samples = 0
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("Fișierul nu conține o pistă audio.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=MODEL_SAMPLE_RATE)
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                values = converted.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                total_samples += len(values)
                if total_samples > MAX_AUDIO_SECONDS * MODEL_SAMPLE_RATE:
                    raise ValueError("Eșantionul audio depășește limita de 2 ore.")
                parts.append(values.copy())
        for converted in resampler.resample(None):
            parts.append(converted.to_ndarray().reshape(-1).astype(np.float32, copy=True))
    if not parts:
        raise ValueError("Nu am putut decoda sunetul din acest fișier.")
    return np.concatenate(parts)


def merge_transcript(previous: str, current: str) -> str:
    previous_words = previous.split()
    current_words = current.split()
    if not previous_words:
        return current.strip()
    if not current_words:
        return previous.strip()
    max_overlap = min(10, len(previous_words), len(current_words))
    for size in range(max_overlap, 1, -1):
        if previous_words[-size:] == current_words[:size]:
            return " ".join(previous_words + current_words[size:])
    return " ".join(previous_words + current_words)


def normalize_romanian(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9:.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_spoken_number(value: str, max_skip: int = 2) -> tuple[int | None, int, float]:
    tokens = re.findall(r"[a-z0-9]+", normalize_romanian(value))[:5]
    if not tokens:
        return None, 0, 0.0
    if tokens[0].isdigit():
        return int(tokens[0]), 1, 1.0

    best: tuple[int | None, int, float] = (None, 0, 0.0)
    for start in range(min(max_skip + 1, len(tokens))):
        for number, aliases in NUMBER_ALIASES.items():
            for alias in aliases:
                alias_compact = alias.replace(" ", "")
                expected_words = len(alias.split())
                split_allowance = 1 if expected_words == 1 and len(alias_compact) >= 6 else 0
                maximum = min(len(tokens) - start, expected_words + split_allowance)
                for length in range(1, maximum + 1):
                    candidate = "".join(tokens[start : start + length])
                    score = difflib.SequenceMatcher(None, candidate, alias_compact).ratio()
                    score -= abs(length - expected_words) * 0.06
                    score -= start * 0.04
                    consumed = start + length
                    if score > best[2] or (abs(score - best[2]) < 0.001 and consumed > best[1]):
                        best = (number, consumed, score)

    threshold = 0.72
    return best if best[2] >= threshold else (None, 0, best[2])


def extract_bible_references(text: str) -> list[dict[str, Any]]:
    searchable = normalize_romanian(text)
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, int]] = set()
    seen_starts: set[tuple[str, int, int]] = set()
    occupied_book_spans: list[tuple[int, int]] = []

    for book, slug, pattern in BOOK_PATTERNS:
        for book_match in re.finditer(pattern, searchable):
            if any(book_match.start() < end and book_match.end() > start for start, end in occupied_book_spans):
                continue
            occupied_book_spans.append((book_match.start(), book_match.end()))
            window = searchable[book_match.end() : book_match.end() + 180]
            context = searchable[book_match.end() : book_match.end() + 420]

            direct = re.match(r"\s*(\d{1,3})\s*:\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?", window)
            if direct:
                chapter = int(direct.group(1))
                verse_start = int(direct.group(2))
                verse_end = int(direct.group(3) or verse_start)
                key = (slug, chapter, verse_start, verse_end)
                if key not in seen:
                    seen.add(key)
                    seen_starts.add((slug, chapter, verse_start))
                    references.append({"book": book, "slug": slug, "chapter": chapter, "start": verse_start, "end": verse_end, "position": book_match.start(), "context": context})

            for verse_match in re.finditer(VERSE_MARKER_RE, window):
                before_verse = window[: verse_match.start()]
                chapter_markers = list(re.finditer(CHAPTER_MARKER_RE, before_verse))
                if not chapter_markers:
                    continue
                chapter_marker = chapter_markers[-1]
                chapter, _, chapter_score = parse_spoken_number(before_verse[chapter_marker.end() :])
                verse_fragment = window[verse_match.end() :]
                verse_start, consumed, verse_score = parse_spoken_number(verse_fragment, max_skip=0)
                if not chapter or not verse_start or chapter_score < 0.64 or verse_score < 0.64:
                    continue

                verse_end = verse_start
                verse_tokens = re.findall(r"[a-z0-9]+", normalize_romanian(verse_fragment))
                remaining = verse_tokens[consumed:]
                if remaining and remaining[0] in {"si", "su", "pana"}:
                    possible_end, _, end_score = parse_spoken_number(" ".join(remaining[1:]), max_skip=0)
                    if possible_end and end_score >= 0.64 and verse_start <= possible_end <= verse_start + 10:
                        verse_end = possible_end

                key = (slug, chapter, verse_start, verse_end)
                if key in seen or chapter > 150 or verse_start > 176:
                    continue
                seen.add(key)
                seen_starts.add((slug, chapter, verse_start))
                references.append({"book": book, "slug": slug, "chapter": chapter, "start": verse_start, "end": verse_end, "position": book_match.start(), "context": context})

            for chapter_match in re.finditer(CHAPTER_MARKER_RE, window):
                fragment = window[chapter_match.end() :]
                chapter, consumed, chapter_score = parse_spoken_number(fragment)
                if not chapter or chapter_score < 0.64:
                    continue
                tokens = re.findall(r"[a-z0-9]+", normalize_romanian(fragment))
                remaining = tokens[consumed:]
                if not remaining or remaining[0] not in {"cu", "verset", "versetul"}:
                    continue
                verse_start, _, verse_score = parse_spoken_number(" ".join(remaining[1:]), max_skip=0)
                if not verse_start or verse_score < 0.64:
                    continue
                key = (slug, chapter, verse_start, verse_start)
                if (slug, chapter, verse_start) in seen_starts or chapter > 150 or verse_start > 176:
                    continue
                seen.add(key)
                seen_starts.add((slug, chapter, verse_start))
                references.append({"book": book, "slug": slug, "chapter": chapter, "start": verse_start, "end": verse_start, "position": book_match.start(), "context": context})

            chapter, consumed, chapter_score = parse_spoken_number(window)
            tokens = re.findall(r"[a-z0-9]+", normalize_romanian(window))
            remaining = tokens[consumed:]
            if chapter and chapter_score >= 0.72 and remaining and remaining[0] in {"cu", "verset", "versetul"}:
                verse_start, _, verse_score = parse_spoken_number(" ".join(remaining[1:]), max_skip=0)
                key = (slug, chapter, verse_start or 0, verse_start or 0)
                if (
                    verse_start
                    and verse_score >= 0.64
                    and (slug, chapter, verse_start) not in seen_starts
                    and chapter <= 150
                    and verse_start <= 176
                ):
                    seen.add(key)
                    seen_starts.add((slug, chapter, verse_start))
                    references.append({"book": book, "slug": slug, "chapter": chapter, "start": verse_start, "end": verse_start, "position": book_match.start(), "context": context})

    references.sort(key=lambda item: item["position"])
    for reference in references:
        reference.pop("position", None)
    return references[:12]


@lru_cache(maxsize=256)
def fetch_verse_text(slug: str, chapter: int, verse: int) -> str:
    response = requests.get(
        f"{RESURSE_BIBLE_URL}/{slug}/{chapter}/{verse}",
        headers={"User-Agent": "RomanianRealtimeASR/1.0"},
        timeout=12,
    )
    response.raise_for_status()
    match = re.search(
        r'<meta\s+name="description"\s+content="(.*?)"\s*/?>',
        response.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def verse_context_score(verse_text: str, context: str) -> float:
    verse_words = {word for word in normalize_romanian(verse_text).split() if len(word) >= 4}
    context_words = {word for word in normalize_romanian(context).split() if len(word) >= 4}
    if not verse_words or not context_words:
        return 0.0
    matched = 0
    for verse_word in verse_words:
        if max(difflib.SequenceMatcher(None, verse_word, word).ratio() for word in context_words) >= 0.72:
            matched += 1
    return matched / len(verse_words)


def load_reference_text(slug: str, chapter: int, verse_start: int, verse_end: int) -> str:
    verse_texts: list[str] = []
    for verse in range(verse_start, min(verse_end, verse_start + 2) + 1):
        try:
            verse_text = fetch_verse_text(slug, chapter, verse)
        except requests.RequestException:
            verse_text = ""
        if verse_text:
            verse_texts.append(verse_text)
    return " ".join(verse_texts)


def reference_matches(text: str) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for reference in extract_bible_references(text):
        chapter = reference["chapter"]
        verse_start = reference["start"]
        verse_end = reference["end"]
        verse_text = load_reference_text(reference["slug"], chapter, verse_start, verse_end)
        current_score = verse_context_score(verse_text, reference.get("context", ""))

        if current_score < 0.38:
            passage_length = verse_end - verse_start
            alternatives = [
                (chapter - 1, verse_start),
                (chapter + 1, verse_start),
                (chapter, verse_start - 1),
                (chapter, verse_start + 1),
            ]
            for candidate_chapter, candidate_start in alternatives:
                if candidate_chapter < 1 or candidate_start < 1:
                    continue
                candidate_end = candidate_start + passage_length
                candidate_text = load_reference_text(reference["slug"], candidate_chapter, candidate_start, candidate_end)
                candidate_score = verse_context_score(candidate_text, reference.get("context", ""))
                if candidate_score >= 0.38 and candidate_score > current_score + 0.12:
                    chapter = candidate_chapter
                    verse_start = candidate_start
                    verse_end = candidate_end
                    verse_text = candidate_text
                    current_score = candidate_score

        verse_label = str(verse_start) if verse_end == verse_start else f"{verse_start}-{verse_end}"
        matches.append(
            {
                "reference": f"{reference['book']} {chapter}:{verse_label}",
                "text": verse_text or "Deschide pasajul pe Biblia Resurse Creștine.",
                "url": f"{RESURSE_BIBLE_URL}/{reference['slug']}/{chapter}/{verse_start}",
                "query": "referință biblică rostită",
            }
        )
    return matches


async def transcribe_long_audio(audio: np.ndarray) -> dict[str, Any]:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    duration = len(audio) / MODEL_SAMPLE_RATE
    if duration < 0.2:
        raise ValueError("Eșantionul audio este prea scurt.")
    if duration > MAX_AUDIO_SECONDS:
        raise ValueError("Eșantionul audio depășește limita de 2 ore.")

    chunk_samples = 10 * MODEL_SAMPLE_RATE
    overlap_samples = 1 * MODEL_SAMPLE_RATE
    step_samples = chunk_samples - overlap_samples
    transcript = ""
    inference_seconds = 0.0
    processed_chunks = 0

    for start in range(0, len(audio), step_samples):
        chunk = audio[start : start + chunk_samples]
        if len(chunk) < round(0.25 * MODEL_SAMPLE_RATE):
            break
        if rms(chunk) < 0.0005:
            continue
        result = await infer_audio(chunk)
        transcript = merge_transcript(transcript, result.text)
        inference_seconds += result.inference_seconds
        processed_chunks += 1

    return {
        "text": transcript.strip(),
        "audio_seconds": round(duration, 2),
        "inference_ms": round(inference_seconds * 1000),
        "rtf": round(inference_seconds / max(duration, 1e-6), 3),
        "chunks": processed_chunks,
    }


def bible_query_candidates(text: str) -> list[str]:
    words = [word.strip(".,!?;:()[]{}\"„”’'") for word in text.split()]
    words = [word for word in words if len(word) > 1]
    if not words:
        return []
    candidates: list[str] = []
    for size in (12, 8, 6):
        if len(words) <= size:
            candidate = " ".join(words)
            if candidate not in candidates:
                candidates.append(candidate)
            continue
        positions = (0, max(0, (len(words) - size) // 2), len(words) - size)
        for position in positions:
            candidate = " ".join(words[position : position + size])
            if candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) >= 6:
                return candidates
    return candidates[:6]


def search_resurse_crestine(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for query in bible_query_candidates(text):
        response = requests.get(
            RESURSE_SEARCH_URL,
            params={
                "search_text": query,
                "search_in": "16",
                "search_by": "filtru-titlu",
                "output": "json2",
            },
            headers={"User-Agent": "RomanianRealtimeASR/1.0"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("Results", []), list):
            continue
        for item in payload.get("Results", []):
            if not isinstance(item, dict):
                continue
            book = str(item.get("id", "")).strip()
            chapter = str(item.get("author", "")).strip()
            verse = str(item.get("slug", "")).strip()
            key = (book, chapter, verse)
            if not all(key) or key in seen:
                continue
            seen.add(key)
            book_slug = str(item.get("title_slug", "")).strip()
            found.append(
                {
                    "reference": f"{book} {chapter}:{verse}",
                    "text": str(item.get("title", "")).strip(),
                    "url": f"https://biblia.resursecrestine.ro/{book_slug}/{chapter}/{verse}",
                    "query": query,
                }
            )
            if len(found) >= 5:
                return found
        if found:
            break
    return found


async def find_bible_matches(text: str) -> list[dict[str, str]]:
    if len(text.strip()) < 12:
        return []
    try:
        direct_matches = await asyncio.to_thread(reference_matches, text)
        if direct_matches:
            return direct_matches[:8]
        text_matches = await asyncio.to_thread(search_resurse_crestine, text)
        seen = {match["url"] for match in direct_matches}
        return (direct_matches + [match for match in text_matches if match["url"] not in seen])[:8]
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return []


def notify_ppt_finder_sync(match: dict[str, str]) -> bool:
    global LAST_NOTIFIED_VERSE
    reference = str(match.get("reference") or "").strip()
    if not reference:
        return False
    now = time.time()
    if (
        LAST_NOTIFIED_VERSE["reference"] == reference
        and now - LAST_NOTIFIED_VERSE["timestamp"] < VERSE_NOTIFY_COOLDOWN
    ):
        return False

    payload = {
        "reference": reference,
        "text": str(match.get("text") or "").strip(),
        "url": str(match.get("url") or "").strip(),
        "timestamp": now,
        "source": "voice",
    }
    LAST_NOTIFIED_VERSE = {"reference": reference, "timestamp": now}
    try:
        response = requests.post(
            f"{PPT_FINDER_URL}/api/voice/verse-detected",
            json=payload,
            timeout=2,
        )
        if response.status_code == 200:
            return True
        logger.warning("PPT Finder rejected verse notification: HTTP %s", response.status_code)
    except requests.RequestException as error:
        logger.warning("Could not notify PPT Finder: %s", error)
    LAST_NOTIFIED_VERSE = {"reference": reference, "timestamp": time.time() - max(VERSE_NOTIFY_COOLDOWN - 5, 0)}
    return False


async def notify_ppt_finder(match: dict[str, str]) -> bool:
    return await asyncio.to_thread(notify_ppt_finder_sync, match)


def schedule_ppt_finder_notification(matches: list[dict[str, str]]) -> None:
    if matches:
        asyncio.create_task(notify_ppt_finder(matches[0]))


def validate_youtube_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    allowed = host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
    if parsed.scheme not in {"http", "https"} or not allowed:
        raise ValueError("Introdu un link valid de pe youtube.com sau youtu.be.")
    return value.strip()


def download_youtube_audio(url: str, directory: Path) -> tuple[Path, str]:
    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "max_filesize": MAX_UPLOAD_BYTES,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        duration = float(info.get("duration") or 0)
        if duration <= 0:
            raise ValueError("Durata eșantionului YouTube nu a putut fi determinată.")
        if duration > MAX_AUDIO_SECONDS:
            raise ValueError("Eșantionul YouTube depășește limita de 2 ore.")
        info = downloader.extract_info(url, download=True)
        title = str(info.get("title") or "Eșantion YouTube")
    files = [path for path in directory.iterdir() if path.is_file()]
    if not files:
        raise ValueError("Sunetul YouTube nu a putut fi descărcat.")
    return max(files, key=lambda path: path.stat().st_size), title


@app.post("/api/bible-search")
async def bible_search(payload: TextPayload) -> JSONResponse:
    matches = await find_bible_matches(payload.text)
    schedule_ppt_finder_notification(matches)
    return JSONResponse({"matches": matches})


@app.post("/api/transcribe-configured", dependencies=[Depends(reserve_audio_source)])
async def transcribe_configured_sample() -> JSONResponse:
    if not CONFIGURED_SAMPLE_PATH or not CONFIGURED_SAMPLE_PATH.is_file():
        raise HTTPException(404, "Nu este configurat niciun eșantion local.")
    try:
        audio = await asyncio.to_thread(decode_audio_file, CONFIGURED_SAMPLE_PATH)
        result = await transcribe_long_audio(audio)
        result["matches"] = await find_bible_matches(result["text"])
        schedule_ppt_finder_notification(result["matches"])
        result["source"] = CONFIGURED_SAMPLE_PATH.name
        app.state.last_sample_result = result
        return JSONResponse(result)
    except (ValueError, av.error.FFmpegError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/transcribe-upload", dependencies=[Depends(reserve_audio_source)])
async def transcribe_upload(file: UploadFile = File(...)) -> JSONResponse:
    with tempfile.TemporaryDirectory(prefix="ro-asr-upload-") as directory_name:
        suffix = Path(file.filename or "audio.bin").suffix or ".bin"
        path = Path(directory_name) / f"source{suffix}"
        written = 0
        with path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Fișierul depășește limita de 250 MB.")
                destination.write(chunk)
        try:
            audio = await asyncio.to_thread(decode_audio_file, path)
            result = await transcribe_long_audio(audio)
            result["matches"] = await find_bible_matches(result["text"])
            schedule_ppt_finder_notification(result["matches"])
            result["source"] = file.filename or "Fișier audio"
            app.state.last_sample_result = result
            return JSONResponse(result)
        except (ValueError, av.error.FFmpegError) as error:
            raise HTTPException(400, str(error)) from error


@app.post("/api/transcribe-youtube", dependencies=[Depends(reserve_audio_source)])
async def transcribe_youtube(payload: YoutubePayload) -> JSONResponse:
    try:
        url = validate_youtube_url(payload.url)
        with tempfile.TemporaryDirectory(prefix="ro-asr-youtube-") as directory_name:
            path, title = await asyncio.to_thread(
                download_youtube_audio,
                url,
                Path(directory_name),
            )
            audio = await asyncio.to_thread(decode_audio_file, path)
            result = await transcribe_long_audio(audio)
            result["matches"] = await find_bible_matches(result["text"])
            schedule_ppt_finder_notification(result["matches"])
            result["source"] = title
            app.state.last_sample_result = result
            return JSONResponse(result)
    except (ValueError, yt_dlp.utils.DownloadError, av.error.FFmpegError) as error:
        raise HTTPException(400, str(error)) from error


class BrowserAudioSession:
    def __init__(
        self,
        websocket: WebSocket,
        capture_rate: int,
        max_segment_seconds: float,
        silence_seconds: float,
        voice_threshold: float,
    ) -> None:
        if capture_rate < 8_000 or capture_rate > 192_000:
            raise ValueError("Unsupported browser sample rate")
        self.websocket = websocket
        self.capture_rate = capture_rate
        self.audio_clock = 0.0
        self.threshold_floor = voice_threshold
        self.threshold = self.threshold_floor
        self.calibration_seconds = 1.2
        self.calibration_audio: list[np.ndarray] = []
        self.calibration_samples = 0
        self.calibrated = False

        self.pre_roll: deque[np.ndarray] = deque()
        self.pre_roll_samples = 0
        self.pre_roll_target = round(0.25 * capture_rate)
        self.utterance: list[np.ndarray] = []
        self.utterance_samples = 0
        self.speech_active = False
        self.last_voice_clock = 0.0
        self.last_decode_samples = 0

        self.update_seconds = 0.8
        self.window_seconds = 6.0
        self.silence_seconds = silence_seconds
        self.min_speech_seconds = 0.35
        self.max_utterance_seconds = max_segment_seconds

    async def emit(self, event_type: str, **payload: Any) -> None:
        await self.websocket.send_json({"type": event_type, **payload})

    def to_model_rate(self, audio: np.ndarray) -> np.ndarray:
        return resample_to_model_rate(audio, self.capture_rate)

    async def infer(self, audio: np.ndarray) -> Transcript:
        model_audio = self.to_model_rate(audio)
        return await infer_audio(model_audio)

    async def calibrate(self, chunk: np.ndarray) -> None:
        self.calibration_audio.append(chunk)
        self.calibration_samples += len(chunk)
        progress = min(
            1.0,
            self.calibration_samples / (self.calibration_seconds * self.capture_rate),
        )
        if progress < 1.0:
            return

        audio = np.concatenate(self.calibration_audio)
        frame_size = max(1, round(self.capture_rate * 0.05))
        levels = [rms(audio[i : i + frame_size]) for i in range(0, len(audio), frame_size)]
        noise = float(np.percentile(levels, 75)) if levels else 0.0
        self.threshold = max(self.threshold_floor, noise * 3.0)
        self.calibration_audio.clear()
        self.calibrated = True
        await self.emit(
            "ready",
            threshold=round(self.threshold, 5),
            noise=round(noise, 5),
        )

    def update_pre_roll(self, chunk: np.ndarray) -> None:
        self.pre_roll.append(chunk)
        self.pre_roll_samples += len(chunk)
        while (
            self.pre_roll
            and self.pre_roll_samples - len(self.pre_roll[0]) >= self.pre_roll_target
        ):
            self.pre_roll_samples -= len(self.pre_roll.popleft())

    async def handle_audio(self, chunk: np.ndarray) -> None:
        if chunk.size == 0:
            return
        self.audio_clock += len(chunk) / self.capture_rate

        if not self.calibrated:
            await self.calibrate(chunk)
            return

        level = rms(chunk)
        voiced = level >= self.threshold
        self.update_pre_roll(chunk)

        if voiced and not self.speech_active:
            self.speech_active = True
            self.utterance = list(self.pre_roll)
            self.utterance_samples = sum(len(part) for part in self.utterance)
            self.last_voice_clock = self.audio_clock
            self.last_decode_samples = 0
            await self.emit("speech_start")
        elif self.speech_active:
            self.utterance.append(chunk)
            self.utterance_samples += len(chunk)
            if voiced:
                self.last_voice_clock = self.audio_clock

        if not self.speech_active:
            return

        utterance_seconds = self.utterance_samples / self.capture_rate
        update_due = (
            self.utterance_samples - self.last_decode_samples
            >= self.update_seconds * self.capture_rate
        )
        if update_due and utterance_seconds >= self.min_speech_seconds:
            audio = np.concatenate(self.utterance)
            window_samples = round(self.window_seconds * self.capture_rate)
            result = await self.infer(audio[-window_samples:])
            await self.emit_result("partial", result)
            self.last_decode_samples = self.utterance_samples

        trailing_silence = self.audio_clock - self.last_voice_clock
        if (
            trailing_silence >= self.silence_seconds
            or utterance_seconds >= self.max_utterance_seconds
        ):
            await self.finalize()

    async def emit_result(self, event_type: str, result: Transcript) -> None:
        await self.emit(
            event_type,
            text=result.text,
            inference_ms=round(result.inference_seconds * 1000),
            audio_seconds=round(result.audio_seconds, 2),
            rtf=round(result.real_time_factor, 3),
        )

    async def finalize(self) -> None:
        if not self.speech_active or not self.utterance:
            return
        duration = self.utterance_samples / self.capture_rate
        if duration >= self.min_speech_seconds:
            result = await self.infer(np.concatenate(self.utterance))
            await self.emit_result("final", result)
        self.utterance.clear()
        self.utterance_samples = 0
        self.speech_active = False
        self.last_decode_samples = 0
        self.pre_roll.clear()
        self.pre_roll_samples = 0


def decode_pcm_packet(data: bytes, capture_rate: int) -> np.ndarray:
    if len(data) % 4 or len(data) > capture_rate * 4 * 2:
        raise ValueError("Pachet audio invalid sau prea mare.")
    audio = np.frombuffer(data, dtype="<f4").copy()
    if not np.isfinite(audio).all():
        raise ValueError("Pachetul audio conține valori invalide.")
    return audio


async def reserve_socket_source() -> None:
    require_ready()
    if app.state.source_lock.locked():
        raise HTTPException(409, "O altă sesiune vocală este activă. Oprește-o înainte de a continua.")
    await app.state.source_lock.acquire()


async def close_socket(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        pass


@app.websocket("/ws/transcribe")
async def transcribe_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    session: BrowserAudioSession | None = None
    reserved = False
    try:
        await reserve_socket_source()
        reserved = True
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                command = json.loads(text)
                if not isinstance(command, dict):
                    raise ValueError("Comandă vocală invalidă.")
                if command.get("type") == "start":
                    if session is not None:
                        raise ValueError("Sesiunea vocală este deja pornită.")
                    saved_settings = getattr(app.state, "voice_settings", read_voice_settings())
                    session_settings = normalize_voice_settings(
                        {
                            **saved_settings,
                            "max_segment_seconds": command.get("max_segment_seconds", saved_settings["max_segment_seconds"]),
                            "silence_seconds": command.get("silence_seconds", saved_settings["silence_seconds"]),
                            "voice_threshold": command.get("voice_threshold", saved_settings["voice_threshold"]),
                        }
                    )
                    session = BrowserAudioSession(
                        websocket,
                        capture_rate=int(command["sample_rate"]),
                        max_segment_seconds=session_settings["max_segment_seconds"],
                        silence_seconds=session_settings["silence_seconds"],
                        voice_threshold=session_settings["voice_threshold"],
                    )
                    await session.emit(
                        "calibrating",
                        seconds=session.calibration_seconds,
                    )
                elif command.get("type") == "stop" and session is not None:
                    await session.finalize()
                    await session.emit("stopped")
                    break
                continue

            data = message.get("bytes")
            if data is not None and session is not None:
                audio = decode_pcm_packet(data, session.capture_rate)
                await session.handle_audio(audio)
    except WebSocketDisconnect:
        pass
    except Exception as error:
        logger.warning("Microphone session stopped: %s", error)
        try:
            await websocket.send_json({"type": "error", "message": error.detail if isinstance(error, HTTPException) else str(error)})
        except Exception:
            pass
    finally:
        if reserved:
            app.state.source_lock.release()
        await close_socket(websocket)


class PlaybackChunkSession:
    def __init__(
        self,
        websocket: WebSocket,
        capture_rate: int,
        max_segment_seconds: float,
        silence_seconds: float,
        voice_threshold: float,
        offset_seconds: float,
    ) -> None:
        if capture_rate < 8_000 or capture_rate > 192_000:
            raise ValueError("Unsupported browser sample rate")
        if not math.isfinite(max_segment_seconds) or max_segment_seconds < 2 or max_segment_seconds > 30:
            raise ValueError("Unsupported maximum playback segment duration")
        if not math.isfinite(silence_seconds) or silence_seconds < 0.10 or silence_seconds > 2.0:
            raise ValueError("Unsupported playback silence duration")
        if not math.isfinite(voice_threshold) or voice_threshold < 0.0001 or voice_threshold > 0.1:
            raise ValueError("Unsupported playback voice threshold")
        if not math.isfinite(offset_seconds) or offset_seconds < 0:
            raise ValueError("Unsupported playback offset")
        self.websocket = websocket
        self.capture_rate = capture_rate
        self.max_segment_seconds = max_segment_seconds
        self.max_segment_samples = round(capture_rate * max_segment_seconds)
        self.silence_seconds = silence_seconds
        self.voice_threshold = voice_threshold
        self.offset_seconds = max(0.0, offset_seconds)
        self.parts: list[np.ndarray] = []
        self.buffered_samples = 0
        self.received_samples = 0
        self.segment_start_sample = 0
        self.vad_buffer = np.empty(0, dtype=np.float32)
        self.pre_roll: deque[np.ndarray] = deque()
        self.pre_roll_samples = 0
        self.frame_samples = max(1, round(capture_rate * 0.02))
        self.pre_roll_target = round(capture_rate * 0.10)
        self.silence_target = round(capture_rate * silence_seconds)
        self.level_history: deque[float] = deque(maxlen=150)
        self.silence_samples = 0
        self.speech_started = False
        self.partial_update_samples = round(capture_rate * 0.8)
        self.last_partial_samples = 0
        self.partial_in_flight = False
        self.partial_task: asyncio.Task | None = None
        self.sequence = 0

    async def emit(self, event_type: str, **payload: Any) -> None:
        await self.websocket.send_json({"type": event_type, **payload})

    async def transcribe_chunk(self, audio: np.ndarray, start_sample: int) -> None:
        start_seconds = self.offset_seconds + start_sample / self.capture_rate
        end_seconds = start_seconds + len(audio) / self.capture_rate
        self.sequence += 1

        if rms(audio) < 0.0005:
            await self.emit(
                "chunk",
                sequence=self.sequence,
                text="",
                start_seconds=round(start_seconds, 2),
                end_seconds=round(end_seconds, 2),
                inference_ms=0,
                audio_seconds=round(len(audio) / self.capture_rate, 2),
                rtf=0.0,
            )
            return

        model_audio = resample_to_model_rate(audio, self.capture_rate)
        result = await infer_audio(model_audio)
        await self.emit(
            "chunk",
            sequence=self.sequence,
            text=result.text,
            start_seconds=round(start_seconds, 2),
            end_seconds=round(end_seconds, 2),
            inference_ms=round(result.inference_seconds * 1000),
            audio_seconds=round(result.audio_seconds, 2),
            rtf=round(result.real_time_factor, 3),
        )

    def reset_segment(self) -> None:
        self.parts.clear()
        self.buffered_samples = 0
        self.silence_samples = 0
        self.speech_started = False
        self.last_partial_samples = 0
        self.pre_roll.clear()
        self.pre_roll_samples = 0

    async def transcribe_partial(self, audio: np.ndarray, start_sample: int) -> None:
        try:
            model_audio = resample_to_model_rate(audio, self.capture_rate)
            result = await infer_audio(model_audio)
            await self.emit(
                "partial",
                text=result.text,
                start_seconds=round(self.offset_seconds + start_sample / self.capture_rate, 2),
                audio_seconds=round(len(audio) / self.capture_rate, 2),
                inference_ms=round(result.inference_seconds * 1000),
                rtf=round(result.real_time_factor, 3),
            )
        except Exception:
            # A partial hypothesis is best-effort; the final segment remains authoritative.
            pass
        finally:
            self.partial_in_flight = False

    def schedule_partial(self) -> None:
        if self.partial_in_flight or self.buffered_samples - self.last_partial_samples < self.partial_update_samples:
            return
        audio = np.concatenate(self.parts).astype(np.float32, copy=True)
        window_samples = min(len(audio), round(self.capture_rate * 5.0))
        self.partial_in_flight = True
        self.last_partial_samples = self.buffered_samples
        self.partial_task = asyncio.create_task(self.transcribe_partial(audio[-window_samples:], self.segment_start_sample))

    async def finish_segment(self) -> None:
        if self.partial_task is not None:
            self.partial_task.cancel()
            await asyncio.gather(self.partial_task, return_exceptions=True)
            self.partial_task = None
        if not self.speech_started or self.buffered_samples < round(0.25 * self.capture_rate):
            self.reset_segment()
            return
        audio = np.concatenate(self.parts).astype(np.float32, copy=False)
        start_sample = self.segment_start_sample
        self.reset_segment()
        await self.transcribe_chunk(audio, start_sample)

    async def handle_frame(self, frame: np.ndarray) -> None:
        frame_start = self.received_samples
        self.received_samples += len(frame)
        level = rms(frame)
        self.level_history.append(level)
        noise_floor = float(np.percentile(self.level_history, 20)) if self.level_history else 0.0
        voiced = level >= max(self.voice_threshold, noise_floor * 2.2)

        if not self.speech_started:
            if not voiced:
                self.pre_roll.append(frame.copy())
                self.pre_roll_samples += len(frame)
                while self.pre_roll_samples > self.pre_roll_target and self.pre_roll:
                    self.pre_roll_samples -= len(self.pre_roll.popleft())
                return
            self.speech_started = True
            self.segment_start_sample = max(0, frame_start - self.pre_roll_samples)
            self.parts = list(self.pre_roll)
            self.buffered_samples = self.pre_roll_samples
            self.pre_roll.clear()
            self.pre_roll_samples = 0
            self.silence_samples = 0

        self.parts.append(frame.copy())
        self.buffered_samples += len(frame)
        if voiced:
            self.silence_samples = 0
        else:
            self.silence_samples += len(frame)

        self.schedule_partial()

        if self.silence_samples >= self.silence_target or self.buffered_samples >= self.max_segment_samples:
            await self.finish_segment()

    async def handle_audio(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        if self.vad_buffer.size:
            audio = np.concatenate((self.vad_buffer, audio))
        full_samples = (len(audio) // self.frame_samples) * self.frame_samples
        for start in range(0, full_samples, self.frame_samples):
            await self.handle_frame(audio[start : start + self.frame_samples])
        self.vad_buffer = audio[full_samples:].copy()

    async def finalize(self) -> None:
        if self.vad_buffer.size:
            await self.handle_frame(self.vad_buffer)
            self.vad_buffer = np.empty(0, dtype=np.float32)
        await self.finish_segment()


@app.websocket("/ws/playback-transcribe")
async def playback_transcribe_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    session: PlaybackChunkSession | None = None
    reserved = False
    try:
        await reserve_socket_source()
        reserved = True
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                command = json.loads(text)
                if not isinstance(command, dict):
                    raise ValueError("Comandă vocală invalidă.")
                if command.get("type") == "start":
                    if session is not None:
                        raise ValueError("Sesiunea vocală este deja pornită.")
                    session = PlaybackChunkSession(
                        websocket,
                        capture_rate=int(command["sample_rate"]),
                        max_segment_seconds=float(
                            command.get("max_segment_seconds", command.get("chunk_seconds", 10))
                        ),
                        silence_seconds=float(command.get("silence_seconds", 0.30)),
                        voice_threshold=float(command.get("voice_threshold", 0.005)),
                        offset_seconds=float(command.get("offset_seconds", 0)),
                    )
                    await session.emit(
                        "ready",
                        segmentation="silence",
                        max_segment_seconds=session.max_segment_seconds,
                        silence_seconds=session.silence_seconds,
                        voice_threshold=session.voice_threshold,
                    )
                elif command.get("type") == "stop" and session is not None:
                    await session.finalize()
                    await session.emit("stopped")
                    break
                continue

            data = message.get("bytes")
            if data is not None and session is not None:
                await session.handle_audio(decode_pcm_packet(data, session.capture_rate))
    except WebSocketDisconnect:
        pass
    except Exception as error:
        logger.warning("Playback session stopped: %s", error)
        try:
            await websocket.send_json({"type": "error", "message": error.detail if isinstance(error, HTTPException) else str(error)})
        except Exception:
            pass
    finally:
        if session is not None and session.partial_task is not None:
            session.partial_task.cancel()
            await asyncio.gather(session.partial_task, return_exceptions=True)
        if reserved:
            app.state.source_lock.release()
        await close_socket(websocket)


app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
