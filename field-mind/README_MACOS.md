# FieldMind on macOS (Apple Silicon host + QIDK over USB)

Setup and operations guide for running FieldMind from a MacBook Pro against the
Qualcomm QIDK HDK8650. The main [README.md](README.md) covers what the system
*is*; this one covers getting it running on a Mac and the Mac-specific traps.

Assumes: Apple Silicon, Homebrew, zsh. Verified paths are marked; anything not
yet run on hardware is marked **UNVERIFIED**.

---

## 1. What works on a Mac and what does not

| path | host | status |
|---|---|---|
| `--backend mock` | Mac alone | works, no device needed |
| `--backend gemini` | Mac alone | works, needs an API key |
| `--backend litert --accelerator cpu` | Mac + QIDK over USB | works |
| `--backend litert --accelerator gpu` | Mac + QIDK over USB | works |
| `--backend litert --accelerator npu` | Mac + QIDK | **blocked on Mac** |
| Genie / QNN model conversion | — | **blocked on Mac** |
| Quantization sweeps | — | **blocked on Mac** |

**The blocker:** the Qualcomm AI Runtime SDK (QAIRT/QNN) ships for Linux and
Windows only. There is no macOS build. Everything that requires the SDK's
*host-side* tooling — `qnn-model-lib-generator`, `qairt-converter`, the
`split_llm()` prefill/decode graph split, quantization sweeps — cannot run
here at all, not even under Rosetta.

This blocks *producing* NPU artefacts. It does not block *running* them: the
`.so` libraries and compiled graphs live on the device, and `adb` drives them
fine from a Mac. So the practical split is:

- **Mac** — all agent development, mock and Gemini benchmarks, CPU and GPU
  device runs, all analysis.
