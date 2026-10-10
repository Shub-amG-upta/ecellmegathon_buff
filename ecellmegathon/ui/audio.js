// Microphone -> 16 kHz mono 16-bit WAV, shared by push-to-talk (app.js) and record mode (record.js),
// so the eval recordings go through exactly the audio path the app uses.
// Raw PCM comes from an AudioWorklet (MediaRecorder gives webm/opus, which the STT engine cannot take),
// is resampled with an OfflineAudioContext, and encoded as a WAV in JS.
"use strict";

const Mic = (() => {
  const TARGET_RATE = 16000;
  const st = { ctx: null, stream: null, node: null, chunks: [], active: false, onLevel: null };

  const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);`;

  async function ready() {
    if (st.ctx) {
      if (st.ctx.state === "suspended") await st.ctx.resume();
      return;
    }
    st.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    st.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await st.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = st.ctx.createMediaStreamSource(st.stream);
    st.node = new AudioWorkletNode(st.ctx, "capture");
    st.node.port.onmessage = e => {
      if (!st.active) return;
      st.chunks.push(e.data);
      if (st.onLevel) {
        let sum = 0;
        for (const s of e.data) sum += s * s;
        st.onLevel(Math.sqrt(sum / e.data.length));
      }
    };
    src.connect(st.node);
  }

  // Starts capturing. Throws if the microphone is unavailable or permission is refused.
  async function start(onLevel) {
    await ready();
    st.chunks = [];
    st.onLevel = onLevel || null;
    st.active = true;
  }

  // Stops capturing. Returns null if nothing was captured, else
  // { wav: Blob, seconds, peak } with peak = largest absolute sample (0..1) after resampling.
  async function stop() {
    if (!st.active) return null;
    st.active = false;
    const length = st.chunks.reduce((n, c) => n + c.length, 0);
    if (!length) return null;
    const pcm = new Float32Array(length);
    let o = 0;
    for (const c of st.chunks) { pcm.set(c, o); o += c.length; }
    st.chunks = [];
    const out = await resample(pcm, st.ctx.sampleRate, TARGET_RATE);
    let peak = 0;
    for (const s of out) peak = Math.max(peak, Math.abs(s));
    return { wav: encodeWav(out, TARGET_RATE), seconds: out.length / TARGET_RATE, peak };
  }

  async function resample(pcm, fromRate, toRate) {
    if (fromRate === toRate) return pcm;
    const frames = Math.ceil(pcm.length * toRate / fromRate);
    const off = new OfflineAudioContext(1, frames, toRate);
    const buf = off.createBuffer(1, pcm.length, fromRate);
    buf.copyToChannel(pcm, 0);
    const src = off.createBufferSource();
    src.buffer = buf;
    src.connect(off.destination);
    src.start();
    return (await off.startRendering()).getChannelData(0);
  }

  function encodeWav(samples, rate) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  return { start, stop, resample, encodeWav, TARGET_RATE, isActive: () => st.active };
})();
