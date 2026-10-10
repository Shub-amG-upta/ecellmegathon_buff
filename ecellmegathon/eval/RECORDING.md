# Recording the speech eval

For the three people recording: **Shubham, Sarthak, and one more person**. Each of you reads the same
**36 lines** once in a quiet room, then **10 of them** again with noise. About 10 minutes quiet + 5 minutes noisy
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

**B. The UI's record mode** (Sarthak is building it; also works on Windows): same lines, same file names.

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

Share the zip in our shared drive. **Never commit `eval/audio/`** (it's gitignored): recordings of our voices
don't go on GitHub. Whoever runs the eval unzips everyone's files into `eval/audio/`.

## 6. Then the numbers

```
voice/laptop_up.sh                                   # STT + TTS on the laptop (or board/voice_up.sh on the QIDK)
.venv/bin/python eval/wer.py --label LAPTOP          # word error rate (Track B)
LLM=llamaserver .venv/bin/python eval/run_eval.py --label LAPTOP --audio eval/audio   # typed vs spoken (Track A)
```

## 7. The script: all 36 lines

The file name is `<start>_<speaker>.wav`, e.g. `ps-01_t1_shubham.wav`. The terminal recorder and the UI name
files for you. 🔊 = also recorded in the noisy pass.

| # | file name starts with | noisy | context | say exactly this |
|---|---|---|---|---|
| 1 | `ps-01_t1` | 🔊 | starts the report | There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area. |
| 2 | `ps-01_t2` | 🔊 | answers *How serious is it: low, medium, high or critical?* | it's medium |
| 3 | `ps-01_t3` | 🔊 | at review / adds detail | submit |
| 4 | `ps-02_t1` | 🔊 | starts the report | water leaking near conf room 2 since 10 or so, maintenance guys have isolated it |
| 5 | `ps-03_t1` | 🔊 | starts the report | water leak near Conference Room 2 |
| 6 | `ps-03_t3` | 🔊 | answers *When did it happen?* | around 10 AM |
| 7 | `el-01_t1` | 🔊 | starts the report | This is to report a serious incident in the meter room on the ground floor around 3 PM today. Our electrical contractor's foreman was installing panels and got a shock from the 480 volt pull panel. The cover was off and one live wire was hanging loose. He was working alone, coworkers found him on the floor and called the ambulance. He has been taken to hospital. |
| 8 | `el-03_t3` | 🔊 | answers *When did it happen?* | 3 PM |
| 9 | `sl-01_t1` | 🔊 | starts the report | Around 10:30 this morning one of our staff slipped on the wet floor in the emergency room while preparing medical equipment. He hit his right arm on a metal cabinet, looks like his elbow and wrist are fractured. He has been admitted. I'd say severity high. |
| 10 | `sl-03_t2` | 🔊 | answers *When did it happen?* | today morning 10:30 |
| 11 | `na-01_t1` |  | starts the report | Last night around 8 PM a nursing aide tripped over the metal strip in the first floor shower room and hurt her ankle on the wet floor, it's fractured. She was showering a resident at the time. The floor is still wet and the strip is still loose. |
| 12 | `fk-01_t1` |  | starts the report | At about 2 in the afternoon in the warehouse, a forklift driver hit the stacked material, got down to straighten it and the load fell on his chest. He couldn't move it. We called an ambulance and he's being taken to hospital. This is critical. |
| 13 | `fk-03_t1` |  | starts the report | Material fell from the rack in warehouse aisle 4 and hit the forklift, no one hurt |
| 14 | `fk-03_t2` |  | answers *When did it happen?* | quarter past two |
| 15 | `lw-01_t1` |  | starts the report | There's water leaking from a valve in the basement parking driveway. Our maintenance mechanic went up the extension ladder at 11:30 to shut it, the ladder slipped in the water and he fell onto the concrete. Both his arms look broken. The water is still leaking. |
| 16 | `lw-03_t1` |  | starts the report | I saw the maintenance guy fall off the ladder at the parking ramp, me and Ravi both saw it, he was trying to shut the water valve |
| 17 | `wit-01_t1` |  | starts the report | A box fell off the top shelf in the store room, it just missed a delivery boy. Anil and I both saw it. |
| 18 | `wit-02_t1` |  | starts the report | There's oil spilled near the generator room entrance. The guard Mahesh saw the drum tip over around 1 PM. |
| 19 | `wit-03_t4` |  | at review / adds detail | Priya from the lab saw the sparks |
| 20 | `wit-05_t1` |  | starts the report | Two cars scraped each other in the basement parking around 6 PM yesterday. Ravi, Sunita and the parking attendant saw it. |
| 21 | `wit-09_t1` |  | starts the report | A man fainted in the canteen queue around 1:30, Deepak and Farah saw him fall |
| 22 | `sl-03_t1` |  | starts the report | Sir one housekeeping staff fell down in the ER corridor, floor was wet after mopping, she is having pain in wrist |
| 23 | `cl-03_t1` |  | starts the report | False ceiling has fallen down in the conference hall on 2nd floor, luckily meeting was not going on. Light fittings broken. |
| 24 | `cv-02_t2` |  | answers *When did it happen?* | sorry, it's fine actually, maintenance already fixed it, cancel this |
| 25 | `fi-03_t4` |  | at review / adds detail | sorry, it's the UPS room, not the server room |
| 26 | `ps-02_t2` |  | answers *How serious is it: low, medium, high or critical?* | medium |
| 27 | `ps-02_t3` |  | at review / adds detail | yes submit it |
| 28 | `el-03_t1` |  | starts the report | electrical problem |
| 29 | `el-03_t2` |  | answers *Where did it happen?* | meter room |
| 30 | `el-03_t4` |  | answers *How serious is it: low, medium, high or critical?* | high |
| 31 | `na-02_t2` |  | answers *How serious is it: low, medium, high or critical?* | low |
| 32 | `lw-03_t2` |  | answers *When did it happen?* | 11:30 |
| 33 | `ld-01_t1` |  | starts the report | A carpenter fell from the thirty foot extension ladder near the east wall about twenty minutes ago. Someone moved the base while the top was tied, then he cut the tie wires and the top section came down. He is conscious, we have called the ambulance and informed the site supervisor. |
| 34 | `ch-01_t1` |  | starts the report | Reporting a chemical splash at the tanker wash bay around 9 AM. The manager was checking the vapour cleaning, he pressurised the internal valve before closing the outer one and the solvent splashed into his eyes. He wasn't wearing goggles. We flushed his eyes at the eyewash and he has gone to the clinic. Medium severity. |
| 35 | `cv-01_t1` |  | starts the report | One of the mechanics got his finger caught in the conveyor chain while taking tyres off. The fingertip is cut off. It happened at about 1:45 at the tyre line. We stopped the conveyor and he is on the way to hospital. Critical. |
| 36 | `fi-01_t1` |  | starts the report | Fire on the fourth floor, in the software company office, started maybe ten minutes back. Looks like a short circuit. We have evacuated everyone and the fire brigade is on the way. Nobody is hurt. |
