"""Frozen entry point for the independent Romanian voice web application."""

from __future__ import annotations

import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import webbrowser


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if executable_dir.name == "Voice_Recognition":
            return executable_dir.parent
        return executable_dir
    return Path(__file__).resolve().parent.parent


PORTABLE_ROOT = portable_root()
os.environ.setdefault("VOICE_PORTABLE_ROOT", str(PORTABLE_ROOT))
os.environ.setdefault("HF_HOME", str(PORTABLE_ROOT / "model-cache"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def configured_ui_language() -> str:
    config_path = PORTABLE_ROOT / "config.txt"
    try:
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = raw_line.strip().partition("=")
            if separator and key.strip().lower() == "language":
                language = value.strip().lower()
                return language if language in {"ro", "en"} else "ro"
    except OSError:
        pass
    return "ro"


os.environ.setdefault("APP_UI_LANGUAGE", configured_ui_language())


def open_when_listening(url: str) -> None:
    for _ in range(180):
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
                payload = json.load(response)
            if payload.get("service") == "romanian-realtime-asr":
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.5)


def main() -> int:
    multiprocessing.freeze_support()
    log_dir = PORTABLE_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "voice-recognition.log", maxBytes=2_000_000,
        backupCount=3, encoding="utf-8",
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    url = "http://127.0.0.1:8765"
    threading.Thread(target=open_when_listening, args=(url,), daemon=True).start()
    try:
        import uvicorn
        from web_server import app

        uvicorn.run(
            app, host="127.0.0.1", port=8765,
            ws_max_size=2_097_152, ws_max_queue=16,
            log_config=None, access_log=False,
        )
        return 0
    except Exception as error:
        logging.exception("Voice Recognition could not start")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(
                None,
                f"Voice Recognition could not start.\n\n{error}\n\nSee logs\\voice-recognition.log",
                "Voice Recognition",
                0x10,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
