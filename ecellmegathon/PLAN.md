# ecellmegathon: plan for the basic model

**Event:** Megathon X (E-Cell, IIIT Hyderabad) · **Track:** Qualcomm, Problem Statement 1: AI Incident Report Assistant
**Pipeline asked for:** speak → understand → extract → review → submit, with every AI model on the Snapdragon device
and no cloud APIs.

---

## 1. What the judges score (from the problem statement, page 3)

| # | criterion | what it means for us |
|---|---|---|
| E1 | All AI models run **on-device with Snapdragon acceleration**. No cloud APIs. | Speech-to-text, the LLM and text-to-speech all run on the QIDK. The LLM runs on the NPU (HTP), and we show that it does. |
| E2 | Understands **natural conversational speech**, not fixed commands | No keyword templates. Free narration, corrections ("actually it was 11"), and answers to follow-up questions. |
| E3 | **Speech recognition** and transcription accuracy | Measure word error rate (WER) on our own recorded test set. Bias Whisper toward our vocabulary with a location and asset word list. |
| E4 | **Extraction** accuracy | Measure per-field accuracy on a test set with gold labels. |
| E5 | **Mapping** of extracted values to the right report fields | Constrain the output with a grammar so a value can only go into a real field, and an enum field can only hold an allowed value. |

Flow in their diagram: STT → LLM extraction → **mandatory fields present?** → function/tool calling → populate form,
user review, submit → display on screen **and** text-to-speech.

---

## 2. What we carry over from FieldMind (and what we don't)

FieldMind already solved the hardest on-device part on this exact board (QIDK, SM8650, Hexagon HTP v75).
These items transfer directly:

| FieldMind asset | measured result (BOARD, from `fieldmind_research_dossier.md` §6) | use here |
|---|---|---|
| llama.cpp Hexagon build (`b11371`, `99b95488`), `llama-server` on HTP0 | every layer on HTP0, including the output layer (29/29) | the LLM runtime, unchanged |
| **pure Q4_0 recipe** (`build_candidate_gguf.sh`): every matrix Q4_0, embedding and output Q8_0 | published "Q4_0" files silently fall back to CPU; the recipe fixes this | same recipe for any model we add |
| 4 GGUFs already built and sha256-checked | Llama 3.2 3B: 986 prefill / 21 decode tok/s; Qwen3 1.7B: 1,432 / 32; Gemma 3 1B: 1,757 / 44 | our LLM candidates |
| launch flags `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4` | without `--cache-ram 0`, board memory ran out after ~36 calls | same flags |
| **per-request GBNF grammars** (`fieldmind/multi/grammar.py`) | unusable answers went from 25–100% to **0** | the model can only emit a valid tool call |
| "**the model never has the last word**" gate (L6) | citations checked, actions only from a catalogue | code decides "mandatory present?", the tool catalogue is fixed, extracted text is checked against the transcript |
| `LlamaServerBackend` (stdlib HTTP, server-side timings) | prefill/decode times from the server itself | per-stage latency panel in the UI |
| `bench/board.py` (adb, lane start, sha256 check, chip temperature) | proven | board bring-up script |
| evaluation habits: label every number BOARD/LAPTOP, no invented numbers, run twice | temp-0 replies differed on 13–21% of calls between launches | how we report results to judges |

**The biggest lesson for this event:** on this NPU, **decoding dominates** (70–89% of call time; decode is 20–50×
slower than prefill). Every millisecond we save comes from **writing fewer output tokens**. That drives the design
below: short keys, no whitespace, no model-written description, and follow-ups produced by code.

**What does not transfer:** the boiler domain, the simulator, the belief and merge-rule machinery, and the
multi-agent scheduler. This task needs one LLM job per turn, not many.

**Known constraint (`README_MACOS.md`):** Qualcomm's QAIRT/QNN host tools do not run on macOS. Building llama.cpp's
Hexagon package needs the Linux (Fedora) laptop. The Mac is fine for app development and for driving the board
over adb.

---

## 3. Architecture of the basic model

