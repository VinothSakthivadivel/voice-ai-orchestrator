// AudioWorklet: downmix + resample mic audio to 16 kHz PCM16 in 20 ms frames (320 samples).
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;           // fractional read position in input stream
    this.carry = new Float32Array(0);
    this.out = new Int16Array(320);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    const buf = new Float32Array(this.carry.length + ch.length);
    buf.set(this.carry); buf.set(ch, this.carry.length);
    let p = this.pos;
    while (p + this.ratio < buf.length) {
      // box-filter average over the decimation window (cheap anti-alias) + linear pick
      const start = Math.floor(p), end = Math.min(buf.length, Math.floor(p + this.ratio));
      let s = 0; for (let i = start; i < Math.max(end, start + 1); i++) s += buf[i];
      const v = s / Math.max(1, end - start);
      this.out[this.n++] = Math.max(-32768, Math.min(32767, v * 32767));
      if (this.n === 320) { this.port.postMessage(this.out.buffer.slice(0)); this.n = 0; }
      p += this.ratio;
    }
    const keep = Math.floor(p);
    this.carry = buf.slice(keep);
    this.pos = p - keep;
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
