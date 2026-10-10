# BUILD_PLAN: AI Incident Report Assistant (single agent)

Shared build spec for **Track A** (Shubham + Claude) and **Track B** (teammate + Claude).
Megathon X, Qualcomm PS1. Background and rationale: `PLAN.md`. This file is what both of us build against.

**If you are a Claude Code instance reading this:** read the whole file before you write code. Build only the
files your track owns (§5). Treat §4 (contracts) as frozen. If a contract has to change, stop and tell your human,
because the other track depends on it.

### What's new (2026-10-10, evening): read this first, Track B

1. **The form is the problem statement's 10 fields** (§3, `schema/incident_form.json` version 2). Field names,
   keys and the spoken questions are unchanged, so the UI and `voice.py` need no change. What changed: 15 incident
   types (`fire`, `slip_trip`, `fall_height`, …; the UI shows them with `_` → space), the ask order
   type → location → time → severity, list limits 4, and severity is never changed by code.
2. **The text eval set** is `eval/nlu/` (65 dialogs, 250 turns). `eval/cases.jsonl` is generated from it in the
   §4.3 format, so `eval/wer.py` reads all 250 turns as before.
3. **New Track B work: record the speech eval subset** (§4.3 "Speech eval", §7 "Speech eval" and "TTS check"):
   36 turns × 3 speakers + 10 noisy, a record mode in the UI, WER on the subset. No TTS dataset: TTS is checked
   by ear against a fixed phrase list. **`eval/RECORDING.md` (instructions + the full script) and
   `eval/record_lines.sh` (a terminal recorder) are written by Track A**, so skip writing them.

---

## 1. What we are building

The user talks; the app fills an incident report form, asks for whatever is missing, reads the report back, and
submits it. Every AI model runs on the Qualcomm QIDK (Snapdragon 8 Gen 3). No cloud APIs anywhere.

Example from the problem statement (our first test case):

```
User:      "There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area."
Form:      type=water_leak  location="near Conference Room 2"  time="around 10 AM" → 2026-10-10T10:00
           actions=["area isolated by maintenance"]
Assistant: "How serious is it: low, medium, high or critical?"            (severity is mandatory and missing)
User:      "Medium."
Assistant: "Here's what I have: a water leak near Conference Room 2 at 10 AM, medium severity, area isolated.
            Say submit, or tell me what to change."
User:      "Submit."
Assistant: "Report IR-0001 submitted."                      (shown on screen + spoken; JSON saved)
```

Judging: on-device with Snapdragon acceleration · natural speech · STT accuracy · extraction accuracy · correct
field mapping.

---

## 2. Architecture: one agent, one LLM call per turn

This is a **single-agent** design. Each user turn makes exactly **one** LLM call, and that call returns one tool call.
Everything else is plain code. There is no multi-agent setup and no scheduler. Each model has a fixed processor:

| stage | model | runs on | owner |
|---|---|---|---|
| speech-to-text | Whisper (whisper.cpp `base.en` to start) | QIDK CPU (NPU optional) | B |
| extraction | Llama 3.2 3B, pure Q4_0, via `llama-server` | **QIDK NPU (HTP0)** | A |
| text-to-speech | Piper voice via sherpa-onnx | QIDK CPU | B |
| dialog logic, gate, report | Python, stdlib only | laptop (tier 1), board later (tier 2) | A |
| UI | one HTML page | laptop browser (tier 1) | B |

Why a single agent: on this NPU, writing the answer (decode) is 82–89% of model time (FieldMind, BOARD). A second
model call costs another 1–3 s per turn. Code is faster and can't hallucinate for the checks.

### One turn

```
 UI (mic or text box)
   │  WAV 16 kHz mono, or text
   ▼
 app/server.py  POST /api/turn ─────────────────────────────────────────────── (A)
   │
   ├─ voice.transcribe(wav) ──► STT on QIDK ─────────────► text            (B)
   │
   ├─ dialog.turn(text):                                                    (A)
   │     prompt = fixed instructions + form so far + "Asked: <field>" + text
   │     LLM on NPU, JSON-schema constrained ─► {"tool":"update_report","args":{...}}
   │     gate: drop values not in the transcript · merge · resolve time · severity floor
   │     mandatory check ─► missing? say that field's question
   │                     ─► complete? read back, wait for "submit"
   │
   ├─ voice.speak(say) ──► TTS on QIDK ─────────────► WAV                  (B)
   ▼
 TurnResult JSON ──► UI shows the form, the tool call, the timings, and plays the audio      (B)
```

