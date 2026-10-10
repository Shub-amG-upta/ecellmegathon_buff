# BUILD_PLAN: AI Incident Report Assistant (single agent)

Shared build spec for **Track A** (Shubham + Claude) and **Track B** (teammate + Claude).
Megathon X, Qualcomm PS1. Background and rationale: `PLAN.md`. This file is what both of us build against.

**If you are a Claude Code instance reading this:** read the whole file before you write code. Build only the
files your track owns (§5). Treat §4 (contracts) as frozen. If a contract has to change, stop and tell your human,
because the other track depends on it.

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

Source of truth: `schema/incident_form.json`. It's **frozen**: change it only when both humans agree.

| field | LLM key | mandatory | kind | filled by |
|---|---|---|---|---|
| incident_type | `t` | yes | enum: water_leak, fire_smoke, electrical, injury, slip_trip_fall, equipment_failure, chemical_spill, security, near_miss, other | LLM |
| severity | `s` | yes | enum: low, medium, high, critical | LLM, then code applies floors |
| location | `l` | yes | text ≤ 60 | LLM |
| date_time | `d` | yes | text ≤ 40 (the phrase) → code resolves it to ISO | LLM + code |
| persons_involved | `p` | no | list ≤ 4 × 40 | LLM |
| equipment_involved | `e` | no | list ≤ 3 × 40 | LLM |
| description | — | yes | the user's own words, all turns joined | code |
| immediate_actions | `a` | no | list ≤ 3 × 60 | LLM |
| witnesses | `w` | no | list ≤ 3 × 40 | LLM |
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
- `audio_url` is `null` when TTS failed or is off.

### 4.3 Shared eval cases: `eval/cases.jsonl`

A writes the cases and gold labels. B records the audio. One JSON object per line:

```json
{"id": "c001", "turns": [
   {"say": "There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area.",
    "gold": {"t": "water_leak", "l": "Conference Room 2", "d": "around 10 AM", "a": ["area isolated"]}},
   {"say": "Medium.", "gold": {"s": "medium"}},
   {"say": "Submit.", "gold_tool": "submit_report"}]}
```

Audio files: `eval/audio/<case_id>_t<turn>_<speaker>.wav` (16 kHz mono). The reference text for WER is `say`.
`eval/audio/` is gitignored; share the recordings outside git.

### 4.4 Saved report: `reports_out/<id>.json`

The full form (full field names, as in `TurnResult.form`) plus `id`, `submitted_at` (laptop clock),
`turns` (each transcript plus its tool call), and `models` (STT, LLM and TTS names and devices). Owner: A.

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
| `eval/cases.jsonl`, `eval/run_eval.py` | A | extraction accuracy, mandatory recall, hallucination rate, LLM latency |
| `eval/wer.py`, `eval/audio/` | **B** | WER per STT model, STT/TTS latency |
| `tests/test_<module>.py` | owner of the module | |

Git: one repo. Each track works on its own branch (`track-a`, `track-b`) and merges to `main` at the integration
points (§8). Since no file has two owners, merges shouldn't conflict.

---

## 6. Track A details (extraction core)

**LLM call.** `LlamaServerBackend(url="http://localhost:8080", lane="npu").generate(prompt, system=SYSTEM,
json_schema=schema, max_tokens=96)`, at temperature 0. The schema is built from `incident_form.json` as an object
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

**Severity floors:** `injury` → at least `high`; `fire_smoke` → at least `high`; `chemical_spill` → at least `medium`.
When a floor raises a value, the read-back says so.

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
3. `report.py`, the follow-up catalogue, then `eval/cases.jsonl` (15 cases first, 40 later) and `run_eval.py`.
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

**Order of work:**
1. STT + TTS on the laptop behind the §4.1 URLs, then `app/voice.py`.
2. The UI against A's `server.py` (mock backend is fine).
3. `data/vocab.txt`; record `eval/cases.jsonl` (3 voices if possible); `eval/wer.py`.
4. `board/voice_up.sh`: STT + TTS on the QIDK, then the M-board milestone.

---

## 8. Milestones and integration points

| # | milestone | done when | who |
|---|---|---|---|
| M0 | contracts agreed | both humans have read §4; `server.py` returns a mock `TurnResult`; `voice.py` has stub signatures | A + B |
| M1 | text works (laptop) | the §1 example works by typing in the UI, LLM = llama-server on the Mac | A (B's UI) |
| M2 | voice works (laptop) | the same, spoken: mic → laptop whisper → LLM → laptop TTS → audio plays | A + B |
| M3 | **on the QIDK** | `board/up.sh` brings up all three models on the board; the §1 example works by voice; the proof panel shows 29/29 on HTP0; timings labelled BOARD | A + B |
| M4 | measured | 40 cases: extraction accuracy, WER, latency per stage, all BOARD, each run twice | A + B |
| M5 | demo | 3 rehearsed scripts (happy path · missing info → follow-ups · correction + noise), backup video, slides | A + B |
| stretch | tier 2 | UI on the QIDK's own screen; Hinglish; voice activity detection; airplane-mode demo | |

---

## 9. Testing on the QIDK (M3 checklist)

Ports: **8080** llama-server (NPU) · **8082** STT · **8083** TTS · **8000** our app server (laptop, tier 1).

1. `adb devices` shows the board. Log the laptop time; never use the board clock.
2. `.venv/bin/python -m board.board start npu Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`. This refuses any file
   whose sha256 on the board doesn't match the allow-list.
3. `.venv/bin/python -m board.board check npu` must print `OK: placement confirmed` (all layers offloaded, nonzero
   HTP0 buffer). **If it doesn't, nothing measured afterwards counts as NPU.**
4. `board/voice_up.sh` (B): STT and TTS healthy on :8082 and :8083 via `adb forward`.
5. `.venv/bin/python -m board.board temp`: chip temperature before the run.
6. Start `app/server.py`, open `http://localhost:8000`, and run the §1 example by voice.
7. `.venv/bin/python eval/run_eval.py` and `eval/wer.py`: save the results with the model names, flags, laptop time
   and temperature before and after.
8. Server flags for llama-server are fixed: `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4`, plus
   `--device HTP0 -ngl 99`. `--cache-ram 0` is required: without it, board memory ran out after ~36 calls.
9. Known board facts: `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` must be set on every launch (`board.py` does it),
   USB can drop (servers keep running; just rerun the turn), and temperature-0 replies can differ between server
   launches, so run evals twice.

---

## 10. Open questions (humans decide)

1. Build window length and team size: is there a third person for the UI?
2. Our lab QIDK, or a board handed out at the venue? (A fresh board needs everything prebuilt; see
   `docs/reference/phase0b_board_setup.md`.)
3. Is tier 1 (models on the board, UI on the laptop) acceptable to the judges, or must the UI be on the board?
4. English only, or Hinglish too? (`*.en` Whisper vs multilingual.)
