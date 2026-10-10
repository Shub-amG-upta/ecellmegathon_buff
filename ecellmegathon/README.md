# ecellmegathon: AI Incident Report Assistant

Megathon X (E-Cell, IIIT Hyderabad), Qualcomm Problem Statement 1.

Voice in, structured incident report out. Speech-to-text, a grammar-constrained LLM tool call, and text-to-speech,
all running on the Qualcomm QIDK (Snapdragon 8 Gen 3). The LLM runs on the Hexagon NPU. No cloud APIs.

    speak → understand → extract → review → submit

- **[PLAN.md](PLAN.md)**: architecture, model choices, build plan, risks
- **[schema/incident_form.json](schema/incident_form.json)**: the form, the mandatory fields and the tool set
- On-device stack inherited from FieldMind (`../field-mind`): the llama.cpp Hexagon build, the pure-Q4_0 recipe,
  and per-request GBNF grammars