```
 ┌──────────── QIDK (Snapdragon 8 Gen 3): every AI model runs here ─────────────┐
 │                                                                                 │
 │  mic audio ──► [STT] whisper.cpp server (CPU, port 8082)                       │
 │                   │  transcript (+ initial prompt: site room/asset names)      │
 │                   ▼                                                             │
 │                [LLM] llama-server on HTP0 / NPU (port 8080)                    │
 │                   │  ONE grammar-constrained tool call per user turn:          │
 │                   │  {"tool":"update_report","args":{"t":"water_leak",...}}    │
 │                   ▼                                                             │
 │                [TTS] Piper voice via sherpa-onnx (CPU)  ◄── text to speak      │
 └────────────────────────────────────────────────────────────────────────────────┘
                     ▲ adb forward (tier 1) / localhost (tier 2)
 ┌──────────── orchestrator: plain code, no model ───────────────────────────────┐
 │  1. GATE: drop args whose text is not supported by the transcript;            │
 │     enums are already safe because of the grammar                             │
 │  2. merge into the form state; resolve "around 10 AM" with the device clock   │
 │  3. MANDATORY CHECK (code): missing → ask the template question via TTS       │
 │  4. all present → read-back summary → user says "submit" or edits             │
 │  5. submit_report → JSON (+ PDF) saved on the device, shown on screen         │
 └────────────────────────────────────────────────────────────────────────────────┘
```

### Design decisions, and why

1. **One LLM call per turn, grammar-constrained, outputting a tool call** from a fixed set: `update_report`,
   `submit_report`, `cancel_report`, `no_op` (`schema/incident_form.json`). This is the "function calling" box in the
   problem statement, and the grammar makes a malformed call impossible. We don't use llama-server's native
   `--jinja` tool calling as the main path: FieldMind measured free-form JSON from 1–3B models as 25–100% unusable on
   this board. It can stay as a comparison point.
2. **The per-turn grammar shrinks to the question just asked.** After "Where did it happen?", the grammar allows only
   `l` (plus `submit` and `cancel`). That means fewer output tokens and fewer wrong mappings, the same idea as
   FieldMind's per-request grammars.
3. **Code decides "mandatory fields present?"** and writes the follow-up questions from templates (the `ask` strings
   in the schema). Asking a follow-up costs no extra LLM call.
4. **The description is the user's own transcript**, not a summary written by the model. It costs zero decode tokens
   and cannot contain hallucinations. An optional one-line LLM summary comes later as a stretch goal.
5. **Hallucination gate.** For every free-text value (location, persons, equipment, actions), its words must appear
   in the transcript (normalized fuzzy match). If they don't, the value is dropped and the drop is logged. This is
   FieldMind's citation check, adapted.
6. **Date and time are resolved by code, not the model.** The model copies the phrase ("around 10 AM"); code turns it
   into a timestamp against the device clock. If code can't parse it, the phrase is kept and flagged for review.
7. **Follow-up actions come from a catalogue** keyed by incident type (`data/followup_catalogue.json`, to write),
   the same pattern as FieldMind's action catalogue. The model never writes a procedure.
8. **Severity:** the model suggests a value, code applies minimum levels (injury ≥ high, fire/smoke ≥ high), and the
   user confirms at review.
9. **Push-to-talk, not always-listening.** Hackathon venues are noisy, and voice activity detection is a stretch goal.

### Model choices (start with what is already proven on the board)

| stage | first choice | backup | accelerator | status |
|---|---|---|---|---|
| LLM | **Llama 3.2 3B**, pure Q4_0 (already built, sha256 in `field-mind/bench/board.py`) | **Qwen3 1.7B** pure Q4_0 (1.5× faster decode; its JSON failures are removed by the grammar; run with thinking off) | NPU (HTP0) | proven on the board |
| STT | **whisper.cpp `base.en`** (`small.en` if WER needs it) | Whisper from Qualcomm AI Hub (NPU build) | CPU first | **not yet run on this board** |
| TTS | **Piper** voice through sherpa-onnx (arm64 binary) | Android's built-in `TextToSpeech` (tier 2 app only) | CPU | **not yet run on this board** |

Using both the CPU and the NPU at once is also a pitch point. STT and TTS stay on the CPU while the LLM holds the NPU,
so stages don't fight over one accelerator. This is the heterogeneous-scheduling idea from FieldMind's research
question, applied on a small scale.

### Latency budget for one turn (ESTIMATE from FieldMind's screening rates; must be measured)

