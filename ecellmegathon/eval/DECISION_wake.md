# "Hey Qualcomm" hands-free: the keep/drop rule, written before any board test

Written 2026-10-11, after the code and its tests were committed and before any recording or board run. Results
must not change this rule; a human override is recorded here with the date and the reason.

## What is being decided

Whether the page offers **Hands-free** (say "Hey Qualcomm" instead of holding the button) in the demo, and with
which detector. Hold-to-speak stays in every case.

| detector | what runs on the board | switch |
|---|---|---|
| `stt` | the Whisper already running: each short speech burst is transcribed, `app/wake.py` looks for the phrase | `WAKE=stt` (default without `KWS_URL`) |
| `kws` | sherpa-onnx keyword spotter, ~3 MB model, `voice/kws_server.py` on port 8085 | `KWS_URL` set by `board/voice_up.sh` |

## Recordings (Sarthak; never committed)

- **Wake clips:** 3 speakers × 10 clips `wake_<n>_<speaker>.wav`: 6 × "Hey Qualcomm" alone, 4 × "Hey Qualcomm,
  there was a fire in the lab" (one breath). Normal voice, ~1 m from the laptop mic, the room as it is.
- **Normal speech:** the speech eval recordings (`eval/RECORDING.md`), which never say the phrase.

## Measure (BOARD), per detector

```
.venv/bin/python eval/wake_eval.py --label BOARD --pos <wake clips> --neg <speech eval clips>
```

plus the CPU temperature (`python -m board.board temp`) before and after 10 minutes with hands-free on and
people talking normally near the laptop.

## Keep hands-free in the demo only if ALL hold

1. detected **≥ 9/10 for every speaker**;
2. false wakes **≤ 1** over all the normal-speech clips;
3. wake check **median ≤ 1.0 s** (the time from the end of "Hey Qualcomm" to "Listening…");
4. CPU peak **≤ 3 °C** above the same 10 minutes without hands-free; no board reboot;
5. zero errors.

If both detectors pass, use the one with fewer false wakes (then: faster). If neither passes, hands-free stays
off (the button is hidden with `WAKE=off`) and the demo uses hold-to-speak.
