"""The 15-field eval set: labels pass the builder's checks and cases.jsonl is up to date."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_cases15", ROOT / "eval" / "nlu15" / "build_cases15.py")
B15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B15)


def _build():
    B15.B10.LIST_FIELDS = B15.LIST_FIELDS15
    return [B15.B10.expand(B15.with_extra(c)) for c in B15.B10.CASES] + [B15.B10.expand(c) for c in B15.NEW]


def test_checks_pass():
    originals = {}
    for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        oc = json.loads(ln)
        for t in oc["turns"]:
            originals[(oc["id"], t["i"])] = t["gold"]
    assert B15.check(_build(), originals) == []


def test_jsonl_matches_builder():
    built = [json.loads(json.dumps(c, ensure_ascii=False)) for c in _build()]
    stored = [json.loads(ln) for ln in
              (ROOT / "eval" / "nlu15" / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert built == stored
