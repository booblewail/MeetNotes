/**
 * AudioWorkletProcessor that downsamples microphone input to 16kHz mono Int16 PCM
 * and posts 100ms (1600 sample) frames along with RMS volume and simple energy speech flag.
 */
class PCM16DownsampleProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = (options && options.processorOptions && options.processorOptions.targetSampleRate) || 16000;
    this.chunkSamples = 1600; // 100ms at 16kHz
    this.buffer = new Int16Array(this.chunkSamples);
    this.writeIndex = 0;
    this.sampleAccumulator = 0;
    this.sampleCount = 0;
    this.ratio = sampleRate / this.targetSampleRate;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }
    const channelData = input[0];
    if (!channelData || channelData.length === 0) {
      return true;
    }

    for (let i = 0; i < channelData.length; i++) {
      this.sampleAccumulator += channelData[i];
      this.sampleCount += 1;

      if (this.sampleCount >= this.ratio) {
        const avg = this.sampleAccumulator / this.sampleCount;
        const clamped = Math.max(-1, Math.min(1, avg));
        this.buffer[this.writeIndex++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        this.sampleAccumulator = 0;
        this.sampleCount = 0;

        if (this.writeIndex >= this.chunkSamples) {
          let sumSq = 0;
          for (let j = 0; j < this.chunkSamples; j++) {
            const norm = this.buffer[j] / 32768;
            sumSq += norm * norm;
          }
          const rms = Math.sqrt(sumSq / this.chunkSamples);
          const outCopy = new Int16Array(this.buffer);
          this.port.postMessage(
            {
              pcm16: outCopy.buffer,
              rms,
            },
            [outCopy.buffer]
          );
          this.writeIndex = 0;
        }
      }
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCM16DownsampleProcessor);
