"""
Type turns in the terminal; see the tool call, the form and the next question.

  .venv/bin/python -m app.cli                                   # mock LLM
  LLM=llamaserver LLM_URL=http://localhost:8080 .venv/bin/python -m app.cli

Commands: /reset  /form  /quit
"""
from __future__ import annotations

import json

from app.dialog import Dialog
from app.server import make_backend


def _show(r: dict) -> None:
    if r.get("tool_call"):
        print("  tool   ", json.dumps(r["tool_call"], ensure_ascii=False))
    for d in r.get("dropped", []):
        print("  dropped", d["field"], repr(d["value"]), d["reason"])
    t = r["timings_ms"]
    if t.get("llm_wall") is not None:
        def ms(x):
            return "n/a" if x is None else f"{x:.0f}"
        print(f"  timing  prefill {ms(t['llm_prefill'])} ms · decode {ms(t['llm_decode'])} ms · "
              f"llm wall {ms(t['llm_wall'])} ms · on {r['devices']['llm']}")
    if r.get("error"):
        print("  error  ", r["error"])
    print(f"[{r['state']}] assistant: {r['say']}")


def main() -> None:
    d = Dialog(make_backend())
    _show(d.last)
    while True:
        try:
            text = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.strip() == "/quit":
            return
        if text.strip() == "/reset":
            _show(d.reset())
        elif text.strip() == "/form":
            print(json.dumps(d.form.public(), indent=1, ensure_ascii=False))
        else:
            _show(d.turn(text))


if __name__ == "__main__":
    main()