### Hard rules (both tracks)

1. **No cloud API** in any code path, not even as a fallback.
2. **No invented numbers.** Every latency or accuracy figure comes from a real run and is labelled `BOARD`,
   `LAPTOP` or `ESTIMATE`. A laptop timing is never reported as a board timing.
3. **The model never has the last word.** Code decides whether mandatory fields are present, which question comes
   next, the description, the follow-up actions, and whether submit is allowed.
4. **`app/` is stdlib only** (urllib, json, http.server, wave, ...). No pip packages, so it can move onto the board.
   Tests may use pytest.
5. Don't edit files the other track owns (§5). Use the contracts in §4.

---

## 3. The form

Source of truth: `schema/incident_form.json` (version 2). It's **frozen**: change it only when both humans agree.
The problem statement's 10 fields, no more. Mandatory fields are asked in the order **type → location → time →
severity** (`ask_order`). Labelling rules for every field: `eval/nlu/LABELING.md`.

| field | LLM key | mandatory | kind | filled by |
|---|---|---|---|---|
| incident_type | `t` | yes | enum: fire, electrical, water_leak, gas_leak, chemical, slip_trip, fall_height, struck_by, caught_in, equipment_failure, lift, structural, medical, security, other | LLM |
| severity | `s` | yes | enum: low, medium, high, critical | LLM; never changed by code |
| location | `l` | yes | text ≤ 60 | LLM |
| date_time | `d` | yes | text ≤ 40 (the phrase) → code resolves it to ISO | LLM + code |
| persons_involved | `p` | no | list ≤ 4 × 40 | LLM |
| equipment_involved | `e` | no | list ≤ 3 × 40 | LLM |
| description | — | yes | the user's own words, all turns joined | code |
| immediate_actions | `a` | no | list ≤ 4 × 60 | LLM |
| witnesses | `w` | no | list ≤ 4 × 40 | LLM |
| follow_up | — | no | from `data/followup_catalogue.json` by type | code |

Tools the LLM may call: `update_report` (args = any subset of the keys above), `submit_report`, `cancel_report`,
`no_op`.

---

## 4. Contracts between the tracks (frozen)

### 4.1 `app/voice.py`: owned by B, called by A

```python
from dataclasses import dataclass

@dataclass
class SttResult:
    text: str                 # "" when nothing was heard; never None
    wall_ms: float            # measured by the client, end to end
    stt_ms: float | None      # server-side time if the engine reports it, else None (never a guess)
    device: str               # "cpu" | "npu" | "laptop"
    model: str                # e.g. "ggml-base.en.bin"
    error: str = ""           # non-empty on failure; text is then ""

@dataclass
class TtsResult:
    wav: bytes                # complete WAV file; b"" on failure
    wall_ms: float
    device: str
    model: str
    error: str = ""

def transcribe(wav: bytes, prompt: str = "") -> SttResult: ...
    # wav: 16 kHz, mono, 16-bit PCM WAV. prompt: vocabulary hint (contents of data/vocab.txt).
    # Must not raise on a network or engine error: return SttResult(text="", error=...).

def speak(text: str) -> TtsResult: ...
    # Must not raise; return TtsResult(wav=b"", error=...) on failure. The dialog continues without audio.
```

Configuration comes from environment variables with these defaults:
`STT_URL=http://localhost:8082`, `TTS_URL=http://localhost:8083`. How B runs STT/TTS behind these URLs is up to B.
The suggestion is whisper.cpp `whisper-server` (its own `POST /inference`, multipart `file`, `response_format=json`)
and a small TTS wrapper around sherpa-onnx. The laptop and the board must expose **the same API**, so switching
is only a URL change.

Until B delivers, A tests with a fake that has the same signatures (in `tests/`, owned by A).

