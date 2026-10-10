# ecellmegathon: plan for the basic model

> **The build spec both tracks work from is [BUILD_PLAN.md](BUILD_PLAN.md)** (contracts, file ownership,
> milestones, QIDK test steps). Where the two disagree, BUILD_PLAN.md wins; this file is the background.

**Event:** Megathon X (E-Cell, IIIT Hyderabad) · **Track:** Qualcomm PS1, AI Incident Report Assistant
**Asked for:** speak → understand → extract → review → submit. Every AI model on the Snapdragon device, no cloud APIs.

---

## 1. What the judges score (problem statement, page 3)

| # | criterion | what it means for us |
|---|---|---|
| E1 | All AI models **on-device with Snapdragon acceleration**. No cloud APIs. | STT, LLM and TTS all run on the QIDK. The LLM runs on the NPU (HTP0), and we show the log lines that prove it. |
| E2 | Understands **natural conversational speech**, not fixed commands | Free narration, corrections ("actually it was 11"), short answers to follow-up questions. |
| E3 | **Speech recognition** accuracy | Word error rate on our own recordings. **Teammate's part** (§4). |
| E4 | **Extraction** accuracy | Per-field accuracy against hand-written gold labels. |
| E5 | **Mapping** to the right report fields | A schema-constrained tool call: a value can only go into a real field, an enum only holds an allowed value. |

Their flow: STT → LLM extraction → **mandatory fields present?** → function/tool calling → populate form, user
review, submit → display on screen **and** speak it back.

Their example, which is our first test case:

> "There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area."
> → type: water leak · location: Conference Room 2 · time: 10 AM · action taken: area isolated

---

## 2. What we took from FieldMind, and what we dropped

FieldMind ran LLMs on this same board (QIDK, SM8650, Hexagon HTP v75) for six weeks. The parts that transfer are now
in this repo, with their tests (20 pass, no board needed):

| now at | from FieldMind | what it gives us |
|---|---|---|
| `app/backends.py` | `fieldmind/runtime/llm_backend.py` (`LlamaServerBackend`) | stdlib HTTP client for llama-server. Prefill/decode times come from the server itself. Added: `json_schema=`, a system message, the Qwen3 thinking-off switch, and a `MockBackend` |
| `app/grammar.py` | `fieldmind/multi/grammar.py` (generic engine only) | GBNF built in code, with exact `accepts` / `longest` checks. The fallback if the schema route misbehaves |
| `board/board.py` | `bench/board.py` | start/stop llama-server on HTP0 over adb, sha256 allow-list, health, speed, chip temperature |
| `board/npu_proof.py` | `bench/board_preflight.py` | reads the startup log for `offloaded 29/29 layers` and a nonzero HTP0 buffer. **This is our E1 evidence** |
| `board/build_candidate_gguf.sh`, `board/gguf_types.py` | same | the pure Q4_0 recipe and the GGUF header checker, for any new model |
| `docs/reference/` | reports + slide notes | build recipe (container, preset, pinned commit, model hashes), NPU stack, key numbers, full research record |

**Board facts we rely on (all BOARD measurements from FieldMind):**
- llama.cpp `b11371` / `99b95488`, Hexagon backend. 4 pure Q4_0 GGUFs already built and sha256-recorded.
- NPU screening (700-token prompt): Llama 3.2 3B **986 prefill / 21 decode tok/s**; Qwen3 1.7B 1,432 / 32;
  Gemma 3 1B 1,757 / 44; Qwen2.5 0.5B 2,960 / 53.
- **Decode dominates**: 82–89% of model time. Every output token on the 3B costs ~50 ms, so the answer is short JSON
  with one-letter keys, and every sentence the user hears is written by code.
- Free-form JSON from these models broke 25–100% of the time. With a grammar it was 0. **Every call is constrained.**
- `--cache-ram 0` is mandatory (without it, memory ran out after ~36 calls). `-lv 4` is needed for the NPU proof.
- Replies at temperature 0 still differ between server launches on 13–21% of calls. Run evals twice.

**Dropped:** the boiler simulator and its data, the belief/merge machinery, the multi-agent scheduler, campaign
runners, the LiteRT and Gemini backends, and all FieldMind results. None of them apply to a one-call-per-turn form
filler. Everything is still in git at commit `367e9b4`.

