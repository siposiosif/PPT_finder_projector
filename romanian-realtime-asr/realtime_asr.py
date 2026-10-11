"""Low-latency Romanian microphone transcription on CUDA."""

from __future__ import annotations

import argparse
import math
import queue
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import sounddevice as sd
import torch
from scipy.signal import resample_poly
from transformers import (
    AutoConfig,
    AutoFeatureExtractor,
    AutoModel,
    AutoModelForCTC,
    AutoModelForSpeechSeq2Seq,
    AutoProcessor,
    AutoTokenizer,
    Wav2Vec2Processor,
)


MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"
MODEL_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class Transcript:
    text: str
    inference_seconds: float
    audio_seconds: float

    @property
    def real_time_factor(self) -> float:
        return self.inference_seconds / max(self.audio_seconds, 1e-6)


class GpuTranscriber:
    def __init__(self, model_id: str, fp16: bool = True) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available in this Python environment. Run setup.ps1 "
                "to install the CUDA build of PyTorch."
            )

        self.device = torch.device("cuda:0")
        self.dtype = torch.float16 if fp16 else torch.float32

        print(f"Loading processor: {model_id}")
        self.config = AutoConfig.from_pretrained(model_id)
        try:
            self.processor = AutoProcessor.from_pretrained(model_id)
        except ImportError as error:
            if "pyctcdecode" not in str(error):
                raise
            # The old checkpoint advertises an optional LM processor. Use its
            # feature extractor + tokenizer for dependency-free greedy CTC.
            self.processor = Wav2Vec2Processor(
                feature_extractor=AutoFeatureExtractor.from_pretrained(model_id),
                tokenizer=AutoTokenizer.from_pretrained(model_id),
            )
        self.is_whisper = self.config.model_type == "whisper"
        self.is_parakeet = self.config.model_type == "parakeet_tdt"
        self.forced_decoder_ids = None
        if self.is_whisper:
            # This fine-tune ships an older Whisper generation config. Supplying
            # language/task directly to generate() makes recent Transformers
            # reject it, while explicit prompt IDs remain backwards compatible.
            self.forced_decoder_ids = self.processor.get_decoder_prompt_ids(
                language="romanian", task="transcribe"
            )
        print(f"Loading model on {torch.cuda.get_device_name(0)} ({self.dtype})")
        model_class = (
            AutoModel
            if self.is_parakeet
            else AutoModelForSpeechSeq2Seq
            if self.is_whisper
            else AutoModelForCTC
        )
        self.model = model_class.from_pretrained(model_id, dtype=self.dtype).to(self.device)
        if self.is_whisper:
            self.model.generation_config.forced_decoder_ids = self.forced_decoder_ids
        self.model.eval()

        torch.backends.cudnn.benchmark = True
        torch.set_grad_enabled(False)

    def transcribe(self, audio: np.ndarray) -> Transcript:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio_seconds = len(audio) / MODEL_SAMPLE_RATE
        inputs = self.processor(audio, sampling_rate=MODEL_SAMPLE_RATE, return_tensors="pt")

        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            if self.is_whisper:
                input_features = inputs.input_features.to(self.device, dtype=self.dtype)
                token_ids = self.model.generate(
                    input_features,
                    max_new_tokens=min(192, max(32, math.ceil(audio_seconds * 12))),
                )
            elif self.is_parakeet:
                input_features = inputs.input_features.to(self.device, dtype=self.dtype)
                attention_mask = inputs.get("attention_mask")
                if attention_mask is not None:
                    attention_mask = attention_mask.to(self.device)
                generated = self.model.generate(input_features, attention_mask=attention_mask)
                token_ids = generated.sequences
            else:
                input_values = inputs.input_values.to(self.device, dtype=self.dtype)
                logits = self.model(input_values).logits
                token_ids = torch.argmax(logits, dim=-1)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started

        text = self.processor.batch_decode(token_ids, skip_special_tokens=True)[0].strip()
        return Transcript(text=text, inference_seconds=elapsed, audio_seconds=audio_seconds)

    def warm_up(self) -> Transcript:
        return self.transcribe(np.zeros(MODEL_SAMPLE_RATE, dtype=np.float32))