### 4.2 `app/server.py` HTTP API: owned by A, called by B's UI

Served at `http://localhost:8000`. It also serves `ui/` as static files at `/`.

| method + path | request | response |
|---|---|---|
| `POST /api/turn` | body `audio/wav` (16 kHz mono 16-bit), **or** JSON `{"text": "..."}` | `TurnResult` |
| `POST /api/edit` | `{"field": "location", "value": "Conference Room 3"}` (manual edit at review) | `TurnResult` |
| `POST /api/reset` | — | `TurnResult` (empty form, greeting) |
| `GET /api/state` | — | `TurnResult` of the last turn |
| `GET /api/audio/<turn_id>.wav` | — | the TTS audio for that turn (404 if none) |
| `GET /api/reports` | — | `[{"id","incident_type","location","submitted_at"}, ...]` |
| `GET /api/reports/<id>` | — | the full report JSON (§4.4) |
| `GET /api/proof` | — | `{"llm": {"offloaded": 29, "layers": 29, "htp0_model_mib": 1911.9, "log_lines": [...]}, "stt": {...}, "tts": {...}}` |

`TurnResult`:

```json
{
  "turn_id": 3,
  "state": "collecting",
  "transcript": "it's medium",
  "say": "Here's what I have: ... Say submit, or tell me what to change.",
  "audio_url": "/api/audio/3.wav",
  "asked": null,
  "form": {"incident_type": "water_leak", "severity": "medium", "location": "near Conference Room 2",
           "date_time": {"phrase": "around 10 AM", "iso": "2026-10-10T10:00:00+05:30", "resolved": true},
           "persons_involved": [], "equipment_involved": [], "description": "There was a water leak ...",
           "immediate_actions": ["area isolated by maintenance"], "witnesses": [],
           "follow_up": ["Inspect source pipe", "Check for electrical exposure"]},
  "missing": [],
  "tool_call": {"tool": "update_report", "args": {"s": "medium"}},
  "dropped": [{"field": "w", "value": "John", "reason": "not in transcript"}],
  "report_id": null,
  "timings_ms": {"stt": 812.0, "llm_prefill": 410.2, "llm_decode": 1890.5, "llm_wall": 2402.0,
                 "code": 3.1, "tts": 520.0, "total": 3790.4},
  "devices": {"stt": "cpu", "llm": "npu", "tts": "cpu"},
  "error": ""
}
```

- `state` is one of `collecting` | `review` | `submitted` | `cancelled`.
- A timing that was not measured is `null`, never 0.
- `audio_url` is `null` when TTS is off. **Speech is made after the reply (2026-10-10):** the turn response
  comes back as soon as the form is updated, with `timings_ms.tts: null`; `GET audio_url` waits until the audio
  is ready (up to 30 s; 404 if TTS failed) and carries the TTS time in an `X-TTS-Ms` header; `GET /api/state` then
  shows `timings_ms.tts` and `devices.tts`. UI: render the form at once, then play `audio_url`, then read the TTS
  time from the header or from `/api/state`.
- `fast_path: true` when code answered the turn without the LLM (bare severity word, exact type name, pure time
  answer, submit / cancel); the `llm_*` timings are then `null`.

### 4.3 Shared eval cases: `eval/cases.jsonl`