---

## 3. The basic model

### 3.1 One user turn

```
 transcript (text) ──► build prompt ──► LLM on NPU, schema-constrained ──► {"tool":..., "args":{...}}
   from STT, or                          llama-server :8080, ~80 tok cap        │
   typed in dev                                                                  ▼
                       ┌──────────────── plain code, no model ─────────────────────────────┐
                       │ 1. gate: drop any free-text value not supported by the transcript │
                       │ 2. merge into the form (a stated field overwrites, lists append)  │
                       │ 3. time: "around 10 AM" → timestamp from the device clock         │
                       │ 4. severity floors (injury/fire ≥ high)                           │
                       │ 5. MANDATORY CHECK → missing? ask that field's question           │
                       │                    → complete? read back, wait for "submit"       │
                       └───────────────────────────────────────────────────────────────────┘
                                         │ text to say                    │ form state
                                         ▼                                ▼
                                       TTS                          UI (live form)
```

### 3.2 The LLM call

**Output: one tool call, from the fixed set in `schema/incident_form.json`:** `update_report`, `submit_report`,
`cancel_report`, `no_op`. Keys are one letter on purpose (decode cost).

For the problem statement's example, the target output is:

```json
{"tool":"update_report","args":{"t":"water_leak","l":"near Conference Room 2","d":"around 10 AM","a":["area isolated by maintenance"]}}
```

That's about 45 tokens, roughly 2 s of decode on the 3B (ESTIMATE from the 21 tok/s screening rate).

**Constraint: a JSON schema, compiled by llama-server** (`json_schema=` in `app/backends.py`). The schema comes
from `schema/incident_form.json`: `t` and `s` are enums, `l`/`d` strings with `maxLength`, `p`/`e`/`a`/`w` arrays
with `maxItems`, every arg optional. That covers free text, which FieldMind's hand-built GBNF couldn't. **Check on
day 0** that our llama.cpp build accepts it (one call with the schema). If it doesn't, build the GBNF with a
string rule in `app/grammar.py`.

**Prompt** (target under 500 tokens; prefill is cheap):
- system: the field list with one-line meanings, the enum values, the rule "only fields the user actually said",
  and 2 short examples. Keep it identical every turn so `cache_prompt` can reuse it.
- user: `Form: {current compact state}` · `Asked: <field the assistant just asked about, or none>` ·
  `User: <this turn's transcript>`

`Asked:` is what turns a bare "high" or "the second floor pantry" into the right field.

### 3.3 What code decides (the model never has the last word)

| decision | rule |
|---|---|
| mandatory fields present? | `mandatory: true` in the schema: type, severity, location, date/time, description |
| next question | the first missing mandatory field's `ask` string. No LLM call |
| description | the user's own words, all turns joined. Never generated, so it can't contain hallucinations |
| free-text values | each value's content words must appear in the transcript (normalised, fuzzy). If not, drop and log it |
| date/time | the model copies the phrase; code resolves it. If parsing fails, keep the phrase and flag it for review |
| severity | the model suggests; code applies floors; the user confirms at review |
| follow-up action | from `data/followup_catalogue.json` by incident type. The model never writes a procedure |
| submit | refused while any mandatory field is empty (the assistant asks for it instead) |

### 3.4 Models

| stage | first choice | backup | where | owner |
|---|---|---|---|---|
| LLM | **Llama 3.2 3B** pure Q4_0 (built, hash recorded) | **Qwen3 1.7B** (1.5× faster decode; `thinking=False`) | NPU, HTP0 | us |
| STT | teammate's choice, e.g. whisper.cpp `base.en`/`small.en` | Whisper from Qualcomm AI Hub (NPU) | CPU or NPU | **teammate** |
| TTS | Piper voice via sherpa-onnx (arm64) | Android `TextToSpeech` (if we build an APK) | CPU | unassigned |

STT and TTS on the CPU while the LLM holds the NPU means no two stages compete for one accelerator. That's a
pitch point, carried over from FieldMind's scheduling work.

**Developing without the board:** llama-server also runs on the Mac (`brew install llama.cpp`) with the **same GGUF**
(`adb pull /data/local/tmp/llm/<model>.gguf`). The whole dialog loop can be built and tested there. Numbers from it
are LAPTOP numbers and never go on a slide as board numbers.