def parse_device(value: str | None) -> int | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def list_input_devices() -> None:
    default_input = sd.default.device[0]
    print("Input devices (* = default):")
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) <= 0:
            continue
        marker = "*" if index == default_input else " "
        rate = int(round(float(device["default_samplerate"])))
        print(f"{marker} {index:>2}: {device['name']}  ({rate} Hz)")


def choose_capture_rate(device: int | str | None, requested_rate: int) -> int:
    if requested_rate > 0:
        return requested_rate
    info = sd.query_devices(device, "input")
    return int(round(float(info["default_samplerate"])))


def resample_to_model_rate(audio: np.ndarray, capture_rate: int) -> np.ndarray:
    if capture_rate == MODEL_SAMPLE_RATE:
        return np.asarray(audio, dtype=np.float32)
    divisor = math.gcd(capture_rate, MODEL_SAMPLE_RATE)
    converted = resample_poly(
        audio,
        up=MODEL_SAMPLE_RATE // divisor,
        down=capture_rate // divisor,
    )
    return np.asarray(converted, dtype=np.float32)


def join_audio(chunks: Iterable[np.ndarray]) -> np.ndarray:
    parts = list(chunks)
    if not parts:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def print_partial(result: Transcript, width: int = 140) -> None:
    prefix = f"[partial RTF {result.real_time_factor:.2f}] "
    line = (prefix + (result.text or "…"))[:width]
    print("\r" + line.ljust(width), end="", flush=True)


def print_final(result: Transcript) -> None:
    print("\r" + (" " * 150) + "\r", end="")
    text = result.text or "(no speech recognized)"
    print(
        f"[final | {result.audio_seconds:.1f}s audio | "
        f"{result.inference_seconds:.2f}s GPU | RTF {result.real_time_factor:.2f}] {text}"
    )


def calibrate_noise(
    audio_queue: queue.Queue[np.ndarray],
    seconds: float,
    block_seconds: float,
    capture_rate: int,
    minimum_threshold: float,
) -> float:
    if seconds <= 0:
        return minimum_threshold
    print(f"Calibrating microphone for {seconds:.1f}s — please stay quiet…")
    levels: list[float] = []
    target_blocks = max(1, math.ceil(seconds / block_seconds))
    for _ in range(target_blocks):
        raw = audio_queue.get(timeout=max(2.0, block_seconds * 4))
        levels.append(rms(resample_to_model_rate(raw, capture_rate)))
    noise = float(np.percentile(levels, 75)) if levels else 0.0
    threshold = max(minimum_threshold, noise * 3.0)
    print(f"Noise RMS {noise:.4f}; speech threshold {threshold:.4f}")
    return threshold


