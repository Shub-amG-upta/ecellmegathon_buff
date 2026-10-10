// Record mode (BUILD_PLAN §4.5): read each line of eval/speech_subset.jsonl, record it with the same
// 16 kHz WAV path as push-to-talk (audio.js), play it back, save it as <case>_t<turn>_<speaker>.wav.
// With ui_dev/record_server.py the take is saved straight into eval/audio/; anywhere else (e.g. opened
// from app/server.py) the line list comes from a file picker and each take is downloaded.
"use strict";

const $ = id => document.getElementById(id);
// Same rules as eval/record_lines.sh and ui_dev/record_server.py: lowercase first name; at least 0.6 s;
// a peak under 300/32768 is silence (usually no mic permission).
const SPEAKER_RE = /^[a-z0-9]+$/;
const QUIET_PEAK = 0.05;    // below this the take is probably too far from the mic
const CLIP_PEAK = 0.99;     // at or above this the take is clipping
const MIN_SECONDS = 0.6;
const SILENT_PEAK = 300 / 32768;

const S = { lines: [], i: 0, speaker: "", server: false, take: null, takeUrl: null, recording: false };

// The same name eval/wer.py reads back (and ui_dev/record_server.py writes).
function recordingName(caseId, turn, speaker) {
  return `${caseId}_t${turn}_${speaker}.wav`;
}

function speakerName() {
  const base = $("speaker").value.trim().toLowerCase().replace(/-noisy$/, "");
  if (!SPEAKER_RE.test(base)) return null;
  return $("noisy").checked && !base.endsWith("-noisy") ? `${base}-noisy` : base;
}

function setupError(msg) { $("setup-error").hidden = !msg; $("setup-error").textContent = msg || ""; }
function lineError(msg) { $("line-error").hidden = !msg; $("line-error").textContent = msg || ""; }

async function hasServer() {
  try {
    const r = await fetch("/api/rec/lines?speaker=probe");
    return r.ok;
  } catch { return false; }
}

async function load() {
  setupError("");
  const sp = speakerName();
  if (!sp) { setupError("Enter your first name: lowercase letters and digits only (e.g. sarthak)."); $("speaker").focus(); return; }
  S.speaker = sp;
  try { localStorage.setItem("rec-speaker", $("speaker").value.trim()); } catch { /* storage blocked */ }
  if (S.server) {
    const r = await fetch(`/api/rec/lines?speaker=${encodeURIComponent(sp)}`);
    const d = await r.json();
    if (!r.ok) { setupError(d.error || "could not load lines"); return; }
    S.lines = d.lines;
    $("mode").textContent = `Saving to ${d.audio_dir} as speaker "${sp}".`;
  } else {
    const f = $("picker").files[0];
    if (!f) { setupError("Pick eval/speech_subset.jsonl first."); return; }
    let all;
    try {
      all = (await f.text()).split("\n").filter(s => s.trim()).map(s => JSON.parse(s));
    } catch (e) { setupError(`That file isn't JSON lines: ${e.message}`); return; }
    const noisy = sp.endsWith("-noisy");
    S.lines = all.filter(l => !noisy || l.noisy)
      .map(l => ({ ...l, file: recordingName(l.case, l.turn, sp), done: false }));
    $("mode").textContent = `Downloading each take as speaker "${sp}". Move the files into eval/audio/ afterwards.`;
  }
  if (!S.lines.length) { setupError("No lines to record."); return; }
  const firstTodo = S.lines.findIndex(l => !l.done);
  show(firstTodo === -1 ? 0 : firstTodo);
  $("line-card").hidden = false;
  $("list-card").hidden = false;
}

function show(i) {
  if (S.recording) return;
  S.i = Math.max(0, Math.min(S.lines.length - 1, i));
  const l = S.lines[S.i];
  clearTake();
  lineError("");
  $("progress").textContent = `Line ${S.i + 1} of ${S.lines.length}`;
  $("done-count").textContent = `${S.lines.filter(x => x.done).length} saved`;
  const meta = $("meta");
  meta.replaceChildren();
  const tags = [`case ${l.case}`, `turn ${l.turn}`, ...(l.why || []).map(w => `why: ${w}`)];
  if (l.noisy) tags.push("noisy line");
  if (l.done) tags.push("✓ already saved");
  for (const t of tags) {
    const s = document.createElement("span");
    s.className = "tag";
    s.textContent = t;
    meta.appendChild(s);
  }
  $("say").textContent = l.say;
  renderList();
}