- **A Linux box** (lab machine, or Gagan's Fedora host) — one-time model
  conversion and any quantization work. Copy the resulting artefacts back and
  push them from the Mac.

Plan around this rather than fighting it. It is a tooling constraint, not a
project blocker — the CPU and GPU baselines are on the critical path anyway and
are fully reachable from here.

---

## 2. Host setup

```zsh
brew install python@3.12
brew install --cask android-platform-tools     # adb
```

Homebrew Python refuses `pip install` into the system environment (PEP 668,
`error: externally-managed-environment`). Use a venv — do not reach for
`--break-system-packages` on your own machine:

```zsh
cd fieldmind-boiler
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` is one line (PyYAML). The agent core is stdlib-only on
purpose, so there is no scientific stack to fight with on arm64.

Verify:

```zsh
python -m data.generator.episode_build --out data/episodes
python bench/test_checks.py          # expect 18 passed, 0 failed
python run_demo.py --all             # ~2 min, writes results/summary_mock.json
```

For the Gemini backend, put the key in your shell profile rather than in the
config file:

```zsh
echo 'export GOOGLE_API_KEY="..."' >> ~/.zshrc && source ~/.zshrc
python run_demo.py --all --backend gemini
```

---

## 3. Connecting the QIDK

```zsh
adb devices
```

Unlike Linux there are **no udev rules to write** — macOS needs no permission
setup for adb. If the board does not appear:

1. Check the cable carries data, not just power. This is the most common cause.
2. `adb kill-server && adb start-server`.
3. Confirm USB debugging is enabled on the board and the RSA fingerprint
   prompt has been accepted on its display.
4. If several devices are attached, set `llm.litert.adb_serial` in
   `configs/base.yaml` — otherwise adb picks one arbitrarily and the results
   are not attributable to a specific board.

Then, before trusting any device number:

```zsh
python bench/probe_device.py
```

This checks the binary and model are present, that
`libQnnHtpV75Skel.so` / `libQnnHtpV75Stub.so` are there, that the flags are
accepted, and that the timing output parses. A wrong flag does **not** error —
`litert_lm_main` falls back to CPU and returns a plausible, wrong number.

---

## 4. Mac-specific traps

These cost real time. All of them are silent failures.

### zsh expands globs before adb sees them

```zsh
adb shell ls /data/local/tmp/llm/*.so        # zsh: no matches found
adb shell "ls /data/local/tmp/llm/*.so"      # correct
```

zsh tries to match the pattern against your *Mac* filesystem, finds nothing,
and aborts the command before adb runs. bash would have passed it through.
Quote every remote path containing `*`, `?` or `[`.

### Prompts through `adb shell` cross two shells

A prompt with quotes, newlines, `$`, or backticks is mangled by your local zsh
and then again by the device's shell. The corruption is silent — you get a
reply, just to a different prompt than you sent.

Write the prompt to a file, push it, and point the binary at the file:

```zsh
printf '%s' "$PROMPT" > /tmp/_p.txt
adb push /tmp/_p.txt /data/local/tmp/llm/_p.txt
adb shell "cd /data/local/tmp/llm && \
  LD_LIBRARY_PATH=/data/local/tmp/llm ADSP_LIBRARY_PATH=/data/local/tmp/llm \
  ./litert_lm_main --model_path=... --input_prompt_file=/data/local/tmp/llm/_p.txt"
```

`LiteRTBackend` already does exactly this. The rule matters when you are
debugging by hand outside the harness.

### `LD_LIBRARY_PATH` must be set in every single invocation

It does not persist between `adb shell` calls. Omitting it does not produce a
link error — the binary loads whatever it can find and quietly runs a different
backend than you asked for.

### Downloads truncate silently

`wget` without `-c` will exit 0 on a partial file. SDK archives are large
enough that this happens regularly:

```zsh
wget -c --tries=10 --timeout=30 <url>
unzip -t <file>.zip                          # always verify before trusting it
```

### Gatekeeper quarantines downloaded binaries

Anything fetched with a browser gets a quarantine attribute and dies with a
vague "cannot be opened" dialog:

```zsh
xattr -d com.apple.quarantine <file>
```

Files fetched with `wget` or `curl` are not quarantined, which is one more
reason to use them.

### APFS is case-insensitive by default

Android's filesystem is not. Two files differing only in case will collide on
the Mac and one will silently overwrite the other before you ever push them.
Rare, but it presents as a corrupted artefact with no error.

### `.DS_Store` files end up in archives

Finder writes them into any directory it displays. Exclude them when zipping:

```zsh
zip -qr fieldmind.zip fieldmind-boiler -x "*.DS_Store" -x "*__pycache__*"
```

---

## 5. Measurement hygiene on this setup

Device numbers taken from a laptop-driven board are not repeatable unless the
conditions are recorded with them. Log alongside every run:

- **Thermal state.** Read the thermal zones before and after
  (`bench/probe_device.py` prints them). A hot board throttles and the second
  run of an identical config will be slower than the first. Insert a cooldown
  between configurations rather than pretending it does not happen.
- **Which accelerator actually ran**, not which one you requested. The probe is
  the only thing standing between you and reporting CPU numbers as NPU numbers.
- **Prompt length.** An earlier CPU run used 10 tokens, which is far too short
  to say anything about throughput — it measures startup, not decode. Use
  realistic FieldMind prompts (150–300 tokens of evidence packet) for anything
  that goes in the writeup.
- **Do not let your Mac sleep mid-sweep.** `caffeinate -i python run_demo.py
  --all --backend litert` keeps it awake for the duration; a sleep in the
  middle drops the adb connection and truncates the run.

---

## 6. Suggested working order from here

1. `python bench/probe_device.py` — confirm flags and HTP libraries.
2. CPU baseline over all 30 episodes with realistic prompt lengths.
3. GPU run, same protocol, same thermal handling.
4. `--backend gemini` on all 30 to measure what the LLM adds over the
   deterministic baseline. This is the number the project turns on, and it
   needs no device at all.
5. Hand the NPU conversion to a Linux host; push the artefacts back and run
   them from here.