---

## 4. The STT contract (us ↔ teammate)

Agree on this once, then neither side blocks the other. Everything downstream of the transcript is built and tested
on typed text until the STT server is ready.

| item | agreed value |
|---|---|
| audio in | WAV, 16 kHz, mono, 16-bit PCM. Push-to-talk, one utterance per request |
| endpoint | `POST http://localhost:8082/inference` on the board (adb-forwarded), multipart field `file`, plus `response_format=json`. This is whisper.cpp server's own API, so no glue is needed if that's the engine. Another engine keeps the same shape |
| optional field | `prompt`: our vocabulary list (`data/vocab.txt`: room, asset and team names), to bias recognition |
| reply | `{"text": "..."}` at minimum. Optionally `"stt_ms"` (server-side time) and `"device"` (`cpu`/`npu`) for the latency panel |
| empty or noise | `{"text": ""}`. The dialog asks the user to repeat; it never calls the LLM on empty text |
| our side | `app/stt.py`: `transcribe(wav_bytes) -> SttResult(text, wall_ms, stt_ms, device)`. Measures wall time itself |
| shared eval | `eval/cases.jsonl` holds the scripted incidents. The teammate records them (`eval/audio/`) for WER; we use the same cases' text for extraction accuracy, then run them end to end |

**Questions for the teammate today:** which engine and model size; CPU or NPU; did it run on the board yet, and how
long does a 5–8 s utterance take; does the board have a usable mic, or is the mic on the laptop?

---

## 5. Who does what

