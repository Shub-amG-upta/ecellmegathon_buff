# Hindi and Hinglish input

The app stays in English: the form, the questions, the read-back and the voice. **The user may speak English,
Hindi or Hinglish.** Everything is **off by default**; `HINGLISH=1` turns it on.

| piece | what it does | file |
|---|---|---|
| STT | a multilingual Whisper model (not `*.en`), the language detected per clip (`-l auto`), the text **translated into English** (`--translate`): Hindi speech reaches the app as English, so nothing after STT changes | `board/voice_up.sh`, `voice/laptop_up.sh` |
| time phrases | Hinglish times are understood: "kal raat 8 baje", "subah 10 baje", "das minute pehle", "saadhe teen baje", "dedh baje", "abhi abhi", "lagbhag 11 baje" | `app/timeparse.py` (always on; English phrases are never rewritten, tested on every eval time) |
| fast path | Hinglish short answers in code: "haan, submit kar do", "cancel karo", "medium hai", "subah 10 baje", "conference room 2 mein". "nahi" / "mat" (not / don't) always go to the LLM | `app/fastpath.py` (always on; English answers unchanged) |
| prompt | two rules and one example telling the model the text may be Hinglish (copy text fields as said, "nahi" = not) | `app/prompt.py`, only with `HINGLISH=1` |
| server | `GET /api/config` offers Hindi; a spoken turn with `X-Language: hi` asks Whisper for `language=hi` + translate, `en` for `language=en` (so English turns are not translated); `stt_language` in the result | `app/server.py`, only with `HINGLISH=1` |
| UI | language choice (opening dialog + 🌐 button, shown only when offered), "translated from Hindi" under the user's bubble, Hindi subtitles on the field labels and severity choices, a Hinglish example; replies stay English | `ui/` |

When Whisper takes a Hinglish clip for English it keeps the words in Latin-letter Hindi; that is what the
time, fast-path and prompt changes are for. Text fields are always copied as said (the gate keeps only the
user's own words), so such an answer gives Hinglish text values, e.g. location "Conference Room 2 ke paas".

## Run it

```
HINGLISH=1 STT_MODEL_FILE=ggml-base.bin board/voice_up.sh      # or voice/laptop_up.sh; prints export HINGLISH=1
export HINGLISH=1                                              # the app server reads it (prompt rules, UI language)
```

On the page, pick **हिंदी / Hinglish** when it opens (or the 🌐 button). The server sends each Hindi turn to
Whisper as `language=$STT_LANGUAGE` (default `hi`) with `translate` (`STT_TRANSLATE`, default 1); turns from a
client that names no language (record mode) use the launch flags above.

`STT_MODEL_FILE`: `ggml-base.bin` (default with `HINGLISH=1`), `ggml-small.bin`, or a quantized
`ggml-small-q5_1.bin` (smaller and faster; whisper.cpp's model downloads). `STT_LANGUAGE=hi` forces Hindi
instead of detecting; `STT_TRANSLATE=0` keeps the words as said (for comparison).

## Measure it (nothing below has been run yet)

1. **Typed Hinglish (no STT): `eval/hinglish/cases.jsonl`**, 10 hand-written dialogs, 38 turns, in the
   `eval/nlu` format: every incident shape the problem asks for, plus a negation ("high nahi, medium"), a
   correction, a cancel, a question that must be no_op, and a witness.
   ```
   EVAL_HEAT_PAUSE=90:80 HINGLISH=1 .venv/bin/python benchmarks/run_bench.py --tag v8-hinglish --runs 2 \
       --cases eval/hinglish/cases.jsonl
   ```
   ORACLE check (correct answers in place of the model): tool 1.0, field 1.0, 10/10 dialogs ended right, the
   fast path answers 23 of 38 turns, the gate drops nothing.
2. **Spoken: the same 38 lines, recorded** by each speaker as `hi-01_t1_<speaker>.wav` … (turn numbers from 1,
   as `eval/RECORDING.md`), then `run_eval.py --audio DIR --cases eval/hinglish/cases.jsonl`, once with
   translate on and once with `STT_TRANSLATE=0`. With translate on, text fields come back in English while the
   labels are Hinglish, so compare **tool, type, severity, resolved time and dialogs ended right**; read the
   text fields by eye.
3. **English must not get worse.** `HINGLISH=1` changes the prompt and the STT model for everyone, so run the
   usual English dev split with it too.

## Keep/drop rule (written before any run; same practice as `eval/DECISION_*.md`)

Make `HINGLISH=1` the demo default only if, on 2 board runs each:

1. English dev split with `HINGLISH=1`: fields right not more than **2 below** the current configuration, no
   field loses more than 1, tool accuracy unchanged (± 1 turn), ended right not lower;
2. English speech subset: key-word error with the multilingual model not more than **2 points above** the
   `.en` model of the same size (`eval/STT_DECISION.md`), and its time within that rule's 3.0 s per 8 s clip;
3. Hinglish typed set: tool right ≥ 34/38, type right ≥ 8/10, ended right ≥ 7/10.

Otherwise keep it off and show Hindi as a separate, clearly labelled demo mode.
