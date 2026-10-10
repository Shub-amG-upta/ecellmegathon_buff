"""Saved reports: reports_out/<id>.json (BUILD_PLAN §4.4). Stdlib only."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.form import ROOT

REPORT_DIR = Path(os.environ.get("REPORT_DIR", ROOT / "reports_out"))


def next_id(report_dir: Path = REPORT_DIR) -> str:
    report_dir.mkdir(parents=True, exist_ok=True)
    n = [int(p.stem.split("-")[1]) for p in report_dir.glob("IR-*.json")
         if p.stem.split("-")[1].isdigit()]
    return f"IR-{(max(n) + 1) if n else 1:04d}"


def save(report: dict, report_dir: Path = REPORT_DIR) -> str:
    """Write the report under a new id; returns the id. Never overwrites."""
    rid = next_id(report_dir)
    path = report_dir / f"{rid}.json"
    with open(path, "x") as fh:                       # "x": fail rather than overwrite
        json.dump({"id": rid, **report}, fh, indent=1, ensure_ascii=False)
    return rid


def list_reports(report_dir: Path = REPORT_DIR) -> list[dict]:
    out = []
    for p in sorted(report_dir.glob("IR-*.json")):
        r = json.loads(p.read_text())
        out.append({"id": r["id"], "incident_type": r["form"].get("incident_type"),
                    "location": r["form"].get("location"),
                    "submitted_at": r.get("submitted_at")})
    return out


def load(rid: str, report_dir: Path = REPORT_DIR) -> dict | None:
    p = report_dir / f"{Path(rid).name}.json"         # .name: no path tricks via the id
    return json.loads(p.read_text()) if p.exists() else None
