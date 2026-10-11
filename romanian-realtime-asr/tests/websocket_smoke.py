"""End-to-end protocol smoke test using generated audio."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import websockets


async def main() -> None:
    rate = 48_000
    block = 2_048
    rng = np.random.default_rng(7)
    received: list[str] = []

    async with websockets.connect("ws://127.0.0.1:8765/ws/transcribe") as socket:
        await socket.send(json.dumps({"type": "start", "sample_rate": rate}))

        # Quiet calibration, voiced test noise, then enough silence to finalize.
        samples = np.concatenate(
            [
                np.zeros(round(rate * 1.3), dtype=np.float32),
                rng.normal(0, 0.025, round(rate * 1.1)).astype(np.float32),
                np.zeros(round(rate * 1.0), dtype=np.float32),
            ]
        )
        for offset in range(0, len(samples), block):
            await socket.send(samples[offset : offset + block].tobytes())

        await socket.send(json.dumps({"type": "stop"}))
        while True:
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            received.append(event["type"])
            if event["type"] == "stopped":
                break

    required = {"calibrating", "ready", "speech_start", "partial", "final", "stopped"}
    missing = required.difference(received)
    if missing:
        raise RuntimeError(f"Missing WebSocket events: {sorted(missing)}; got {received}")
    print("WebSocket flow passed:", " -> ".join(received))


if __name__ == "__main__":
    asyncio.run(main())