| track | owner | delivers |
|---|---|---|
| STT | **teammate** | the §4 endpoint running on the board, WER on our recordings, the vocab prompt |
| extraction core | **you** | `app/`: schema → prompt → LLM call → gate → merge → mandatory check → dialog; text eval |
| board + E1 proof | you (with FieldMind's scripts) | `board/up.sh`: start llama-server (+ the STT server), forward ports, check placement, log temperature |
| TTS + UI + report output | unassigned, the next free person | push-to-talk page, live form, tool-call log, latency panel, spoken read-back, JSON/PDF report |

---

## 6. Repository layout

```
schema/incident_form.json     fields, keys, enums, mandatory flags, tools          DONE
app/backends.py               llama-server client (+ mock)                          DONE (ported, tested)
app/grammar.py                GBNF engine (fallback constraint)                     DONE (ported, tested)
board/board.py, npu_proof.py  bring-up, sha256 allow-list, NPU proof, temperature   DONE (ported, tested)
board/build_candidate_gguf.sh, gguf_types.py   pure Q4_0 build + check              DONE (ported)
docs/reference/               FieldMind stack, build recipe, numbers, dossier       DONE

app/form.py                   load the schema; json_schema for the LLM; form state + merge        TODO
app/prompt.py                 system prompt (fixed) + per-turn user message                         TODO
app/gate.py                   transcript-support check, severity floors, submit guard               TODO
app/timeparse.py              "around 10 AM", "yesterday evening", "just now" → timestamp           TODO
app/dialog.py                 the turn loop (§3.1); text in → (say, form, log) out                  TODO
app/stt.py                    client for the §4 contract                                            TODO
app/tts.py                    client for the TTS server                                             TODO
app/server.py                 stdlib HTTP server: /turn (text or WAV), /form, /submit, static UI    TODO
ui/index.html                 push-to-talk, live form, tool-call log, latency panel ("NPU" label)    TODO
data/followup_catalogue.json  incident type → follow-up actions                                     TODO
data/vocab.txt                rooms / assets / teams, for STT and for the gate                      TODO
board/up.sh                   one command: start servers, forward ports, prove placement            TODO
eval/cases.jsonl              ~40 scripted incidents + gold fields (multi-turn ones too)            TODO
eval/run_eval.py              per-field accuracy, mandatory recall, hallucination rate, latency     TODO
```

`app/` stays stdlib-only, so it can move onto the board (tier 2) without installing anything.

---

## 7. Build order

The milestones are in order. Hours depend on the event schedule, which isn't known yet (§9).

### M0: one call on the board (first thing)
- [ ] Board up: `python -m board.board start npu Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, then
      `python -m board.board check npu` must print `OK`.
- [ ] One call with a hand-written JSON schema on the example sentence. Confirms that the schema route works at our
      llama.cpp build, and gives the **first real latency number for this task** (BOARD).
- [ ] Same call against llama-server on the Mac, so the core can be developed without the board.

### M1: the basic model, on typed text
- [ ] `form.py` + `prompt.py`: schema → `json_schema`; the prompt from §3.2.
- [ ] `gate.py`, `timeparse.py`, `dialog.py`: the full §3.1 loop, with corrections and submit/cancel.
- [ ] A command-line loop: type a sentence, see the tool call, the form and the next question.
- [ ] 15 text cases in `eval/cases.jsonl` and `run_eval.py` → first per-field accuracy (BOARD).
- **Done when:** the problem statement's example fills type, location, time and action, the assistant asks for
  severity, "it's medium" fills it, "submit" produces the report. On the board's NPU.

### M2: voice in (joins the teammate's work)
- [ ] `stt.py` against the teammate's endpoint. The same loop, now with WAV in.
- [ ] Latency per stage: STT / prefill / decode / code, each labelled BOARD.

### M3: the demo shape
- [ ] TTS for the questions and the read-back.
- [ ] `server.py` + `ui/index.html`: push-to-talk, live form, tool-call log, latency panel with **NPU** shown next
      to the LLM time, and a "proof" panel with the `offloaded 29/29` and HTP0 lines from the startup log.
- [ ] Spoken review: "Here's what I have: … Say submit, or tell me what to change."
- [ ] Report saved as JSON (+ PDF if time allows) and listed on screen.

### M4: measure, then improve
- [ ] 40 cases across all 10 incident types, including messy speech: fillers, corrections, Indian English,
      relative times, and nothing to extract. The teammate records them (3 voices if possible).
- [ ] Write the decision rules down **before** looking at the results: 3B vs 1.7B, and narrowing the schema to the
      asked field vs not.
- [ ] Run each configuration twice (temperature-0 drift). Fix from failures: prompt examples, vocab, floors.
- [ ] A results table, every number labelled BOARD.

### M5: stretch, then freeze
- [ ] Tier 2: the UI on the QIDK's own screen (static page served by our server on the board, or a WebView APK).
- [ ] Hinglish (multilingual Whisper), voice activity detection, a one-line LLM summary, an airplane-mode demo.
- [ ] Code freeze, 3 rehearsed scripts (happy path · missing info → follow-ups · correction + noise), slides,
      a recorded backup video.

---

## 8. Risks

| risk | likelihood | mitigation |
|---|---|---|
| A fresh board at the venue, without our llama.cpp package | medium | everything prebuilt on the Fedora laptop; push script with sha256 check (`docs/reference/phase0b_board_setup.md` has the full recipe) |
| Qualcomm's QAIRT host tools don't run on macOS | known | the Fedora laptop builds; the Mac develops and drives the board over adb |
| llama-server rejects or mis-compiles our JSON schema | low–medium | checked at M0; fallback is GBNF from `app/grammar.py` |
| A downloaded "Q4_0" model has other tensor types | high if we download one | only build with `board/build_candidate_gguf.sh`; `board check npu` on every launch |
| STT is late or slow | medium | the core is built and evaluated on typed text; the contract (§4) is fixed, so integration is one file |
| The model puts a value in the wrong field | medium | schema + `Asked:` line + review step; measured in eval |
| Noisy venue hurts recognition | high | push-to-talk, close mic, vocab prompt |
| USB/adb drops (5 times in FieldMind) | medium | servers keep running on the board; the client retries; the demo resumes |
| Judges ask whether it's really on-device | certain | the proof panel, airplane mode, tier 2 on the board's screen |

---

## 9. Open questions

1. How long is the build window, and how many of us are there? (This sets the hours and who takes TTS + UI.)
2. Our lab QIDK, or a board handed out at the venue?
3. Is tier 1 (models on the board, UI on the laptop) acceptable, or must everything run on the board's screen?
   Ask the Qualcomm mentors early.
4. English only, or Hinglish too? (This decides the STT model: `*.en` or multilingual.)
5. Teammate: engine, device, measured time per utterance, mic location (§4).
