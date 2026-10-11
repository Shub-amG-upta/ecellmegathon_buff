// Incident Report Assistant UI (Track B). Plain JS, no build step.
// Talks to app/server.py (BUILD_PLAN §4.2) on the same origin.
"use strict";

// The form, as in schema/incident_form.json (kept in sync by hand: the server
// only serves ui/). key = the one-letter LLM key; null = filled by code.
const FIELDS = [
  { name: "incident_type", key: "t", label: "Incident type", mandatory: true, kind: "enum", hi: "घटना का प्रकार" },
  { name: "severity", key: "s", label: "Severity", mandatory: true, kind: "enum", hi: "गंभीरता" },
  { name: "location", key: "l", label: "Location", mandatory: true, kind: "text", hi: "स्थान" },
  { name: "date_time", key: "d", label: "Date & time", mandatory: true, kind: "time", hi: "दिनांक और समय" },
  { name: "persons_involved", key: "p", label: "Persons involved", mandatory: false, kind: "list", hi: "शामिल लोग" },
  { name: "equipment_involved", key: "e", label: "Equipment / asset", mandatory: false, kind: "list", hi: "उपकरण" },
  { name: "description", key: null, label: "Description", mandatory: true, kind: "derived", hi: "विवरण" },
  { name: "immediate_actions", key: "a", label: "Actions taken", mandatory: false, kind: "list", hi: "उठाए गए कदम" },
  { name: "witnesses", key: "w", label: "Witnesses", mandatory: false, kind: "list", hi: "गवाह" },
  // FORM=15 context fields (schema/incident_form_15.json): shown only when the server's form has them
  { name: "task", key: "g", label: "Task at the time", mandatory: false, kind: "text", context: true },
  { name: "immediate_cause", key: "c", label: "Immediate cause", mandatory: false, kind: "text", context: true },
  { name: "property_damage", key: "k", label: "Property damage", mandatory: false, kind: "list", context: true },
  { name: "hazard_status", key: "h", label: "Hazard status", mandatory: false, kind: "enum", context: true },
  { name: "reported_to", key: "n", label: "Reported to", mandatory: false, kind: "list", context: true },
  { name: "follow_up", key: null, label: "Follow-up", mandatory: false, kind: "list", hi: "आगे की कार्रवाई" },
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
const SEVERITY_HI = { Low: "कम", Medium: "मध्यम", High: "गंभीर", Critical: "अति गंभीर" };

const $ = id => document.getElementById(id);
let last = null;          // the last TurnResult
let busy = false;
let player = null;        // the assistant audio currently playing
// "voice" = play each reply (TTS) and show it; "text" = show it only. Chosen when the page opens.
let replyMode = "voice";
const MODE_KEY = "reply-mode";
// The language the user speaks: "en", or "hi" (Hindi / Hinglish, translated to English by STT on the
// device). Offered only when GET /api/config lists it (server started with HINGLISH=1). Replies stay English.
let speakLang = "en";
let languages = ["en"];
const LANG_KEY = "speak-lang";

// ---------------------------------------------------------------- API
// Spoken replies in Hindi (⋮ menu, only when the server has a Hindi voice): the server speaks its reply in
// Hindi; the chat text, the form and the report stay English.
const REPLY_LANG_KEY = "reply-lang";
let replyLang = "en";
let replyLanguages = ["en"];

function setReplyLang(code) {
  replyLang = code === "hi" && replyLanguages.includes("hi") ? "hi" : "en";
  $("menu-replylang").textContent = replyLang === "hi" ? "Spoken replies: हिंदी" : "Spoken replies: English";
  try { localStorage.setItem(REPLY_LANG_KEY, replyLang); } catch { /* storage blocked: this session only */ }
}

async function api(method, path, body, contentType, headers) {
  const opts = { method, headers: { ...(headers || {}) } };
  if (method === "POST") opts.headers["X-Reply-Language"] = replyLang;
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

// A spoken turn (push-to-talk or hands-free), with the language the user speaks when the server offers a choice.
function sendSpoken(wav, extra) {
  const headers = { ...(languages.includes("hi") ? { "X-Language": speakLang } : {}), ...(extra || {}) };
  return send(wav, "audio/wav", headers);
}

async function send(body, contentType, headers) {
  if (busy) return;
  setBusy(true);
  showError("");
  try {
    render(await api("POST", "/api/turn", body, contentType, headers));
  } catch (e) {
    showError(`Could not reach the assistant: ${e.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------- rendering
function render(r, opts = {}) {
  if (!r) return;
  const prev = last;
  last = r;
  $("state").textContent = r.state;
  $("state").dataset.state = r.state;
  $("report-id").textContent = r.report_id ? `Report ${r.report_id}` : "";

  hideTyping();
  if (r.transcript) addChat("user", r.transcript, [
    r.timings_ms && r.timings_ms.stt != null ? `heard in ${fmt(r.timings_ms.stt)} ms` : "",
    heardAs(r.stt_language)].filter(Boolean).join(" · "));
  if (r.say) addChat("bot", r.say, "", r.audio_url, r.asked === "s" ? severityChoices() : null);
  if (r.error) showError(r.error);

  renderForm(r, prev);
  renderLatency(r);
  if (r.tool_call || (r.dropped && r.dropped.length)) addTool(r);
  $("view-report").hidden = !(r.state === "submitted" && r.report_id);
  $("menu-report").hidden = $("view-report").hidden;
  if (r.state === "submitted") {
    loadReports();
    if (r.report_id && r.report_id !== shownReport) showReport(r.report_id);   // open it once, on submit
  }
  const willPlay = Boolean(!opts.silent && r.audio_url && replyMode === "voice");
  if (willPlay) play(r.audio_url);
  // hands-free: listen for the answer once the reply is done (not after a silent auto-reset: that waits for
  // the wake word again)
  if (!opts.silent) HandsFree.onReply(r, willPlay);
  if (HandsFree.isOn() && (r.state === "submitted" || r.state === "cancelled")) scheduleAutoReset(r, willPlay);
}

// Hands-free: after a report is submitted or cancelled, start a fresh one so the next "Hey Qualcomm" begins
// on a clean screen. Waits for the confirmation to finish playing, then a few seconds; silent (no greeting:
// nobody has woken it yet); skipped if the user has already started talking again.
const AUTO_RESET_MS = { submitted: 3000, cancelled: 2000 };
const AUTO_RESET_RETRY_MS = 1000, AUTO_RESET_GIVE_UP_MS = 30000;
function scheduleAutoReset(r, willPlay) {
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    setTimeout(() => autoReset(r), AUTO_RESET_MS[r.state] || 3000);
  };
  if (willPlay && player) {
    player.addEventListener("ended", start, { once: true });
    player.addEventListener("error", start, { once: true });
    setTimeout(start, 15000);                  // the audio never ended (blocked, unplugged): reset anyway
  } else start();
}

async function autoReset(r, since = performance.now()) {
  if (!HandsFree.isOn() || last !== r) return;                       // hands-free off, or a new turn came in
  const st = HandsFree.state();
  if (["listen", "sending", "wait", "await-audio"].includes(st)) return;   // the user already started a new report
  if (busy || st !== "idle") {                  // a wake check is running (background talk): try again shortly
    if (performance.now() - since < AUTO_RESET_GIVE_UP_MS) setTimeout(() => autoReset(r, since), AUTO_RESET_RETRY_MS);
    return;
  }
  const dlg = $("report-dialog");
  if (dlg && dlg.open) dlg.close();
  $("chat").replaceChildren();
  $("tools").replaceChildren();
  try { render(await api("POST", "/api/reset"), { silent: true }); } catch (e) { showError(e.message); }
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

// What STT was asked for this turn (server: stt_language), for the line under the user's bubble.
function heardAs(st) {
  if (!st || st.lang !== "hi") return "";
  return st.translate ? "translated from Hindi" : "Hindi, as said";
}

function severityChoices() {
  return speakLang === "hi" ? SEVERITY_CHOICES.map(c => `${c} · ${SEVERITY_HI[c]}`) : SEVERITY_CHOICES;
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
    if (f.context && !(r.form && f.name in r.form)) continue;     // 10-field form: no context fields
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
    if (speakLang === "hi" && f.hi) {
      const h = document.createElement("span");
      h.className = "hi-label";
      h.lang = "hi";
      h.textContent = f.hi;
      dt.appendChild(h);
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
  if (!Mic.isActive()) $("ptt-label").textContent = on ? "Thinking…" : idleLabel();
}

function play(url) {
  if (player) player.pause();
  player = new Audio(url);
  player.onended = () => HandsFree.onAudioEnd();
  player.onerror = () => HandsFree.onAudioEnd();
  player.play().catch(() => HandsFree.onAudioEnd());   // autoplay blocked: the replay button still works
}

// ---------------------------------------------------------------- reply mode
// Only changes what this page does with the reply: in "text" mode nothing plays. The server still
// makes the audio unless it runs with VOICE=off (the HTTP API has no per-request switch).
function setReplyMode(mode) {
  replyMode = mode === "text" ? "text" : "voice";
  document.body.classList.toggle("text-only", replyMode === "text");
  $("reply-mode").textContent = replyMode === "text" ? "💬 Text only" : "🔊 Voice";
  $("menu-reply").textContent = replyMode === "text" ? "Replies: text only" : "Replies: voice and text";
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

// ---------------------------------------------------------------- user / developer view
// "user": the chat only (the QIDK screen); "dev": the chat plus the form, latency, tool calls, NPU proof and
// reports. Chosen with the header switch or the ⋮ menu, remembered on this device; ?view=dev|user overrides.
const VIEW_KEY = "ui-view";
let view = "user";

function setView(v) {
  view = v === "dev" ? "dev" : "user";
  document.body.classList.toggle("view-dev", view === "dev");
  document.body.classList.toggle("view-user", view === "user");
  document.querySelectorAll("[data-view]").forEach(b =>
    b.setAttribute("aria-pressed", b.dataset.view === view ? "true" : "false"));
  try { localStorage.setItem(VIEW_KEY, view); } catch { /* storage blocked: this session only */ }
  if (view === "dev") { loadProof(); loadReports(); }
}

function savedView() {
  const q = new URLSearchParams(location.search).get("view");
  if (q === "dev" || q === "user") return q;
  try { return localStorage.getItem(VIEW_KEY); } catch { return null; }
}

function menuAction(act) {
  const dlg = $("menu-dialog");
  dlg.close();
  if (act === "new") $("reset").click();
  else if (act === "reply") askReplyMode();
  else if (act === "lang") $("lang").click();
  else if (act === "replylang") setReplyLang(replyLang === "hi" ? "en" : "hi");
  else if (act === "handsfree") HandsFree.toggle();
  else if (act === "report") $("view-report").click();
  else if (act === "dev") setView("dev");
}

// ---------------------------------------------------------------- language
// Changes what STT is asked for on spoken turns, and adds Hindi labels and hints to the page. Typed text is
// sent as typed: English or Hinglish in Latin letters (the app understands Hinglish times and short answers).
function idleLabel() {
  return speakLang === "hi" ? "Hold to speak · बोलिए" : "Hold to speak";
}

function setLanguage(code) {
  speakLang = code === "hi" && languages.includes("hi") ? "hi" : "en";
  const hi = speakLang === "hi";
  $("lang").textContent = hi ? "🌐 हिंदी / Hinglish" : "🌐 English";
  $("menu-lang").textContent = hi ? "Language: हिंदी / Hinglish" : "Language: English";
  $("lang-hint").hidden = !hi;
  $("text-in").placeholder = hi ? "Type in English or Hinglish (Roman letters)…" : "Type your message…";
  document.querySelectorAll("#lang-pick [data-lang]").forEach(b =>
    b.setAttribute("aria-pressed", b.dataset.lang === speakLang ? "true" : "false"));
  if (!Mic.isActive() && !busy) $("ptt-label").textContent = idleLabel();
  renderForm(last || { form: {}, missing: [], state: "collecting" }, null);
  try { localStorage.setItem(LANG_KEY, speakLang); } catch { /* storage blocked */ }
}

function savedLanguage() {
  try { return localStorage.getItem(LANG_KEY); } catch { return null; }
}

async function loadConfig() {
  let c = null;
  try {
    c = await api("GET", "/api/config");
    languages = (c && c.languages ? c.languages : []).map(l => l.code);
  } catch { languages = ["en"]; }        // older server: English only
  HandsFree.configure(c && c.wake);
  const multi = languages.includes("hi");
  $("lang").hidden = !multi;
  $("menu-lang").hidden = !multi;
  $("lang-pick").hidden = !multi;
  setLanguage(multi ? savedLanguage() || "en" : "en");
  replyLanguages = c && Array.isArray(c.reply_languages) ? c.reply_languages : ["en"];
  $("menu-replylang").hidden = !replyLanguages.includes("hi");
  let savedReply = null;
  try { savedReply = localStorage.getItem(REPLY_LANG_KEY); } catch { /* storage blocked */ }
  setReplyLang(savedReply || "en");
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
    b.onclick = () => showReport(r.id);
    li.appendChild(b);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- report view
// The saved report (GET /api/reports/<id>) as a readable page: printable (Save as PDF) and downloadable.
let shownReport = null;   // id of the report opened last
let reportData = null;    // its JSON, for the download

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

async function showReport(id) {
  shownReport = id;
  let rep;
  try { rep = await api("GET", `/api/reports/${encodeURIComponent(id)}`); } catch (e) {
    showError(`Could not open ${id}: ${e.message}`);
    return;
  }
  reportData = rep;
  saveStatus("");
  renderReportCard(rep);
  $("report-json").textContent = JSON.stringify(rep, null, 2);
  const dlg = $("report-dialog");
  if (typeof dlg.showModal !== "function") { dlg.setAttribute("open", ""); return; }
  if (!dlg.open) dlg.showModal();
  $("report-card").focus();             // not the first link inside: no focus ring on open
  $("report-card").scrollIntoView({ block: "start" });
}

function fieldLabel(name) {
  const f = FIELDS.find(x => x.name === name);
  const box = el("dt", null, f ? f.label : name);
  if (f && speakLang === "hi" && f.hi) {
    const h = el("span", "hi-label", f.hi);
    h.lang = "hi";
    box.appendChild(h);
  }
  return box;
}

function valueOrEmpty(node, text) {
  if (text == null || text === "") { node.appendChild(el("span", "rv-empty", "Not provided")); return node; }
  node.append(text);
  return node;
}

function renderReportCard(rep) {
  const f = rep.form || {};
  const card = $("report-card");
  card.replaceChildren();

  // header: id, when, type and severity badges
  const head = el("header", "rv-head");
  const title = el("div");
  const h = el("h2", "rv-title", `Incident report ${rep.id}`);
  h.id = "rv-title";
  title.append(h, el("div", "muted", rep.submitted_at ? `Submitted ${when(rep.submitted_at)}` : ""));
  const badges = el("div", "rv-badges");
  if (f.incident_type) badges.appendChild(el("span", "rv-type", String(f.incident_type).replace(/_/g, " ")));
  if (f.severity) badges.appendChild(el("span", `rv-sev sev-${f.severity}`, `${f.severity} severity`));
  head.append(title, badges);
  card.appendChild(head);

  // the fields
  const dl = el("dl", "rv-fields");
  const row = (name, dd) => dl.append(fieldLabel(name), dd);
  row("incident_type", valueOrEmpty(el("dd"), f.incident_type ? String(f.incident_type).replace(/_/g, " ") : null));
  row("severity", valueOrEmpty(el("dd"), f.severity));
  row("location", valueOrEmpty(el("dd"), f.location));
  const t = f.date_time;
  const dd = el("dd");
  if (t && typeof t === "object" && (t.phrase || t.iso)) {
    dd.append(t.resolved && t.iso ? when(t.iso) + (t.approx ? " (approx.)" : "") : (t.phrase || ""));
    if (t.phrase) dd.appendChild(el("span", "rv-said", `said: “${t.phrase}”`));
    if (!t.resolved) dd.appendChild(el("span", "tag warn", "not resolved"));
  } else valueOrEmpty(dd, typeof t === "string" ? t : null);
  row("date_time", dd);
  for (const name of ["persons_involved", "equipment_involved", "witnesses"]) {
    const v = f[name] || [];
    const box = el("dd");
    if (!v.length) valueOrEmpty(box, null);
    else { const chips = el("span", "chips"); v.forEach(x => chips.appendChild(el("span", "chip", x))); box.appendChild(chips); }
    row(name, box);
  }
  card.appendChild(dl);

  // description: the worker's own words
  const desc = el("section", "rv-section");
  desc.appendChild(el("h3", null, "Description"));
  desc.appendChild(f.description ? el("blockquote", "rv-quote", f.description) : el("p", "rv-empty", "Not provided"));
  card.appendChild(desc);

  const list = (heading, items, ordered, note) => {
    const sec = el("section", "rv-section");
    sec.appendChild(el("h3", null, heading));
    if (!items || !items.length) { sec.appendChild(el("p", "rv-empty", "None recorded")); return sec; }
    const ul = el(ordered ? "ol" : "ul", "rv-list");
    items.forEach(x => ul.appendChild(el("li", null, x)));
    sec.appendChild(ul);
    if (note) sec.appendChild(el("p", "hint", note));
    return sec;
  };
  card.appendChild(list("Immediate actions taken", f.immediate_actions, false));
  card.appendChild(list("Recommended follow-up", f.follow_up, true,
    "From the follow-up catalogue for this incident type (not written by the model)."));

  // the conversation it came from
  const said = (rep.turns || []).map(x => x.transcript).filter(Boolean);
  if (said.length) {
    const det = el("details", "rv-section rv-convo");
    det.appendChild(el("summary", null, `What the worker said (${said.length} turns)`));
    const ol = el("ol", "rv-list");
    said.forEach(x => ol.appendChild(el("li", null, x)));
    det.appendChild(ol);
    card.appendChild(det);
  }

  // where it was made
  const m = rep.models || {};
  const bits = ["Made on-device: Qualcomm QIDK, no cloud"];
  if (m.llm) bits.push(`${m.llm.split("/").pop()} on ${(m.llm_device || "?").toUpperCase()}`);
  if (m.type_model) bits.push(`type check: ${m.type_model.split("/").pop()}`);
  if (m.llm_calls != null) bits.push(`${m.llm_calls} model call${m.llm_calls === 1 ? "" : "s"}, ${m.fast_path_turns} turn${m.fast_path_turns === 1 ? "" : "s"} answered by code`);
  card.appendChild(el("footer", "rv-foot", bits.join(" · ")));
}

// In the QIDK app (android/: window.IncidentApp) both buttons save into Download/IncidentReports on the board;
// a WebView ignores window.print() and blob downloads. In a browser they print and download as before.
const onDevice = () => !!(window.IncidentApp && window.IncidentApp.savePdf);

function reportHtml() {
  return `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${location.origin}/style.css">
<style>body{background:#fff;margin:28px}.rv-convo,.no-print{display:none!important}</style></head>
<body>${$("report-card").outerHTML}</body></html>`;
}

function saveStatus(msg, ok) {
  const p = $("rv-status");
  p.textContent = msg || "";
  p.classList.toggle("bad", ok === false);
}
window.appSaved = (msg, ok) => saveStatus(msg, ok);      // called by the app when a file is written

function printReport() {
  if (onDevice() && reportData) {
    saveStatus("Saving the PDF on the device…");
    window.IncidentApp.savePdf(`${reportData.id}.pdf`, reportHtml());
    return;
  }
  document.body.classList.add("print-report");
  window.print();
}

function downloadReport() {
  if (!reportData) return;
  if (onDevice()) {
    saveStatus("Saving the JSON on the device…");
    window.IncidentApp.saveText(`${reportData.id}.json`, JSON.stringify(reportData, null, 2));
    return;
  }
  const blob = new Blob([JSON.stringify(reportData, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${reportData.id}.json`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
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
  $("ptt-label").textContent = idleLabel();
  if (tooShort || !clip) {
    showError("That was too short. Hold the button while you speak.");
    return;
  }
  await sendSpoken(clip.wav);
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
  $("view-report").addEventListener("click", () => last && last.report_id && showReport(last.report_id));
  $("rv-print").addEventListener("click", printReport);
  $("rv-download").addEventListener("click", downloadReport);
  $("rv-close").addEventListener("click", () => $("report-dialog").close());
  window.addEventListener("afterprint", () => document.body.classList.remove("print-report"));
  $("reply-mode").addEventListener("click", askReplyMode);
  document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
  $("menu-btn").addEventListener("click", () => {
    const dlg = $("menu-dialog");
    $("menu-hf").hidden = $("handsfree").hidden;                       // only when the server offers it
    $("menu-hf").textContent = $("handsfree").textContent.replace("🎙 ", "") + " (say “Hey Qualcomm”)";
    if (typeof dlg.showModal === "function") dlg.showModal(); else setView("dev");   // no <dialog>: dev view
  });
  $("menu-dialog").querySelectorAll("[data-act]").forEach(b =>
    b.addEventListener("click", () => menuAction(b.dataset.act)));
  $("lang").addEventListener("click", () => setLanguage(speakLang === "hi" ? "en" : "hi"));
  $("handsfree").addEventListener("click", () => HandsFree.toggle());
  document.querySelectorAll("#lang-pick [data-lang]").forEach(b =>
    b.addEventListener("click", () => setLanguage(b.dataset.lang)));
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
  setView(savedView() || "user");
  if (onDevice()) {
    $("rv-print").textContent = "Save PDF on device";
    $("rv-download").textContent = "Save JSON on device";
  }
  setReplyMode(savedReplyMode() || "voice");
  await loadConfig();                    // before the dialog: it shows the language choice when offered
  askReplyMode();
  renderForm({ form: {}, missing: [], state: "collecting" }, null);
  renderLatency({});
  try {
    const s = await api("GET", "/api/state");
    if (s && s.state === "submitted") shownReport = s.report_id;   // a reload doesn't pop the report up again
    if (s) render(s);
  } catch (e) {
    showError(`Assistant server not reachable: ${e.message}`);
  }
  loadProof();
  loadReports();
}

init();
