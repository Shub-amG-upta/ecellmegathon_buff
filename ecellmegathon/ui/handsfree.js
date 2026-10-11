// Hands-free: say "Hey Qualcomm" instead of holding the button (app/wake.py, POST /api/wake).
// The microphone stays open. Plain loudness checks (no model) cut short bursts of speech; each burst goes to the
// board's wake-word detector. After the phrase the page records until the user pauses (~1 s) and sends that as a
// normal turn. After the assistant's reply (once it has finished speaking) the next answer needs no wake word:
// the page listens for ~7 s. Hold-to-speak keeps working throughout.
// Loaded before app.js; uses its globals at run time (busy, player, replyMode, sendSpoken, $).
"use strict";

const HandsFree = (() => {
  const WIN_MS = 20;            // loudness is measured per 20 ms
  const START_MS = 60;          // this much loud audio starts a burst
  const PREROLL_MS = 400;       // kept from before the burst starts (the "Hey" is often soft)
  const WAKE_GAP_MS = 500;      // a pause this long ends a wake burst
  const WAKE_MAX_MS = 2500;     // a wake burst is checked after at most this long, even if speech goes on
  const TURN_GAP_MS = 1000;     // a pause this long ends the turn after the wake word
  const NO_SPEECH_MS = 7000;    // listening without hearing anything this long: back to waiting
  const TURN_MAX_MS = 45000;
  const MIN_LEVEL = 0.012;      // RMS below this is never speech
  const ECHO_MS = 350;          // ignore the mic this long after the assistant stops speaking
  const KEY = "handsfree";

  const W = {
    available: false, on: false, phrase: "Hey Qualcomm", state: "off",
    acc: [], accN: 0, floor: 0.01, loud: 0,
    pre: [], seg: [], segMs: 0, quiet: 0,       // the wake burst
    after: [], afterSpoke: false,               // audio that came in while the burst was being checked
    buf: [], spoke: false, waited: 0, follow: false, holdUntil: 0,
  };

  // ------------------------------------------------------------------ status line
  const TEXT = {
    idle: () => `Say “${W.phrase}” to start`,
    burst: () => `Say “${W.phrase}” to start`,
    "await-audio": () => "",
    checking: () => "Heard something, checking for the wake word…",
    listen: () => (W.follow ? "Your turn: just answer (no need to say the wake word)"
                            : "Listening… stop talking when you're done"),
    sending: () => "Sending…",
    wait: () => "",
  };

  function status() {
    const el = $("hf-status");
    if (!el) return;
    el.hidden = !W.on;
    el.dataset.state = W.state;
    $("hf-text").textContent = (TEXT[W.state] || (() => ""))();
    const btn = $("handsfree");
    if (btn) {
      btn.textContent = W.on ? "🎙 Hands-free: on" : "🎙 Hands-free: off";
      btn.setAttribute("aria-pressed", W.on ? "true" : "false");
    }
  }

  function to(state) { W.state = state; status(); }

  // ------------------------------------------------------------------ audio frames
  function frame(f) {
    if (!W.on) return;
    W.acc.push(f);
    W.accN += f.length;
    if (W.accN < Mic.rate() * WIN_MS / 1000) return;
    const win = W.acc;
    W.acc = []; W.accN = 0;
    let sum = 0, n = 0;
    for (const c of win) { for (const s of c) sum += s * s; n += c.length; }
    window_(win, Math.sqrt(sum / Math.max(1, n)));
  }

  function speaking() { return player && !player.paused && !player.ended; }

  function window_(win, rms) {
    // the assistant is speaking, the user holds the button, or a turn is in flight: don't listen
    if (Mic.isActive() || busy || speaking() || performance.now() < W.holdUntil) {
      if (W.state === "idle") { W.pre = []; W.loud = 0; }
      return;
    }
    const thr = Math.max(MIN_LEVEL, W.floor * 3.5);
    const loud = rms > thr;
    if (!loud) W.floor = 0.95 * W.floor + 0.05 * rms;           // the room's own level, while nobody talks

    if (W.state === "idle") {
      W.pre.push(win);
      if (W.pre.length > PREROLL_MS / WIN_MS) W.pre.shift();
      W.loud = loud ? W.loud + WIN_MS : 0;
      if (W.loud >= START_MS) {
        W.seg = W.pre.flat(); W.pre = []; W.segMs = W.loud; W.quiet = 0;
        to("burst");
      }
    } else if (W.state === "burst") {
      W.seg.push(...win); W.segMs += WIN_MS;
      W.quiet = loud ? 0 : W.quiet + WIN_MS;
      if (W.quiet >= WAKE_GAP_MS) check(false);
      else if (W.segMs >= WAKE_MAX_MS) check(true);
    } else if (W.state === "checking") {
      W.after.push(...win);
      if (loud) W.afterSpoke = true;
    } else if (W.state === "listen") {
      W.buf.push(...win);
      W.waited += WIN_MS;
      if (loud) { W.spoke = true; W.quiet = 0; } else W.quiet += WIN_MS;
      if (!W.spoke && W.waited >= NO_SPEECH_MS) { W.buf = []; to("idle"); return; }
      if ((W.spoke && W.quiet >= TURN_GAP_MS) || W.waited >= TURN_MAX_MS) finish();
    }
  }

  // ------------------------------------------------------------------ wake check and turn
  async function check(kept) {
    const seg = W.seg;
    W.seg = []; W.after = []; W.afterSpoke = false;
    to("checking");
    let res = null;
    try {
      const { wav } = await Mic.toWav(seg);
      const r = await fetch("/api/wake", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
      res = await r.json();
    } catch (e) { res = { wake: false, error: e.message }; }
    if (!W.on) return;
    if (!res || !res.wake) {
      // Not the wake word. If the user spoke while this burst was being checked (often "Hey Qualcomm" right
      // after a noise), check that speech next instead of throwing it away.
      if (W.afterSpoke && W.after.length) {
        W.seg = W.after;
        W.segMs = W.after.reduce((n, c) => n + c.length, 0) / Mic.rate() * 1000;
        W.quiet = 0;
        W.after = []; W.afterSpoke = false;
        to("burst");
        return;
      }
      W.after = []; to("idle"); return;
    }
    chime();
    // said in one breath ("Hey Qualcomm, there's a fire in the lab"): the burst is part of the turn
    const more = (res.rest && res.rest.trim()) || kept;
    W.buf = more ? seg.concat(W.after) : W.after;
    W.spoke = Boolean(more) || W.afterSpoke;
    W.after = []; W.waited = 0; W.quiet = 0; W.follow = false;
    to("listen");
  }

  async function finish() {
    const buf = W.buf;
    W.buf = [];
    to("sending");
    try {
      const { wav, seconds } = await Mic.toWav(buf);
      if (seconds < 0.3) { to("idle"); return; }
      to("wait");
      await sendSpoken(wav, { "X-Wake": "1" });
    } catch (e) {
      showError(`Hands-free: ${e.message}`);
      to("idle");
    }
  }

  function follow() {
    W.buf = []; W.spoke = false; W.waited = 0; W.quiet = 0; W.follow = true;
    W.holdUntil = performance.now() + ECHO_MS;
    to("listen");
  }

  // app.js calls these
  function onReply(r, willPlay) {
    if (!W.on || W.state === "off") return;
    const ongoing = r && (r.state === "collecting" || r.state === "review") && r.turn_id > 0;
    if (!ongoing) { to("idle"); return; }
    if (willPlay) to("await-audio"); else follow();
  }

  function onAudioEnd() {
    if (W.on && W.state === "await-audio") follow();
  }

  function chime() {
    try {
      const ac = chime.ac || (chime.ac = new AudioContext());
      const o = ac.createOscillator(), g = ac.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ac.currentTime);
      g.gain.exponentialRampToValueAtTime(0.15, ac.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.18);
      o.connect(g).connect(ac.destination);
      o.start(); o.stop(ac.currentTime + 0.2);
    } catch { /* no sound: the status line still changes */ }
  }

  // ------------------------------------------------------------------ on / off
  async function set(on) {
    if (on && !W.available) return;
    if (on) {
      try { await Mic.listen(frame); } catch (e) {
        showError(`Microphone unavailable (${e.message}). Hands-free is off.`);
        on = false;
      }
    } else Mic.unlisten();
    W.on = on;
    W.pre = []; W.seg = []; W.buf = []; W.after = []; W.loud = 0;
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* storage blocked */ }
    to(on ? "idle" : "off");
  }

  function configure(wake) {
    W.available = Boolean(wake && wake.engine && wake.engine !== "off");
    if (wake && wake.phrase) W.phrase = wake.phrase;
    const btn = $("handsfree");
    if (btn) btn.hidden = !W.available;
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch { /* storage blocked */ }
    // the browser asks for the microphone only after a click, so a remembered "on" waits for one
    if (W.available && saved === "1") {
      const once = () => { document.removeEventListener("pointerdown", once); if (!W.on) set(true); };
      document.addEventListener("pointerdown", once);
    }
    status();
  }

  return { configure, set, toggle: () => set(!W.on), onReply, onAudioEnd, isOn: () => W.on,
           state: () => W.state };
})();