| stage | assumption | estimate |
|---|---|---|
| STT, 8 s utterance | whisper `base.en` on 8-core CPU | ~1–2 s (unmeasured) |
| LLM prefill | ~450-token prompt at 986 tok/s (3B) | ~0.5 s |
| LLM decode | ~60 output tokens at 21 tok/s (3B) / 32 tok/s (1.7B) | ~2.9 s / ~1.9 s |
| gate + merge | code | < 50 ms |
| TTS, one sentence | Piper on CPU | ~0.5 s (unmeasured) |
| **total** | | **~5 s (3B) / ~4 s (1.7B)**. Target < 6 s. |

---

## 4. Two tiers: what "done" means

**Tier 1, the basic model (must ship).** All three AI models run on the QIDK. The orchestrator and a web UI run on the
laptop and reach the board through `adb forward`. The mic is on the laptop, and the audio goes to the board's whisper
server. This satisfies E1 as written ("all **AI models** execute on-device"), and it reuses FieldMind's proven
laptop-to-board setup.

**Tier 2, all on the device (stretch, but judges will ask about it).** The UI shows on the QIDK's own screen. The
cheapest way: `llama-server --path ./ui` serves our static page from the board itself, opened in the board's browser
at `localhost:8080`. The orchestration logic moves into the page's JS. The fallback is a thin Android WebView APK
installed with `adb install`. **Check before the event** whether the board has a browser and a usable mic. That
decides which way tier 2 goes.

---

## 5. Repository layout (target)

```
schema/incident_form.json     fields, keys, enums, mandatory flags, tool set   (written)
data/followup_catalogue.json  incident type → recommended follow-up actions
data/vocab.txt                room/asset names, fed to Whisper as its initial prompt
app/
  backends.py                 LlamaServerBackend (ported from FieldMind), WhisperServer, Tts
  grammar.py                  per-turn GBNF from the schema (ported from fieldmind/multi/grammar.py)
  prompt.py                   compact extraction prompt (instructions + form state + transcript)
  gate.py                     grammar-valid check, transcript-support check, severity floors
  timeparse.py                "around 10 AM" / "yesterday evening" → timestamp
  dialog.py                   turn loop: STT → LLM → gate → merge → mandatory check → ask/confirm
  report.py                   submit → JSON (+ PDF) on device
  server.py                   tiny HTTP server for the UI (stdlib)
ui/index.html                 push-to-talk, live form, tool-call log, per-stage latency panel
board/                        bring-up: push binaries + models, sha256 check, start servers, health
eval/
  cases.jsonl                 40 scripted incidents with gold fields
  audio/                      the same cases recorded by 3 team members
  run_eval.py                 WER, per-field accuracy, hallucination rate, latency per stage
```

The agent core stays **stdlib-only**, as in FieldMind, so tier 2 can move it with no dependencies.

---

## 6. Build plan

The hour counts assume about 30 hours of build time with a team of 3–4. Rescale them to the real schedule.

### Before the event (most important, and it can't be done in the venue)

- [ ] **Confirm which board we get.** If it's our lab QIDK, the llama.cpp package and GGUFs are already in
      `/data/local/tmp/llm/`. If Qualcomm hands out fresh boards, bring everything **prebuilt** on the laptop and use
      a one-shot push script with a sha256 check.
- [ ] On the Fedora laptop: build **whisper.cpp** (`whisper-server`) for Android arm64 with the same NDK and container
      as llama.cpp. Download `ggml-base.en.bin` and `ggml-small.en.bin`.
- [ ] Get the **sherpa-onnx** arm64 Android binaries and one Piper English voice (`en_US-lessac-medium` or similar).
- [ ] Smoke-test each one on the board over adb: one WAV transcribed, one sentence spoken, one LLM call with a
      grammar. **Record the timings. These are the first real numbers.**
- [ ] Check the board's browser and mic for tier 2.

### Phase 0 (hours 0–3): bring-up and the skeleton running end to end
- [ ] `board/up.sh`: start llama-server (NPU, 8080) and whisper-server (CPU, 8082), set up `adb forward`, health
      checks, chip temperature.
- [ ] Port `LlamaServerBackend` and `grammar.py` from FieldMind. Add the whisper client.
- [ ] Hard-coded run: WAV file → transcript → LLM → raw JSON printed. **First full run on the board.**

