"""
The incident form: the schema, the JSON schema the LLM must follow, and the
form state that model answers are merged into.

Source of truth: schema/incident_form.json (frozen; both tracks depend on it).
The LLM writes one-letter keys (t, s, l, ...) because every output token costs
decode time on the NPU; everything outside the LLM uses the full field names.

Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "incident_form.json"
CATALOGUE_PATH = ROOT / "data" / "followup_catalogue.json"

TOOLS = ("update_report", "submit_report", "cancel_report", "no_op")


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text())


def load_catalogue(path: Path = CATALOGUE_PATH) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def keyed_fields(schema: dict) -> list[dict]:
    """The fields the LLM may fill (those with a one-letter key), in schema order."""
    return [f for f in schema["fields"] if f.get("key")]


def llm_json_schema(schema: dict, only: list[str] | None = None) -> dict:
    """JSON schema for one LLM answer: a tool name and, for update_report, any
    subset of the keyed fields. `only` limits the args to those keys (for
    narrowing a turn to the field just asked; off in the basic model).
    llama-server compiles this into a grammar, so an enum field can only hold
    an allowed value and a text field can't run past its max length."""
    props = {}
    for f in keyed_fields(schema):
        if only is not None and f["key"] not in only:
            continue
        if f["kind"] == "enum":
            props[f["key"]] = {"type": "string", "enum": f["values"]}
        elif f["kind"] == "text":
            props[f["key"]] = {"type": "string", "minLength": 1, "maxLength": f["max_chars"]}
        elif f["kind"] == "text_list":
            props[f["key"]] = {"type": "array", "minItems": 1, "maxItems": f["max_items"],
                               "items": {"type": "string", "minLength": 1,
                                         "maxLength": f["max_chars"]}}
    return {"type": "object",
            "properties": {"tool": {"type": "string", "enum": list(TOOLS)},
                           "args": {"type": "object", "properties": props,
                                    "additionalProperties": False}},
            "required": ["tool"],
            "additionalProperties": False}


class FormState:
    """The report being filled. Values are stored under full field names."""

    def __init__(self, schema: dict, catalogue: dict | None = None):
        self.schema = schema
        self.catalogue = catalogue or {}
        self.by_key = {f["key"]: f for f in keyed_fields(schema)}
        self.values: dict = {}           # full name -> str | list[str]
        self.date_time: dict | None = None   # {"phrase", "iso", "resolved"} set by the dialog
        self.transcripts: list[str] = []

    # ---- writing ------------------------------------------------------------
    def add_transcript(self, text: str) -> None:
        if text.strip():
            self.transcripts.append(text.strip())

    def apply(self, args: dict) -> list[str]:
        """Merge LLM args (one-letter keys, already gated). A stated value
        overwrites (that's how corrections work); list items are appended
        without duplicates, up to max_items. Returns the full names changed."""
        changed = []
        for key, val in args.items():
            f = self.by_key.get(key)
            if f is None:
                continue
            name = f["name"]
            if f["kind"] == "text_list":
                cur = list(self.values.get(name, []))
                seen = {v.casefold() for v in cur}
                for item in val:
                    if item.casefold() not in seen and len(cur) < f["max_items"]:
                        cur.append(item)
                        seen.add(item.casefold())
                if cur != self.values.get(name, []):
                    self.values[name] = cur
                    changed.append(name)
            elif self.values.get(name) != val:
                self.values[name] = val
                changed.append(name)
        return changed

    def set_field(self, name: str, value) -> None:
        """Direct edit (the review screen). No gate: the user typed it."""
        f = next((f for f in self.schema["fields"] if f["name"] == name), None)
        if f is None or not f.get("key"):
            raise KeyError(f"not an editable field: {name}")
        if f["kind"] == "text_list" and isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if f["kind"] == "enum" and value not in f["values"]:
            raise ValueError(f"{name} must be one of {f['values']}")
        self.values[name] = value

    # ---- reading ------------------------------------------------------------
    @property
    def description(self) -> str:
        return " ".join(self.transcripts)

    @property
    def follow_up(self) -> list[str]:
        return list(self.catalogue.get(self.values.get("incident_type", ""), []))

    def missing(self) -> list[dict]:
        """Mandatory fields still empty, in schema order (the order we ask in)."""
        out = []
        for f in self.schema["fields"]:
            if not f.get("mandatory"):
                continue
            if f["name"] == "description":
                if not self.description:
                    out.append(f)
            elif not self.values.get(f["name"]):
                out.append(f)
        return out

    def compact(self) -> dict:
        """Filled fields under one-letter keys, for the prompt."""
        return {k: self.values[f["name"]] for k, f in self.by_key.items()
                if self.values.get(f["name"])}

    def public(self) -> dict:
        """The form as the UI and the saved report see it (full names)."""
        out = {}
        for f in self.schema["fields"]:
            n = f["name"]
            if n == "description":
                out[n] = self.description
            elif n == "follow_up":
                out[n] = self.follow_up
            elif n == "date_time":
                out[n] = self.date_time
            elif f["kind"] == "text_list":
                out[n] = list(self.values.get(n, []))
            else:
                out[n] = self.values.get(n)
        return out
