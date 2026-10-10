# ecellmegathon: AI Incident Report Assistant

Megathon X (E-Cell, IIIT Hyderabad), Qualcomm Problem Statement 1.

Voice in, structured incident report out. Speech-to-text, a grammar-constrained LLM tool call, and text-to-speech,
all running on the Qualcomm QIDK (Snapdragon 8 Gen 3). The LLM runs on the Hexagon NPU. No cloud APIs.

    speak → understand → extract → review → submit

- **[BUILD_PLAN.md](BUILD_PLAN.md)**: the shared build spec: architecture, contracts between the two tracks, who
  owns which file, milestones, QIDK test checklist. Start here
- **[PLAN.md](PLAN.md)**: background: the basic model, the STT contract, who does what, build order, risks
- **[schema/incident_form.json](schema/incident_form.json)**: the form, the mandatory fields and the tool set
- `app/`: the agent core (stdlib only). `backends.py` (llama-server client) and `grammar.py` are ported from FieldMind
- `board/`: QIDK bring-up (`board.py`), NPU placement proof (`npu_proof.py`), pure-Q4_0 model build
  (`build_candidate_gguf.sh`, `gguf_types.py`), all ported from FieldMind
- `docs/reference/`: FieldMind's on-device stack, build recipe, key numbers and research record

```
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q                 # 20 tests, no board needed
.venv/bin/python -m board.board               # board commands
```