A writes the cases and gold labels. B records the audio. The source is the text eval set in `eval/nlu/` (65
dialogs, 250 turns, `build_cases.py`); running it also writes `eval/cases.jsonl` in this shared format, one
dialog per line (generated: don't edit it by hand):

```json
{"id": "ps-01", "split": "demo", "turns": [
   {"say": "There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area.",
    "gold": {"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM", "a": ["isolated the area"]}},
   {"say": "it's medium", "gold": {"s": "medium"}},
   {"say": "submit", "gold": {}, "gold_tool": "submit_report"}]}
```

Splits: `demo` and `dev` are for tuning; `test` (21 dialogs) is locked until the M4 decision rules are written.

Audio files: `eval/audio/<case_id>_t<turn>_<speaker>.wav` (16 kHz mono). The reference text for WER is `say`.
`eval/audio/` is gitignored; share the recordings outside git.

### 4.4 Saved report: `reports_out/<id>.json`

The full form (full field names, as in `TurnResult.form`) plus `id`, `submitted_at` (laptop clock),
`turns` (each transcript plus its tool call), and `models` (STT, LLM and TTS names and devices). Owner: A.

### 4.5 Speech eval: `eval/speech_subset.jsonl`

Not all 250 turns are recorded: 36 dev turns chosen by rule (`eval/speech_subset.py`, Track A) where STT is most
likely to break extraction: numbers and times, names, Indian English and fillers, one-word meaning flips
("not", "actually"), short answers, long narrations, and the problem statement's example. One line per turn:

```json
{"case": "wit-02", "turn": 1, "say": "There's oil spilled near the generator room entrance. ...", "why": ["name", "number"], "noisy": false}
```

- **Speakers:** 3 people (Shubham, Sarthak, one more), each reads all 36 lines naturally, not slowly.
- **Noise:** the 10 lines with `"noisy": true` are recorded again by each speaker in a noisy room (canteen, or
  chatter playing nearby), as speaker `<name>-noisy`.
- **Files:** `eval/audio/<case>_t<turn>_<speaker>.wav`, 16 kHz mono 16-bit, exactly the naming `eval/wer.py`
  already reads (turn numbers from 1). `eval/audio/` stays out of git; share it as a zip.
- **Metrics:** B: WER and STT latency per model (`eval/wer.py`). A: extraction from the transcripts against the
  same gold (`eval/run_eval.py --audio`, built next), next to the typed-text score: "field accuracy typed vs
  spoken" is the evidence for judging criterion E3.
- Test-split lines are never recorded for tuning.

---

## 5. Who builds what

| path | owner | status |
|---|---|---|
| `schema/incident_form.json` | both (frozen) | done |
| `app/backends.py` | A | done: llama-server client, ported from FieldMind, tested |
| `app/grammar.py` | A | done: GBNF engine (fallback constraint), tested |
| `app/form.py` | A | load schema; build the per-turn JSON schema for the LLM; form state, merge, `missing()` |
| `app/prompt.py` | A | fixed system prompt + 2 examples; the per-turn user message |
| `app/gate.py` | A | transcript-support check; severity floors; submit guard |
| `app/timeparse.py` | A | phrase → ISO time using the laptop clock |
| `app/dialog.py` | A | the turn state machine (§6) |
| `app/report.py` | A | save and list reports (§4.4) |
| `app/server.py` | A | the HTTP API (§4.2) |
| `app/voice.py` | **B** | STT + TTS clients (§4.1) |
| `ui/index.html` (+ `ui/*.js`, `ui/*.css`) | **B** | the whole UI (§7) |
| `board/board.py`, `board/npu_proof.py`, `board/*gguf*` | A | done: ported from FieldMind, tested |
| `board/up.sh` | A | one command: llama-server on NPU, then calls B's `board/voice_up.sh`, forwards ports, prints the proof |
| `board/voice_up.sh` | **B** | start STT (:8082) and TTS (:8083) on the board, forward their ports, health check |
| `data/followup_catalogue.json` | A | incident type → 2–4 follow-up actions |
| `data/vocab.txt` | **B** | room, asset and team names; used by STT and by A's gate |
| `eval/nlu/*`, `eval/cases.jsonl` (generated), `eval/run_eval.py` | A | the text eval set; extraction accuracy per turn and per dialog, extra values, LLM latency |
| `eval/wer.py`, `eval/audio/` | **B** | WER per STT model, STT/TTS latency; the recordings (§4.5) |
| `eval/speech_subset.py`, `eval/speech_subset.jsonl` | A | the recording list (§4.5) |
| record mode in `ui/` | **B** | shows each §4.5 line, records it, saves it under the §4.5 name |
| `tests/test_<module>.py` | owner of the module | |

Git: one repo. Each track works on its own branch (`track-a`, `track-b`) and merges to `main` at the integration
points (§8). Since no file has two owners, merges shouldn't conflict.

---

## 6. Track A details (extraction core)

**LLM call.** `LlamaServerBackend(url="http://localhost:8080", lane="npu").generate(prompt, system=SYSTEM,
json_schema=schema, max_tokens=128)`, at temperature 0. The schema is built from `incident_form.json` as an object
with `tool` (an enum of the 4 tools) and `args` (optional properties `t`/`s` as enums, `l`/`d` as strings with
`maxLength`, `p`/`e`/`a`/`w` as arrays with `maxItems`, each item a string with `maxLength`). Target output:

```json
{"tool":"update_report","args":{"t":"water_leak","l":"near Conference Room 2","d":"around 10 AM","a":["area isolated by maintenance"]}}
```

**Prompt.** The system message (keep it identical every turn; `cache_prompt=True` in the demo) gives the keys with
one-line meanings, the enum values, "only fill fields the user actually said; never guess", and 2 short worked
examples (one first turn, one bare answer like "medium"). The user message is:

```
Form: {"t":"water_leak","l":"near Conference Room 2","d":"around 10 AM"}
Asked: s
User: it's medium I think
```

**Gate (code).**
- For free text (`l`, `p`, `e`, `a`, `w`), at least 60% of the value's content words (lowercased, punctuation and
  stopwords removed) must appear in the transcript so far. Otherwise drop the value and record it in `dropped`.
  The 0.6 is a starting value: tune it on eval cases, never on the demo script.
