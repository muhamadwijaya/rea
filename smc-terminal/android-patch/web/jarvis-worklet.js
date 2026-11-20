// Microphone capture for JARVIS on the phone: 16 kHz mono float samples → 100 ms chunks of 16-bit PCM (the Live API's
// input format), with the chunk's loudness for the orb. Runs in the AudioWorklet thread.
class JarvisCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = 1600;
    this.buffer = new Int16Array(this.size);
    this.count = 0;
    this.energy = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.count++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.energy += s * s;
      if (this.count === this.size) {
        const level = Math.sqrt(this.energy / this.size);
        this.port.postMessage({ pcm: this.buffer.buffer, level }, [this.buffer.buffer]);
        this.buffer = new Int16Array(this.size);
        this.count = 0;
        this.energy = 0;
      }
    }
    return true;
  }
}

registerProcessor('jarvis-capture', JarvisCapture);
