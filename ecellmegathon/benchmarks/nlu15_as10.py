"""Usage: python benchmarks/nlu15_as10.py eval/nlu15/cases.jsonl eval/nlu/cases.jsonl results/nlu15_as10.jsonl
Project eval/nlu15 (15 fields) onto the 10-field form v6 supports: drop g/c/k/h/n everywhere;
a turn left with no args becomes no_op. Also checks the 65 copied dialogs keep their 10-field labels."""
import json, sys
NEW = {"g", "c", "k", "h", "n"}
src, orig, out = sys.argv[1:4]
O = {c["id"]: c for c in map(json.loads, open(orig, encoding="utf-8"))}
strip = lambda d: {k: v for k, v in (d or {}).items() if k not in NEW}
rows, changed_tool, same, diff = [], [], 0, []
for c in map(json.loads, open(src, encoding="utf-8")):
    for t in c["turns"]:
        t["form_before"] = strip(t["form_before"])
        g = t["gold"]; g["args"] = strip(g.get("args"))
        if g["tool"] == "update_report" and not g["args"]:
            g["tool"] = "no_op"; changed_tool.append((c["id"], t["i"]))
    c["gold_final"] = strip(c["gold_final"]); c["accept"] = strip(c.get("accept"))
    if c["id"] in O:
        o = O[c["id"]]
        if [ (t["user"], t["gold"]) for t in o["turns"]] == [(t["user"], t["gold"]) for t in c["turns"]] and o["gold_final"] == c["gold_final"]: same += 1
        else: diff.append(c["id"])
    rows.append(c)
open(out, "w", encoding="utf-8").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
print(f"{len(rows)} dialogs written; copied dialogs with identical 10-field labels: {same}, different: {diff}")
print("turns turned into no_op (only new-field content):", changed_tool)