function renderList() {
  const ol = $("lines");
  ol.replaceChildren();
  S.lines.forEach((l, k) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    if (k === S.i) b.setAttribute("aria-current", "true");
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.textContent = l.done ? "✓" : "";
    const txt = document.createElement("span");
    txt.className = "txt";
    txt.textContent = `${l.case} t${l.turn} · ${l.say}`;
    b.append(mark, txt);
    b.onclick = () => show(k);
    li.appendChild(b);
    ol.appendChild(li);
  });
}

function clearTake() {
  if (S.takeUrl) URL.revokeObjectURL(S.takeUrl);
  S.take = null;
  S.takeUrl = null;
  $("take").classList.remove("show");
  $("playback").removeAttribute("src");
}

async function toggleRecord() {
  lineError("");
  if (!S.recording) {
    clearTake();
    try {
      await Mic.start(rms => { $("level-bar").style.width = `${Math.min(100, rms * 400)}%`; });
    } catch (e) { lineError(`Microphone unavailable: ${e.message}`); return; }
    S.recording = true;
    $("rec").classList.add("recording");
    $("rec").textContent = "■ Stop";
    return;
  }
  S.recording = false;
  $("rec").classList.remove("recording");
  $("rec").textContent = "● Record";
  $("level-bar").style.width = "0";
  const clip = await Mic.stop();
  if (!clip || clip.seconds < MIN_SECONDS) { lineError("That was too short. Record again."); return; }
  if (clip.peak < SILENT_PEAK) { lineError("That was silent: check the mic and its permission, then record again."); return; }
  S.take = clip;
  S.takeUrl = URL.createObjectURL(clip.wav);
  $("playback").src = S.takeUrl;
  const notes = [`${clip.seconds.toFixed(1)} s`, `peak ${Math.round(clip.peak * 100)}%`];
  const info = $("take-info");
  info.className = "muted";
  if (clip.peak < QUIET_PEAK) { notes.push("very quiet: move closer to the mic"); info.className = "warn"; }
  if (clip.peak >= CLIP_PEAK) { notes.push("clipping: move back a little"); info.className = "warn"; }
  info.textContent = notes.join(" · ");
  $("take").classList.add("show");
}

async function save() {
  if (!S.take) return;
  const l = S.lines[S.i];
  lineError("");
  if (S.server) {
    const q = new URLSearchParams({ case: l.case, turn: String(l.turn), speaker: S.speaker });
    const r = await fetch(`/api/rec/save?${q}`, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: S.take.wav });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { lineError(`Not saved: ${d.error || r.status}`); return; }
  } else {
    const a = document.createElement("a");
    a.href = S.takeUrl;
    a.download = l.file;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  l.done = true;
  const next = S.lines.findIndex((x, k) => k > S.i && !x.done);
  show(next === -1 ? Math.min(S.i + 1, S.lines.length - 1) : next);
}

function wire() {
  $("load").onclick = load;
  $("speaker").addEventListener("keydown", e => { if (e.key === "Enter") load(); });
  $("noisy").addEventListener("change", () => { if (S.lines.length) load(); });
  $("rec").onclick = toggleRecord;
  $("save").onclick = save;
  $("redo").onclick = () => { clearTake(); toggleRecord(); };
  $("prev").onclick = () => show(S.i - 1);
  $("next").onclick = () => show(S.i + 1);
  document.addEventListener("keydown", e => {
    if (!S.lines.length || e.target.tagName === "INPUT" || e.repeat) return;
    if (e.key === " ") { e.preventDefault(); toggleRecord(); }
    else if (e.key === "Enter" && S.take) { e.preventDefault(); save(); }
    else if (e.key === "ArrowRight") show(S.i + 1);
    else if (e.key === "ArrowLeft") show(S.i - 1);
  });
}

async function init() {
  wire();
  try { $("speaker").value = localStorage.getItem("rec-speaker") || ""; } catch { /* storage blocked */ }
  S.server = await hasServer();
  $("picker-wrap").hidden = S.server;
  $("mode").textContent = S.server
    ? "Record server found: takes are saved straight into eval/audio/."
    : "";
}

init();
