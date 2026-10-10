"""
M0 smoke test: is llama-server up, does it accept our JSON schema, what does
one real turn look like, and how long does it take?

  .venv/bin/python eval/smoke_llm.py --label LAPTOP                       # http://localhost:8080
  .venv/bin/python eval/smoke_llm.py --label BOARD --url http://localhost:8080

Sends the problem statement's example (ps-01 turn 1) and a short answer ("it's
medium", asked severity) through the real prompt and schema, three times each,
and prints the reply, whether it parses and passes the gate, and the server's
own timings. Then the same first turn WITHOUT the schema, to show what the
constraint changes. Numbers carry the label you pass; nothing is estimated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import gate  # noqa: E402
from app.backends import LlamaServerBackend  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.prompt import user_message  # noqa: E402

EXAMPLE = ("There was a water leak near Conference Room 2 around 10 AM. "
           "Maintenance has already isolated the area.")
TURNS = [({}, None, EXAMPLE),
         ({"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM"}, "s", "it's medium")]


def health(url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP"])
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--model", default="unknown", help="model file name, for the record")
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    if not health(a.url):
        print(f"STOP: no healthy llama-server at {a.url}")
        return 1
    backend = LlamaServerBackend(url=a.url, lane=a.label.lower(), model_file=a.model,
                                 thinking=False if "qwen3" in a.model.lower() else None)
    d = Dialog(backend)
    print(f"[{a.label}] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} · {a.url} · model {a.model} · "
          f"constraint {d.constraint} (LLM_CONSTRAINT)")
    rows = []
    for schema_on in (True, False):
        for form, asked, text in (TURNS if schema_on else TURNS[:1]):
            d.reset()
            d.form.apply(form)
            for k in range(a.repeat if schema_on else 1):
                r = backend.generate(user_message(d.form.compact(), asked, text), system=d.system,
                                     max_tokens=d.max_tokens, **(d.constrain() if schema_on else {}))
                call, err = d._parse(r.text) if r.status == "ok" else ({"tool": None}, r.error)
                kept, dropped = gate.check(dict(call.get("args") or {}), text)
                rows.append({"schema": schema_on, "turn": text[:30], "call": k + 1, "status": r.status,
                             "parses": not err, "dropped": dropped, "prefill_tokens": r.prefill_tokens,
                             "prefill_ms": r.prefill_ms, "decode_tokens": r.decode_tokens,
                             "decode_ms": r.decode_ms, "wall_ms": round(r.latency_ms),
                             "reply": r.text if r.status == "ok" else r.error})
    for x in rows:
        print(f"\n schema={'on ' if x['schema'] else 'off'} | {x['turn']!r} #{x['call']} | {x['status']} "
              f"parses={x['parses']} dropped={len(x['dropped'])}")
        print(f"   prefill {x['prefill_tokens']} tok / {x['prefill_ms']} ms · decode {x['decode_tokens']} tok / "
              f"{x['decode_ms']} ms · wall {x['wall_ms']} ms")
        print(f"   reply: {x['reply'][:300]}")
    ok = all(x["status"] == "ok" and x["parses"] for x in rows if x["schema"])
    print(f"\n[{a.label}] schema accepted and every constrained reply parses: {ok}")
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    path = out / f"smoke_{a.label}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"label": a.label, "url": a.url, "model": a.model, "rows": rows}, indent=1))
    print(f"saved {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
