# Recording the speech eval

For the three people recording: **Shubham, Sarthak, and one more person**. Each of you reads the same
**36 lines** (`eval/RECORDING_SCRIPT.md`) once in a quiet room, then **10 of them** again with noise. About 10 minutes quiet + 5 minutes noisy
per person. What it's for: measuring how speech-to-text affects extraction (judging criterion E3).
Details: BUILD_PLAN §4.5.

## 1. Before you start

- **Use the laptop and mic we'll demo with** (tier 1: the laptop's built-in mic). Don't use a better headset
  mic than the demo has; the numbers should match the demo.
- **Sit as you would in the demo**, about 30–50 cm from the laptop. Same distance for every line.
- **Your speaker name** is your first name in lowercase letters: `shubham`, `sarthak`, … It goes into every file
  name. Use the same name every time.
- Pull the repo (`git pull`), so you have `eval/speech_subset.jsonl` and this file.

## 2. Two ways to record (both make identical files)

**A. Terminal (works now; macOS or Linux):**

```
cd ecellmegathon
eval/record_lines.sh shubham            # the quiet pass: 36 lines
eval/record_lines.sh shubham --noisy    # the noisy pass: 10 lines, saved as shubham-noisy
```

For each line it shows the question the assistant asked and the text to say. Press **Enter** to start, speak,
press **Enter** to stop. It checks the clip (16 kHz mono, not silent, long enough), then **Enter** keeps it,
**r** records again, **p** plays it back, **q** quits. Lines already done are skipped, so you can stop and resume.
macOS asks once for microphone permission for the terminal: allow it. Uses `ffmpeg` (already on Shubham's Mac;
`brew install ffmpeg` otherwise). If the wrong mic is picked: `MIC=":1" eval/record_lines.sh shubham`.

**B. The UI's record mode (any OS, including Windows; Chrome or Edge):**

```
cd ecellmegathon
.venv/bin/python -m ui_dev.record_server        # Windows: .venv/Scripts/python -m ui_dev.record_server
```

Open **http://localhost:8001/record.html**, type your speaker name, **Load lines**. Press **Space** (or ● Record),
speak, **Space** again to stop; listen to the playback, then **Enter** saves and moves on, or **Re-record**. The
take goes straight into `eval/audio/` after the same checks as the terminal (16 kHz mono, at least 0.6 s, not
silent). Warnings under the playback: **"very quiet"** means move closer, **"clipping"** means move back. Saved
lines are ticked, so you can stop and resume. For the noisy pass tick **Noisy pass** and **Load lines** again.
The first take asks for microphone permission: allow it. (The "Record mode" link in the main app opens the same
page without the record server; then each take downloads and you move it into `eval/audio/` yourself.)

## 3. How to read

1. **Say it the way you'd actually report it**, at normal speed. Don't read like a robot and don't slow down.
2. **Say exactly the words on the line.** Small natural hesitations ("uh") are fine; if you change or skip a
   word, record it again: the eval compares against the exact text.
3. **Numbers and times the natural way:** "Conference Room 2" → "conference room two", "10:30" → "ten thirty",
   "1:30" → "one thirty", "3 PM" → "three P M", "2nd floor" → "second floor", "UPS" → "U P S", "ER" → "E R".
4. **Short answers sound like answers.** Read the "context" column first: "it's medium" answers *How serious is
   it?*, so say it like a reply, not like a heading.
5. **Leave about half a second** of quiet after pressing start and before stopping.
6. **Don't correct the text** even if it's informal ("conf room", "today morning 10:30", "Sir one housekeeping
   staff…"). Those lines are informal on purpose.

## 4. The noisy pass

- Somewhere with real background noise: the canteen, a corridor with people talking, or play a "cafeteria
  ambience" or "office chatter" video on a phone about 1 m away, at normal conversation volume.
- Same laptop, same distance, same way of reading. Only the 10 lines marked 🔊 below.
- `eval/record_lines.sh <name> --noisy` saves them as `<name>-noisy` automatically.

## 5. When you're done

```
ls eval/audio/*_shubham.wav | wc -l          # must be 36
ls eval/audio/*_shubham-noisy.wav | wc -l    # must be 10
cd eval && zip audio_shubham.zip audio/*_shubham.wav audio/*_shubham-noisy.wav
```

Windows (PowerShell, from `ecellmegathon/`):

```
(Get-ChildItem eval\audio\*_sarthak.wav).Count           # must be 36
(Get-ChildItem eval\audio\*_sarthak-noisy.wav).Count     # must be 10
Compress-Archive -Path eval\audio\*_sarthak.wav, eval\audio\*_sarthak-noisy.wav -DestinationPath eval\audio_sarthak.zip
```

Share the zip in our shared drive. **Never commit `eval/audio/`** (it's gitignored): recordings of our voices
don't go on GitHub. Whoever runs the eval unzips everyone's files into `eval/audio/`.

## 6. Then the numbers

```
voice/laptop_up.sh                                   # STT + TTS on the laptop (or board/voice_up.sh on the QIDK)
.venv/bin/python eval/wer.py --label LAPTOP --subset --vocab data/vocab.txt   # WER + key-word errors (Track B)
LLM=llamaserver .venv/bin/python eval/run_eval.py --label LAPTOP --audio eval/audio   # typed vs spoken (Track A)
```

The STT model comparison (tiny / base / small, vocabulary prompt off and on) and the rule that picks the model,
written before any result: `eval/STT_DECISION.md`.

## 7. The script

**`eval/RECORDING_SCRIPT.md`**: all 36 lines, case by case (Case 1 … Case 25), each shown as a short
conversation with the assistant's question before your line, plus the noisy-pass list at the end. The terminal
recorder goes through the lines in the same order and shows the case number.
