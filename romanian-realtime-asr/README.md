# Real-time Romanian ASR on NVIDIA GPU

This is a low-latency microphone demo for
[`nvidia/parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3).
It captures the default Windows microphone, resamples it to 16 kHz, detects
speech, and runs NVIDIA Parakeet TDT decoding on CUDA in FP16.

The model is not natively streaming. This demo approximates streaming by
re-transcribing the current utterance every 0.8 seconds and finalizing it after
0.8 seconds of silence.

## Setup

Open PowerShell in this folder and run:

```powershell
.\setup.ps1
```

The CUDA PyTorch environment and model cache need several gigabytes of disk
space. The first run downloads about 2.5 GB of model weights.

## Verify the GPU and model

```powershell
.\run.ps1 --smoke-test
```

## Choose a microphone

```powershell
.\run.ps1 --list-devices
```

The default input is marked with `*`. To select another device, pass its index:

```powershell
.\run.ps1 --device 3
```

## Run live transcription

```powershell
.\run.ps1
```

Stay quiet during the short noise calibration, then speak Romanian. Press
`Ctrl+C` to stop.

Useful tuning options:

```powershell
# More responsive partial text
.\run.ps1 --update-seconds 0.5

# A noisy room: raise the minimum voice threshold
.\run.ps1 --threshold 0.02

# If FP16 causes a model/GPU compatibility issue
.\run.ps1 --fp32
```

The displayed real-time factor (RTF) is GPU inference time divided by audio
duration. `RTF < 1.0` keeps up with live audio; `RTF < 0.3` generally feels
responsive.

## Web interface

The web application runs in its own Python process and environment on port
8765. It can remain open beside PPT Finder; neither application sends control
commands to the other. Opening the page does not activate the microphone or
start sample playback. Use `start_romanian_asr.bat` to launch it independently,
or the shared Live Event launcher in the parent folder to open both applications.
The launcher never installs or updates packages during an event.

Start the local web app:

```powershell
.\run_web.ps1
```

Then open <http://127.0.0.1:8765>. Press **Pornește microfonul**, allow browser
microphone access, remain quiet during the brief calibration, and speak
Romanian. Audio is sent only to the local Python server and transcribed on the
NVIDIA GPU.

The interface stays available while the model loads and shows a clear error if
the GPU or model cannot start. It checks connection status automatically and
keeps displayed transcript text after connection failures. Only one microphone,
playback, or file transcription session can use this voice service at a time.
GPU operations use one dedicated worker, including after browser disconnects.

`GET /api/status` returns JSON with `service: "romanian-realtime-asr"`,
`ready` (boolean), `state` (`loading`, `ready`, or `error`), `error` (message
or null), and `busy` (boolean). HTTP availability does not mean the model is
ready: clients must check `ready`. Transcription requests before readiness
return HTTP 503; requests while another audio session is active return HTTP 409.
Both responses use a JSON `detail` message. WebSocket failures use an `error`
event with a `message`, followed by connection closure.

To choose another local port or open the browser after startup:

```powershell
.\run_web.ps1 -Port 8765 -OpenBrowser
```

For testing, choose an audio/video file (up to 250 MB and two hours) or paste a
YouTube URL. A local playback control appears for the selected file so the
recording can be compared with timestamped transcript chunks while it plays;
playback chunks end after detected silence between spoken phrases, with a
maximum segment length set by the playback slider. Spoken Bible references and
recognizable verse text are linked to Biblia Resurse Creștine; only transcript
fragments used for Bible lookup leave the computer.

To preload one trusted local sample (including its playback control), set it
before starting the server:

```powershell
$env:ROMANIAN_ASR_SAMPLE = 'D:\path\sample.mp3'
.\run_web.ps1
```