def run_microphone(args: argparse.Namespace, transcriber: GpuTranscriber) -> None:
    device = parse_device(args.device)
    capture_rate = choose_capture_rate(device, args.capture_rate)
    block_frames = max(1, round(capture_rate * args.block_ms / 1000))
    block_seconds = block_frames / capture_rate
    max_queue_blocks = max(100, round(30 / block_seconds))
    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_blocks)
    dropped = [0]

    def callback(indata: np.ndarray, frames: int, timing: object, status: object) -> None:
        del frames, timing
        if status:
            print(f"\nAudio warning: {status}", file=sys.stderr)
        try:
            audio_queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            dropped[0] += 1

    device_info = sd.query_devices(device, "input")
    print(
        f"Microphone: {device_info['name']} | capture {capture_rate} Hz -> "
        f"model {MODEL_SAMPLE_RATE} Hz"
    )

    with sd.InputStream(
        device=device,
        samplerate=capture_rate,
        channels=1,
        dtype="float32",
        blocksize=block_frames,
        latency="low",
        callback=callback,
    ):
        threshold = calibrate_noise(
            audio_queue,
            seconds=args.calibrate_seconds,
            block_seconds=block_seconds,
            capture_rate=capture_rate,
            minimum_threshold=args.threshold,
        )
        print("Listening in Romanian. Speak normally; press Ctrl+C to stop.")

        pre_roll: deque[np.ndarray] = deque()
        pre_roll_samples = 0
        wanted_pre_roll = round(args.pre_roll_seconds * MODEL_SAMPLE_RATE)
        utterance: list[np.ndarray] = []
        utterance_samples = 0
        speech_active = False
        last_voice_time = 0.0
        last_decode_samples = 0

        try:
            while True:
                raw = audio_queue.get(timeout=2.0)
                chunk = resample_to_model_rate(raw, capture_rate)
                now = time.monotonic()
                voiced = rms(chunk) >= threshold

                pre_roll.append(chunk)
                pre_roll_samples += len(chunk)
                while pre_roll and pre_roll_samples - len(pre_roll[0]) >= wanted_pre_roll:
                    pre_roll_samples -= len(pre_roll.popleft())

                if voiced and not speech_active:
                    speech_active = True
                    utterance = list(pre_roll)
                    utterance_samples = sum(len(part) for part in utterance)
                    last_voice_time = now
                    last_decode_samples = 0
                elif speech_active:
                    utterance.append(chunk)
                    utterance_samples += len(chunk)
                    if voiced:
                        last_voice_time = now

                if not speech_active:
                    continue

                utterance_seconds = utterance_samples / MODEL_SAMPLE_RATE
                samples_since_decode = utterance_samples - last_decode_samples
                update_due = samples_since_decode >= args.update_seconds * MODEL_SAMPLE_RATE
                long_enough = utterance_seconds >= args.min_speech_seconds

                if update_due and long_enough:
                    window_samples = round(args.window_seconds * MODEL_SAMPLE_RATE)
                    audio = join_audio(utterance)
                    result = transcriber.transcribe(audio[-window_samples:])
                    print_partial(result)
                    last_decode_samples = utterance_samples

                trailing_silence = now - last_voice_time
                should_finalize = trailing_silence >= args.silence_seconds
                should_split = utterance_seconds >= args.max_utterance_seconds
                if should_finalize or should_split:
                    audio = join_audio(utterance)
                    result = transcriber.transcribe(audio)
                    print_final(result)
                    utterance = []
                    utterance_samples = 0
                    speech_active = False
                    last_decode_samples = 0
                    pre_roll.clear()
                    pre_roll_samples = 0

                if dropped[0]:
                    print(f"\nWarning: dropped {dropped[0]} microphone blocks.", file=sys.stderr)
                    dropped[0] = 0
        except KeyboardInterrupt:
            print("\nStopping…")
            if speech_active and utterance_samples >= args.min_speech_seconds * MODEL_SAMPLE_RATE:
                print_final(transcriber.transcribe(join_audio(utterance)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Real-time Romanian speech recognition on an NVIDIA GPU."
    )
    parser.add_argument("--model", default=MODEL_ID, help="Hugging Face model ID")
    parser.add_argument("--device", help="Input-device index or name substring")
    parser.add_argument(
        "--capture-rate",
        type=int,
        default=0,
        help="Microphone rate; 0 uses the device default and resamples to 16 kHz",
    )
    parser.add_argument("--block-ms", type=int, default=50)
    parser.add_argument("--update-seconds", type=float, default=0.8)
    parser.add_argument("--window-seconds", type=float, default=6.0)
    parser.add_argument("--silence-seconds", type=float, default=0.8)
    parser.add_argument("--pre-roll-seconds", type=float, default=0.25)
    parser.add_argument("--min-speech-seconds", type=float, default=0.35)
    parser.add_argument("--max-utterance-seconds", type=float, default=15.0)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.008,
        help="Minimum RMS speech threshold; calibration may raise it",
    )
    parser.add_argument("--calibrate-seconds", type=float, default=1.5)
    parser.add_argument("--fp32", action="store_true", help="Disable FP16 inference")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_devices:
        list_input_devices()
        return 0

    transcriber = GpuTranscriber(args.model, fp16=not args.fp32)
    warmup = transcriber.warm_up()
    allocated = torch.cuda.memory_allocated() / (1024**2)
    print(
        f"GPU ready: warm-up {warmup.inference_seconds:.2f}s, "
        f"allocated VRAM {allocated:.0f} MiB"
    )
    if args.smoke_test:
        print("CUDA/model smoke test passed.")
        return 0

    run_microphone(args, transcriber)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