### Phase 1 (hours 3–10): the basic model
- [ ] `grammar.py`: build the per-turn GBNF from the schema (all fields on the first turn, only the asked field after
      a follow-up).
- [ ] `prompt.py`: compact prompt. Few-shot with 2 examples, current form state, and the latest transcript only.
- [ ] `gate.py` + `timeparse.py` + `dialog.py`: mandatory check, template follow-ups, corrections that overwrite a
      field, submit/cancel.
- [ ] `ui/index.html`: push-to-talk button, form filling live, tool-call log, and a latency panel showing STT / prefill
      / decode / TTS with **"NPU"** shown next to the LLM time.
- [ ] TTS for follow-up questions and the final read-back.
- **Milestone M1:** the water-leak example from the problem statement fills the form by voice, asks for the missing
  severity, and submits. All models run on the board.

### Phase 2 (hours 10–18): measure, then improve
- [ ] `eval/cases.jsonl`: 40 incidents across all 10 types, including messy speech (fillers, corrections, Indian
      English, relative times, nothing to extract). Gold labels written by hand.
- [ ] Record the cases, 3 voices × 40 = 120 clips.
- [ ] `run_eval.py`: WER (tiny / base / small), per-field accuracy, mandatory-field recall, hallucination rate
      (filled when the gold is empty), per-stage latency. **Run each configuration twice** (temp-0 drift).
- [ ] Decide **3B vs 1.7B** and **base vs small** on these numbers. Write the decision rule down before looking at
      the results.
- [ ] Improve extraction from the failures: prompt examples, vocab list, severity floors.
- **Milestone M2:** a results table, every number labelled BOARD.

### Phase 3 (hours 18–26): polish for the demo and tier 2
- [ ] Tier 2: UI on the QIDK screen (static page from `llama-server --path`, or the WebView APK).
- [ ] Spoken review step: "Here's what I have: … Say submit, or tell me what to change."
- [ ] Report saved as JSON + PDF on the device, plus a list of past reports.
- [ ] Stretch: Hindi/Hinglish narration (multilingual Whisper `small`); one-line LLM summary; voice activity
      detection; an airplane-mode demo to prove there is no network.

### Phase 4 (hours 26–30): freeze and rehearse
- [ ] Code freeze. Rehearse 3 demo scripts: (1) the happy path, (2) missing information triggers follow-ups, (3) a
      mid-conversation correction plus noise.
- [ ] Slides: problem → architecture → on-device proof (llama-server log lines showing `offloaded 29/29 layers`,
      HTP0 buffer) → measured accuracy and latency → FieldMind lineage.
- [ ] A recorded backup video of the full demo in case the board or USB fails.

---

## 7. Risks

| risk | likelihood | mitigation |
|---|---|---|
| A fresh board at the event with no llama.cpp Hexagon package | medium | prebuild everything on the Fedora laptop before the event; one-shot push script with sha256 check |
| A published "Q4_0" GGUF silently runs on the CPU | high if we download one | only use files built with the pure recipe; check the startup log for `offloaded N/N` and a nonzero HTP0 buffer |
| whisper.cpp or sherpa-onnx fails to run on Android | medium | test before the event; fallback is Whisper from Qualcomm AI Hub, or the Android system TTS in the APK |
| Noisy venue hurts WER | high | push-to-talk, close-talk mic, Whisper initial prompt with our vocabulary |
| USB/adb drops (seen 5 times in FieldMind) | medium | orchestrator retries; servers keep running on the board; the demo resumes |
| Board memory runs out after many calls | known | `--cache-ram 0` (FieldMind soak test) |
| The model maps a value to the wrong field | medium | grammar + per-turn narrowing + the review step; measured in eval |
| Judges ask whether the app is really on-device | certain | show the tier 2 screen, airplane mode, and the HTP0 log lines live |

---

## 8. Open questions for the team

1. How long is the build window, and how many people? (This sets the hours in §6.)
2. Do we get our own lab QIDK, or a board from Qualcomm at the venue?
3. Is tier 1 (models on the board, UI on the laptop) acceptable to the judges, or do they want everything on the board's
   screen? Ask the Qualcomm mentors early.
4. Which language mix should we support: English only, or Hinglish too?
