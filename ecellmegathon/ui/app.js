// Incident Report Assistant UI (Track B). Plain JS, no build step.
// Talks to app/server.py (BUILD_PLAN §4.2) on the same origin.
"use strict";

// The form, as in schema/incident_form.json (kept in sync by hand: the server
// only serves ui/). key = the one-letter LLM key; null = filled by code.
const FIELDS = [
  { name: "incident_type", key: "t", label: "Incident type", mandatory: true, kind: "enum" },
  { name: "severity", key: "s", label: "Severity", mandatory: true, kind: "enum" },
  { name: "location", key: "l", label: "Location", mandatory: true, kind: "text" },
  { name: "date_time", key: "d", label: "Date & time", mandatory: true, kind: "time" },
  { name: "persons_involved", key: "p", label: "Persons involved", mandatory: false, kind: "list" },
  { name: "equipment_involved", key: "e", label: "Equipment / asset", mandatory: false, kind: "list" },
  { name: "description", key: null, label: "Description", mandatory: true, kind: "derived" },
  { name: "immediate_actions", key: "a", label: "Actions taken", mandatory: false, kind: "list" },
  { name: "witnesses", key: "w", label: "Witnesses", mandatory: false, kind: "list" },
  { name: "follow_up", key: null, label: "Follow-up", mandatory: false, kind: "list" },
];
const BY_KEY = Object.fromEntries(FIELDS.filter(f => f.key).map(f => [f.key, f]));
const LATENCY_ROWS = [
  ["stt", "Speech-to-text", "stt"],
  ["llm_prefill", "LLM prefill", "llm"],
  ["llm_decode", "LLM decode", "llm"],
  ["llm_wall", "LLM call (wall)", "llm"],
  ["code", "Dialog code", null],
  ["tts", "Text-to-speech", "tts"],
  ["total", "Total turn", null],
];
const MIN_RECORD_MS = 300;
// Shown under the severity question as a reminder; the user answers by voice or text.
const SEVERITY_CHOICES = ["Low", "Medium", "High", "Critical"];

const $ = id => document.getElementById(id);
let last = null;          // the last TurnResult
let busy = false;
let player = null;        // the assistant audio currently playing
// "voice" = play each reply (TTS) and show it; "text" = show it only. Chosen when the page opens.
let replyMode = "voice";
const MODE_KEY = "reply-mode";

// ---------------------------------------------------------------- API
async function api(method, path, body, contentType) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.body = body;
    opts.headers["Content-Type"] = contentType || "application/json";
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new Error((data && data.error) || `${method} ${path}: HTTP ${res.status}`);
  return data;
}

