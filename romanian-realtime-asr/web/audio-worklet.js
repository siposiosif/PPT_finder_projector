class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.packetSeconds = 0.2;
    this.packetSize = Math.max(1, Math.round(sampleRate * this.packetSeconds));
    this.buffer = new Float32Array(this.packetSize);
    this.offset = 0;
    this.port.onmessage = (event) => {
      if (event.data?.type !== "packet-seconds") return;
      this.packetSeconds = Math.max(0.2, Number(event.data.seconds) || 0.2);
      this.packetSize = Math.max(1, Math.round(sampleRate * this.packetSeconds));
      this.buffer = new Float32Array(this.packetSize);
      this.offset = 0;
    };
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;

    let sourceOffset = 0;
    while (sourceOffset < input.length) {
      const available = this.buffer.length - this.offset;
      const count = Math.min(available, input.length - sourceOffset);
      this.buffer.set(input.subarray(sourceOffset, sourceOffset + count), this.offset);
      this.offset += count;
      sourceOffset += count;

      if (this.offset === this.buffer.length) {
        const packet = this.buffer;
        this.port.postMessage(packet, [packet.buffer]);
        this.buffer = new Float32Array(this.packetSize);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
