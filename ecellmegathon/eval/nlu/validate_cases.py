#!/usr/bin/env python3
"""Checks cases.jsonl against the form schema and prints coverage + a run-time estimate. Stdlib only.

usage: python validate_cases.py [cases.jsonl] [schema.json]
Exit code 1 if any check fails.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "cases.jsonl"
SCHEMA = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parents[1] / "schema" / "incident_form.json"
TOOLS = {"update_report", "submit_report", "cancel_report", "no_op"}

# BOARD screening numbers from the early board screening (700-token prompt). Grammar-constrained decode may be slower.
MODELS = {"Llama 3.2 3B": (986, 21), "Qwen3 1.7B": (1432, 32)}
SYSTEM_PROMPT_TOKENS = 600    # ESTIMATE: 8 LLM fields + 15 types + rules + 3 examples (app/prompt.py)
CALL_OVERHEAD_S = 0.3         # ESTIMATE: HTTP + grammar setup + code


def load_fields(schema: dict) -> dict:
    """The LLM-filled fields keyed by their one-letter key, from the app's form
    layout (schema/incident_form.json: a list of fields with key/kind/max_chars)."""
    out = {}
    for f in schema["fields"]:
        if not f.get("key"):
            continue                                   # description, follow_up: built by code
        d = {"name": f["name"], "mandatory": f.get("mandatory", False)}
        if f["kind"] == "enum":
            d.update(type="enum", values=f["values"])
        elif f["kind"] == "text":
            d.update(type="string", maxLength=f["max_chars"])
        elif f["kind"] == "text_list":
            d.update(type="array", maxItems=f["max_items"], item_maxLength=f["max_chars"])
        out[f["key"]] = d
    return out


def main():
    schema = json.loads(SCHEMA.read_text())
    fields = load_fields(schema)
    mandatory = [k for k, v in fields.items() if v["mandatory"]]
    errors = []
    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]

    ids = Counter(c["id"] for c in cases)
    errors += [f"duplicate id {i}" for i, n in ids.items() if n > 1]

    def check_value(cid, ti, k, v, text, extractive=True):
        f = fields.get(k)
        if f is None:
            return [f"{cid} t{ti}: unknown field '{k}'"]
        e = []
        if f["type"] == "enum":
            if v not in f["values"]:
                e.append(f"{cid} t{ti}: {k}={v!r} not in enum")
        elif f["type"] == "integer":
            if not isinstance(v, int) or not f["minimum"] <= v <= f["maximum"]:
                e.append(f"{cid} t{ti}: {k}={v!r} not a valid integer")
        elif f["type"] == "string":
            if not isinstance(v, str) or len(v) > f["maxLength"]:
                e.append(f"{cid} t{ti}: {k} bad string or too long")
            elif extractive and v.lower() not in text.lower():
                e.append(f"{cid} t{ti}: {k}={v!r} is not copied from the user's words")
        elif f["type"] == "array":
            if not isinstance(v, list) or len(v) > f["maxItems"]:
                e.append(f"{cid} t{ti}: {k} bad list or too many items")
            else:
                for item in v:
                    if len(item) > f["item_maxLength"]:
                        e.append(f"{cid} t{ti}: {k} item too long: {item!r}")
                    if extractive and item.lower() not in text.lower():
                        e.append(f"{cid} t{ti}: {k} item {item!r} is not copied from the user's words")
        return e

    field_use, type_use, tags, splits, srcs, tool_use = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    turns_total = 0
    for c in cases:
        cid = c["id"]
        splits[c["split"]] += 1
        srcs[c["source"]] += 1
        tags.update(c["tags"])
        for t in c["turns"]:
            turns_total += 1
            g = t["gold"]
            tool_use[g["tool"]] += 1
            if g["tool"] not in TOOLS:
                errors.append(f"{cid} t{t['i']}: unknown tool {g['tool']}")
            if g["tool"] != "update_report" and g["args"]:
                errors.append(f"{cid} t{t['i']}: {g['tool']} must have empty args")
            if g["tool"] == "update_report" and not g["args"]:
                errors.append(f"{cid} t{t['i']}: update_report with no args (use no_op)")
            for k, v in g["args"].items():
                errors += check_value(cid, t["i"], k, v, t["user"])
                field_use[k] += 1
            if t["asked"] is not None and t["asked"] not in fields:
                errors.append(f"{cid} t{t['i']}: asked={t['asked']} is not a field")
        for k, alts in c["accept"].items():
            for a in alts:   # list fields: each alternative is one acceptable item
                v = [a] if fields.get(k, {}).get("type") == "array" else a
                errors += check_value(cid, "accept", k, v, "", extractive=False)
        if c["final_tool"] not in ("submit_report", "cancel_report"):
            errors.append(f"{cid}: dialog must end with submit_report or cancel_report")
        if c["final_tool"] == "submit_report":
            missing = [k for k in mandatory if k not in c["gold_final"]]
            if missing:
                errors.append(f"{cid}: submitted with mandatory fields missing: {missing}")
        if "t" in c["gold_final"]:
            type_use[c["gold_final"]["t"]] += 1

    print(f"cases: {len(cases)}   turns: {turns_total}   splits: {dict(splits)}")
    print(f"tools: {dict(tool_use)}")
    print(f"sources: {len(srcs)} codes, grounded cases: {sum(n for s, n in srcs.items() if s not in ('H',))}"
          f" / {len(cases)} (H = hand-written)")
    print("\nincident types (final form):")
    for v in fields["t"]["values"]:
        print(f"  {v:18s} {type_use.get(v, 0)}")
    print(f"\nfield fills across all turns ({len(fields)} LLM fields):")
    for k in fields:
        print(f"  {k} {fields[k]['name']:24s} {field_use.get(k, 0)}")
    unused = [k for k in fields if field_use.get(k, 0) == 0]
    if unused:
        errors.append(f"fields never exercised: {unused}")
    print("\ntags:", ", ".join(f"{k}:{n}" for k, n in tags.most_common()))

    # ---- run-time estimate (ESTIMATE, from BOARD screening rates; replace with M0 measurements)
    print("\nrun-time ESTIMATE (one pass = every turn sent to the LLM once):")
    max_out = 0
    for name, (pf, dc) in MODELS.items():
        for cached in (True, False):
            secs = 0.0
            for c in cases:
                for t in c["turns"]:
                    out_tok = math.ceil(len(json.dumps(t["gold"], separators=(",", ":"))) / 3.2) + 2
                    max_out = max(max_out, out_tok)
                    user_tok = 30 + len(json.dumps(t["form_before"], separators=(",", ":"))) / 3.5 + len(t["user"]) / 4
                    in_tok = user_tok + (0 if cached else SYSTEM_PROMPT_TOKENS)
                    secs += in_tok / pf + out_tok / dc + CALL_OVERHEAD_S
            label = "system prompt cached" if cached else "no prompt cache"
            print(f"  {name:13s} {label:21s} one pass {secs/60:5.1f} min · teacher-forced + free-running "
                  f"{2*secs/60:5.1f} min · both x2 (drift) {4*secs/60:5.1f} min")
    print(f"  longest gold output ≈ {max_out} tokens → set n_predict ≥ {max_out + 40}")

    if errors:
        print(f"\n{len(errors)} ERRORS")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