- For `d`, the phrase must appear in the transcript.
- Enums are already valid because of the schema.

**Severity:** code never changes the user's answer. Below the usual minimum for the type (`fire` and `gas_leak`:
high; `chemical`: medium), the read-back asks them to confirm it ("Fire is usually at least high severity; you said
low. Say a new severity to change it."). Raising it silently overrode explicit answers in 3 eval dialogs.

**Time:** handle "around 10 AM", "10:30", "at 3 pm", "this morning/afternoon/evening", "yesterday evening",
"just now", "an hour ago", "20 minutes ago". Use the laptop clock (the board's clock is wrong). If a phrase can't be
parsed, keep it with `resolved: false`; that still counts as present, and it's flagged at review.

**State machine (`dialog.py`).**

| state | tool from LLM | action |
|---|---|---|
| collecting | update_report | gate → merge → if anything mandatory is missing, ask the first missing field's `ask`, `asked=<key>`; else go to **review** |
| collecting | submit_report | refuse: "I still need the <field>." and ask it |
| collecting | no_op / empty text | re-ask the current question, or "Sorry, I didn't catch that." |
| review | update_report | apply the change, then read the report back again |
| review | submit_report | save the report → **submitted**, say "Report <id> submitted." |
| any | cancel_report | → **cancelled**, "Okay, I've discarded this report." |

Every sentence the assistant says comes from a template in code; the model never writes user-facing text.

**Order of work:**
1. `form.py`, `prompt.py`, `dialog.py` (+ `gate.py`, `timeparse.py`), on `MockBackend` and then on llama-server on
   the Mac (`brew install llama.cpp`, using the same GGUF pulled from the board).
2. `server.py` **early**, even with `MockBackend`, so B can build the UI against the real API.
3. `report.py`, the follow-up catalogue, then the eval set (`eval/nlu/`) and `run_eval.py`.
4. `board/up.sh`, then the M-board milestone (§8).

---

## 7. Track B details (voice + UI)

**STT.**
- Start with whisper.cpp `whisper-server` + `ggml-base.en.bin` on the laptop (`brew install whisper-cpp`), on port
  8082.
- On the QIDK: build `whisper-server` for Android arm64 (the NDK build, on the Fedora laptop if you need the
  Qualcomm toolchain), push it to `/data/local/tmp/stt/`, run it on CPU threads, and `adb forward tcp:8082 tcp:8082`.
- Pass `data/vocab.txt` as the initial `prompt`. Measure `tiny.en` / `base.en` / `small.en` (WER and time per 5–8 s
  utterance, BOARD) and pick one.
- Optional upgrade: Whisper from Qualcomm AI Hub on the NPU. If you do this, measure whether it slows the LLM,
  since they would share HTP0.

**TTS.**
- Piper voice (e.g. `en_US-lessac-medium`) through sherpa-onnx's arm64 Android binaries.
- Wrap it in a tiny HTTP server on :8083 (`POST /tts {"text": ...}` → `audio/wav`), on the laptop first and then on
  the board.
- Fallback if it won't run on the board: say so; the UI can then show text only. Never fall back to a cloud voice.

**UI (`ui/index.html`)**, plain HTML/JS with no build step, served by A's `server.py`:
- A **push-to-talk** button. Capture mic audio with the Web Audio API, resample to 16 kHz mono, and encode a 16-bit
  WAV in JS. (MediaRecorder gives webm/opus, which is the wrong format.) Then `POST /api/turn` with
  `Content-Type: audio/wav`.
- A text box as a fallback, which sends `{"text": ...}`.
- A live form: each field with its value, mandatory fields marked, missing ones highlighted. In review, each field
  is editable (`POST /api/edit`).
- A chat strip showing the user's transcript and the assistant's `say`, and playing `audio_url`.
- A tool-call log showing `tool_call` and `dropped` for each turn.
- A latency panel: STT / LLM prefill / LLM decode / code / TTS with the device next to each (**NPU** on the LLM).
- A proof panel from `GET /api/proof`: "29/29 layers on HTP0, 1,911.9 MiB" and the raw log lines.
- A list of submitted reports, with a view of each.

**Speech eval (new).**
- A **record mode** in the UI (e.g. `/#record`, or `ui/record.html`): pick a speaker name, then for each line of
  `eval/speech_subset.jsonl` show the text, record with the same 16 kHz WAV path as push-to-talk, play it back,
  re-record if needed, and save it as `<case>_t<turn>_<speaker>.wav`. Saving can be a download, or a small
  endpoint in a dev server of yours; don't add it to `app/server.py` (Track A) without asking.
- Record 36 lines × 3 speakers, plus the 10 noisy lines × 3 speakers.
- `eval/wer.py` on the subset for `tiny.en` / `base.en` / `small.en`, with and without the `data/vocab.txt`
  prompt, LAPTOP first, then BOARD. Also report errors on the words that matter: digits, names, "not".
- Keep `data/vocab.txt` in step with the new eval set's names and places (no test-split words).

**TTS check (new; by ear, no dataset).** Everything TTS says comes from a template in `app/dialog.py`, so check
each template once with real values, for clarity and timing:
- every question: "What kind of incident was it?", "Where did it happen?", "When did it happen?",
  "How serious is it: low, medium, high or critical?";
- read-backs with hard values: "Conference Room 2", "Lab 4", "10:45 AM today", "Mr Rao", "Struck by an object",
  "Caught in machinery", and the severity warning ("Fire is usually at least high severity; you said low. ...");
- "Sorry, I didn't catch that.", "Report IR-0001 submitted.";
- record the TTS time per sentence (LAPTOP, then BOARD) for the latency panel.

**Order of work (updated):**
1. ✅ STT + TTS on the laptop behind the §4.1 URLs, then `app/voice.py`.
2. ✅ The UI against A's `server.py`.
3. Record mode → recordings (§4.5) → `eval/wer.py` on the subset; the TTS check.
4. `board/voice_up.sh`: STT + TTS on the QIDK, then the M-board milestone.

---

## 8. Milestones and integration points

| # | milestone | done when | who |
|---|---|---|---|
| M0 | contracts agreed | both humans have read §4; `server.py` returns a mock `TurnResult`; `voice.py` has stub signatures | A + B |
| M1 | text works (laptop) | the §1 example works by typing in the UI, LLM = llama-server on the Mac | A (B's UI) |
| M2 | voice works (laptop) | the same, spoken: mic → laptop whisper → LLM → laptop TTS → audio plays | A + B |
| M3 | **on the QIDK** | `board/up.sh` brings up all three models on the board; the §1 example works by voice; the proof panel shows 29/29 on HTP0; timings labelled BOARD | A + B |
| M4 | measured | dev split: extraction accuracy typed and spoken, WER on the §4.5 subset, latency per stage, all BOARD, each run twice | A + B |
| M5 | demo | 3 rehearsed scripts (happy path · missing info → follow-ups · correction + noise), backup video, slides | A + B |
| stretch | tier 2 | UI on the QIDK's own screen; Hinglish; voice activity detection; airplane-mode demo | |

---

## 9. Testing on the QIDK in one session (M3 + M4)

Ports: **8080** llama-server (NPU) · **8082** STT · **8083** TTS · **8000** our app server (laptop, tier 1).

**Have ready before you sit down with the board:**
- recordings done and unzipped into `eval/audio/` (`eval/RECORDING.md`: 36 lines × 3 speakers + 10 noisy each);
- Track B's `board/voice_up.sh` works on the laptop side and the board binaries are in `engines/`
  (`docs/MODELS_AND_DEPENDENCIES.md`);
- `main` pulled on the laptop that drives the board, `.venv/bin/python -m pytest -q` all green.

**The session, in order (each step's output is saved; stop at the first failed check):**

| # | step | command | pass when |
|---|---|---|---|
| 1 | board present, laptop time logged | `adb devices` | the board is listed |
| 2 | everything up: LLM on the NPU, then STT + TTS | `board/up.sh` | `OK: placement confirmed`, then `ok :8082`, `ok :8083` |
| 3 | the schema works on the board | `.venv/bin/python eval/smoke_llm.py --label BOARD --model Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf` | every constrained reply parses |
| 4 | the demo, by voice | `LLM=llamaserver LLM_LANE=npu LLM_MODEL=Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf .venv/bin/python -m app.server`, open http://localhost:8000, say the §1 example | form fills, asks severity, read-back, submit; latency panel shows NPU |
| 5 | extraction, typed (E4, E5, E2) | same env + `.venv/bin/python eval/run_eval.py --label BOARD` | results saved; **run it twice** |
| 6 | speech: WER (E3) | `.venv/bin/python eval/wer.py --label BOARD` | results saved |
| 7 | speech: typed vs spoken (E3) | same env + `.venv/bin/python eval/run_eval.py --label BOARD --audio eval/audio` | results saved |
| 8 | chip temperature after, stop | `.venv/bin/python -m board.board temp`, then `board/voice_up.sh stop` and `.venv/bin/python -m board.board stop` | — |

Step 5 sends ~120 turns to the LLM (the fast path answers the other ~130 in code); at the screening rates that's
roughly 5–10 min per run (ESTIMATE). Leave cooling gaps between runs.

**Speed comparisons (after step 5, dev split, one change at a time; keep a change only if latency improves and
field accuracy doesn't drop):**

| switch | baseline | variant | how |
|---|---|---|---|
| fast path | on | off | `eval/run_eval.py --label BOARD --no-fast-path` (shows what the fast path saves) |
| answer constraint | `LLM_CONSTRAINT=schema` | `LLM_CONSTRAINT=gbnf` (no whitespace) | same eval with the env var; first run `eval/smoke_llm.py` to check llama-server accepts the grammar |
| context size | `-c 2048` (default, human decision) | `-c 4096` (FieldMind's value), only if 2048 shows problems | `CTX=4096 board/up.sh`, then the same eval |

Compare the eval's `decode_tokens` and `wall_ms` per LLM call, and the field accuracy.

**Fixed facts:** llama-server flags `-c 2048 -np 1 -fit off --cache-ram 0 -lv 4` + `--device HTP0 -ngl 99`
(`--cache-ram 0` is required: without it board memory ran out after ~36 calls). `LD_LIBRARY_PATH` and
`ADSP_LIBRARY_PATH` on every launch (`board.py` does it). USB can drop: the servers keep running, rerun the step.
Temperature-0 replies can differ between server launches, so evals run twice. Log laptop time, never board time.

---

## 10. Open questions (humans decide)

1. Build window length and team size: is there a third person for the UI?
2. Our lab QIDK, or a board handed out at the venue? (A fresh board needs everything prebuilt; see
   `docs/reference/phase0b_board_setup.md`.)
3. Is tier 1 (models on the board, UI on the laptop) acceptable to the judges, or must the UI be on the board?
4. English only, or Hinglish too? (`*.en` Whisper vs multilingual.)