async function send(body, contentType) {
  if (busy) return;
  setBusy(true);
  showError("");
  try {
    render(await api("POST", "/api/turn", body, contentType));
  } catch (e) {
    showError(`Could not reach the assistant: ${e.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------- rendering
function render(r) {
  if (!r) return;
  const prev = last;
  last = r;
  $("state").textContent = r.state;
  $("state").dataset.state = r.state;
  $("report-id").textContent = r.report_id ? `Report ${r.report_id}` : "";

  hideTyping();
  if (r.transcript) addChat("user", r.transcript, r.timings_ms && r.timings_ms.stt != null
    ? `heard in ${fmt(r.timings_ms.stt)} ms` : "");
  if (r.say) addChat("bot", r.say, "", r.audio_url, r.asked === "s" ? SEVERITY_CHOICES : null);
  if (r.error) showError(r.error);

  renderForm(r, prev);
  renderLatency(r);
  if (r.tool_call || (r.dropped && r.dropped.length)) addTool(r);
  if (r.state === "submitted") loadReports();
  if (r.audio_url && replyMode === "voice") play(r.audio_url);
}

// One chat message: the bubble, optional choices shown under it (not clickable: answer by voice or text),
// then a line with the time and any meta.
function addChat(who, text, meta, audioUrl, choices) {
  const li = document.createElement("li");
  li.className = who;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  li.appendChild(bubble);
  if (choices) {
    const row = document.createElement("div");
    row.className = "choices";
    for (const c of choices) {
      const chip = document.createElement("span");
      chip.className = "choice";
      chip.textContent = c;
      row.appendChild(chip);
    }
    li.appendChild(row);
  }
  const line = document.createElement("div");
  line.className = "meta";
  if (audioUrl) {
    const b = document.createElement("button");
    b.className = "replay";
    b.type = "button";
    b.textContent = "▶ replay";
    b.setAttribute("aria-label", "Replay this reply");
    b.onclick = () => play(audioUrl);
    line.appendChild(b);
  }
  line.appendChild(document.createTextNode([meta, clock()].filter(Boolean).join(" · ")));
  li.appendChild(line);
  $("chat").appendChild(li);
  li.scrollIntoView({ block: "nearest" });
}

function clock() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ". . . ." while the assistant works on a turn; render() removes it when the answer arrives.
function showTyping() {
  if ($("typing")) return;
  const li = document.createElement("li");
  li.id = "typing";
  li.className = "bot typing";
  li.setAttribute("role", "status");
  li.setAttribute("aria-label", "The assistant is thinking");
  li.innerHTML = '<div class="bubble"><span class="dot"></span><span class="dot"></span>'
    + '<span class="dot"></span><span class="dot"></span></div>';
  $("chat").appendChild(li);
  li.scrollIntoView({ block: "nearest" });
}

function hideTyping() {
  const t = $("typing");
  if (t) t.remove();
}

function isMissing(r, f) {
  const miss = r.missing || [];
  return miss.includes(f.name) || (f.key && miss.includes(f.key));
}

function valueText(f, v) {
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return null;
  if (f.kind === "time") return typeof v === "object" ? v.phrase || v.iso || "" : String(v);
  if (Array.isArray(v)) return v.join(", ");
  return String(v).replace(/_/g, " ");
}

function renderForm(r, prev) {
  const dl = $("form");
  dl.replaceChildren();
  const editable = r.state === "review";
  $("form-hint").innerHTML = editable
    ? "Review: click a value to correct it, or say what to change. <span class=\"req\">*</span> = mandatory."
    : "Fields fill in as you talk. <span class=\"req\">*</span> = mandatory.";
  for (const f of FIELDS) {
    const v = r.form ? r.form[f.name] : null;
    const dt = document.createElement("dt");
    dt.textContent = f.label;
    if (f.mandatory) {
      const s = document.createElement("span");
      s.className = "req";
      s.textContent = " *";
      s.setAttribute("aria-label", "mandatory");
      dt.appendChild(s);
    }
    const dd = document.createElement("dd");
    if (isMissing(r, f)) dd.classList.add("missing");
    const was = prev && prev.form ? JSON.stringify(prev.form[f.name]) : undefined;
    if (prev && was !== JSON.stringify(v) && valueText(f, v) != null) dd.classList.add("flash");

    if (editable && f.kind !== "derived") {
      dd.appendChild(editor(f, v));
    } else {
      dd.appendChild(display(f, v));
    }
    dl.append(dt, dd);
  }
}

function display(f, v) {
  const span = document.createElement("span");
  const text = valueText(f, v);
  if (text == null) {
    span.className = "empty";
    span.textContent = "—";
    return span;
  }
  if (Array.isArray(v) && f.kind === "list") {
    span.className = "chips";
    for (const item of v) {
      const c = document.createElement("span");
      c.className = "chip";
      c.textContent = item;
      span.appendChild(c);
    }
    return span;
  }
  span.textContent = text;
  if (f.kind === "time" && v && typeof v === "object") {
    const tag = document.createElement("span");
    tag.className = v.resolved ? "tag" : "tag warn";
    tag.textContent = v.resolved && v.iso ? v.iso.replace("T", " ").slice(0, 16) : "not resolved";
    span.appendChild(tag);
  }
  return span;
}

function editor(f, v) {
  const input = document.createElement("input");
  input.value = valueText(f, v) || "";
  input.setAttribute("aria-label", f.label);
  if (f.kind === "list") input.placeholder = "comma-separated";
  const commit = async () => {
    const raw = input.value.trim();
    if (raw === (valueText(f, v) || "")) return;
    let value = raw;
    if (f.kind === "list") value = raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];
    if (f.kind === "enum") value = raw.toLowerCase().replace(/\s+/g, "_");
    try {
      render(await api("POST", "/api/edit", JSON.stringify({ field: f.name, value })));
    } catch (e) {
      showError(`Edit failed: ${e.message}`);
    }
  };
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } });
  input.addEventListener("change", commit);
  return input;
}

function fmt(ms) {
  return ms == null ? "—" : Math.round(ms).toLocaleString();
}

function renderLatency(r) {
  const t = r.timings_ms || {};
  const d = r.devices || {};
  const body = $("latency");
  body.replaceChildren();
  for (const [key, label, devKey] of LATENCY_ROWS) {
    const tr = document.createElement("tr");
    if (key === "total") tr.className = "total";
    const dev = devKey ? d[devKey] : null;
    const devCell = document.createElement("td");
    if (dev) {
      const s = document.createElement("span");
      s.className = `dev ${dev}`;
      s.textContent = dev.toUpperCase();
      devCell.appendChild(s);
    }
    const name = document.createElement("td");
    name.textContent = label;
    const ms = document.createElement("td");
    ms.className = "num";
    ms.textContent = fmt(t[key]);
    if (t[key] == null) ms.title = "not measured";
    tr.append(name, devCell, ms);
    body.appendChild(tr);
  }
}

function addTool(r) {
  const li = document.createElement("li");
  const head = document.createElement("div");
  head.className = "muted";
  head.textContent = `turn ${r.turn_id}`;
  li.appendChild(head);
  if (r.tool_call) {
    const code = document.createElement("code");
    code.textContent = JSON.stringify(r.tool_call);
    li.appendChild(code);
  }
  for (const d of r.dropped || []) {
    const p = document.createElement("div");
    p.className = "dropped";
    const field = BY_KEY[d.field] ? BY_KEY[d.field].label : d.field;
    p.textContent = `dropped ${field} = ${JSON.stringify(d.value)} (${d.reason})`;
    li.appendChild(p);
  }
  $("tools").prepend(li);
}

function showError(msg) {
  $("error").hidden = !msg;
  $("error").textContent = msg || "";
}

function setBusy(on) {
  busy = on;
  if (on) showTyping(); else hideTyping();
  $("ptt").classList.toggle("busy", on);
  $("ptt").setAttribute("aria-busy", on ? "true" : "false");
  if (!Mic.isActive()) $("ptt-label").textContent = on ? "Thinking…" : "Hold to speak";
}

function play(url) {
  if (player) player.pause();
  player = new Audio(url);
  player.play().catch(() => { /* autoplay blocked: the replay button still works */ });
}

// ---------------------------------------------------------------- reply mode
// Only changes what this page does with the reply: in "text" mode nothing plays. The server still
// makes the audio unless it runs with VOICE=off (the HTTP API has no per-request switch).
function setReplyMode(mode) {
  replyMode = mode === "text" ? "text" : "voice";
  document.body.classList.toggle("text-only", replyMode === "text");
  $("reply-mode").textContent = replyMode === "text" ? "💬 Text only" : "🔊 Voice";
  if (replyMode === "text" && player) player.pause();
  try { localStorage.setItem(MODE_KEY, replyMode); } catch { /* storage blocked: still works this session */ }
}

function savedReplyMode() {
  try { return localStorage.getItem(MODE_KEY); } catch { return null; }
}

function askReplyMode() {
  const dlg = $("mode-dialog");
  if (typeof dlg.showModal !== "function") {   // very old WebView: no <dialog>, keep the saved/default mode
    setReplyMode(savedReplyMode() || "voice");
    return;
  }
  const saved = savedReplyMode();
  dlg.showModal();
  const pick = dlg.querySelector(`[data-mode="${saved === "text" ? "text" : "voice"}"]`);
  if (pick) pick.focus();
}

// ---------------------------------------------------------------- proof + reports
async function loadProof() {
  const box = $("proof");
  box.replaceChildren();
  let p;
  try { p = await api("GET", "/api/proof"); } catch (e) {
    box.textContent = `No proof available: ${e.message}`;
    return;
  }
  const llm = p && p.llm;
  if (llm && llm.layers != null) {
    const big = document.createElement("div");
    big.className = "proof-big";
    big.textContent = `${llm.offloaded}/${llm.layers} layers on HTP0`;
    const sub = document.createElement("div");
    sub.className = "muted";
    sub.textContent = llm.htp0_model_mib != null
      ? `HTP0 model buffer ${Number(llm.htp0_model_mib).toLocaleString()} MiB` : "";
    box.append(big, sub);
    if (llm.log_lines && llm.log_lines.length) {
      const pre = document.createElement("pre");
      pre.className = "json";
      pre.textContent = llm.log_lines.join("\n");
      box.appendChild(pre);
    }
  } else {
    const m = document.createElement("p");
    m.className = "muted";
    m.textContent = p && p.mock ? `Mock server: ${p.mock}` : "The LLM placement proof is not available yet.";
    box.appendChild(m);
  }
  for (const k of ["stt", "tts"]) {
    if (p && p[k]) {
      const line = document.createElement("div");
      line.className = "muted";
      line.textContent = `${k.toUpperCase()}: ${JSON.stringify(p[k])}`;
      box.appendChild(line);
    }
  }
}

async function loadReports() {
  const ul = $("reports");
  let list;
  try { list = await api("GET", "/api/reports"); } catch (e) {
    showError(`Could not load reports: ${e.message}`);
    return;
  }
  ul.replaceChildren();
  if (!list || !list.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "None yet.";
    ul.appendChild(li);
    return;
  }
  for (const r of list) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${r.id} · ${(r.incident_type || "").replace(/_/g, " ")} · ${r.location || ""} · ${r.submitted_at || ""}`;
    b.onclick = async () => {
      try {
        const full = await api("GET", `/api/reports/${encodeURIComponent(r.id)}`);
        $("report-view").hidden = false;
        $("report-view").textContent = JSON.stringify(full, null, 2);
      } catch (e) { showError(e.message); }
    };
    li.appendChild(b);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- microphone
// Capture and WAV encoding live in audio.js (shared with record mode).
let recStarted = 0;

async function startRecording() {
  if (Mic.isActive() || busy) return;
  if (player) player.pause();
  showError("");
  try {
    await Mic.start(rms => { $("level-bar").style.width = `${Math.min(100, rms * 400)}%`; });
  } catch (e) {
    showError(`Microphone unavailable (${e.message}). Type instead.`);
    return;
  }
  recStarted = performance.now();
  $("ptt").classList.add("recording");
  $("ptt-label").textContent = "Listening… release to send";
}

async function stopRecording() {
  if (!Mic.isActive()) return;
  const tooShort = performance.now() - recStarted < MIN_RECORD_MS;
  const clip = await Mic.stop();
  $("ptt").classList.remove("recording");
  $("level-bar").style.width = "0";
  $("ptt-label").textContent = "Hold to speak";
  if (tooShort || !clip) {
    showError("That was too short. Hold the button while you speak.");
    return;
  }
  await send(clip.wav, "audio/wav");
}

// ---------------------------------------------------------------- wiring
function wire() {
  const ptt = $("ptt");
  ptt.addEventListener("pointerdown", e => { ptt.setPointerCapture(e.pointerId); startRecording(); });
  ptt.addEventListener("pointerup", stopRecording);
  ptt.addEventListener("pointercancel", stopRecording);
  ptt.addEventListener("keydown", e => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); startRecording(); }
  });
  ptt.addEventListener("keyup", e => { if (e.key === " " || e.key === "Enter") stopRecording(); });
  document.addEventListener("keydown", e => {
    if (e.key === " " && !e.repeat && e.target === document.body) { e.preventDefault(); startRecording(); }
  });
  document.addEventListener("keyup", e => { if (e.key === " " && e.target === document.body) stopRecording(); });

  $("text-form").addEventListener("submit", e => {
    e.preventDefault();
    const text = $("text-in").value.trim();
    if (!text) return;
    $("text-in").value = "";
    send(JSON.stringify({ text }));
  });
  $("reset").addEventListener("click", async () => {
    $("chat").replaceChildren();
    $("tools").replaceChildren();
    try { render(await api("POST", "/api/reset")); } catch (e) { showError(e.message); }
  });
  $("proof-refresh").addEventListener("click", loadProof);
  $("reports-refresh").addEventListener("click", loadReports);
  $("reply-mode").addEventListener("click", askReplyMode);
  const dlg = $("mode-dialog");
  dlg.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => {
    setReplyMode(b.dataset.mode);
    dlg.close();
  }));
  // Esc closes the dialog: keep whatever mode is current.
  dlg.addEventListener("cancel", () => setReplyMode(replyMode));
}

async function init() {
  wire();
  setReplyMode(savedReplyMode() || "voice");
  askReplyMode();
  renderForm({ form: {}, missing: [], state: "collecting" }, null);
  renderLatency({});
  try {
    const s = await api("GET", "/api/state");
    if (s) render(s);
  } catch (e) {
    showError(`Assistant server not reachable: ${e.message}`);
  }
  loadProof();
  loadReports();
}

init();
