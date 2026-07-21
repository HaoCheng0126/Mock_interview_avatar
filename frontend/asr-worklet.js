/* Convert the browser's native microphone rate (normally 44.1/48 kHz) into
   mono PCM16 at 16 kHz and emit one transferable 20 ms packet at a time. */

class Pcm16kCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.packetSamples = 320;
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.packet = new Int16Array(this.packetSamples);
    this.packetOffset = 0;
  }

  pushSample(sample) {
    const clamped = Math.max(-1, Math.min(1, sample));
    this.packet[this.packetOffset++] =
      clamped < 0 ? Math.round(clamped * 0x8000) : Math.round(clamped * 0x7fff);
    if (this.packetOffset < this.packetSamples) return;

    const ready = this.packet;
    this.port.postMessage(ready.buffer, [ready.buffer]);
    this.packet = new Int16Array(this.packetSamples);
    this.packetOffset = 0;
  }

  process(inputs, outputs) {
    const input = inputs[0] && inputs[0][0];
    const output = outputs[0];
    if (output) {
      for (const channel of output) channel.fill(0);
    }
    if (!input || input.length === 0) return true;

    // Average all source samples covered by one 16 kHz output interval. This is
    // inexpensive, stable across render blocks, and less alias-prone than simply
    // selecting every third sample from a 48 kHz microphone.
    for (let i = 0; i < input.length; i += 1) {
      this.sum += input[i];
      this.count += 1;
      this.phase += this.targetRate;
      if (this.phase >= sampleRate) {
        this.pushSample(this.sum / this.count);
        this.phase -= sampleRate;
        this.sum = 0;
        this.count = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm16k-capture", Pcm16kCaptureProcessor);
