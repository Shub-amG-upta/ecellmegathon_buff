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
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "incident_form.json"
# FORM=15 (env) loads the 15-field form instead: the 10 fields above, unchanged, plus 5 optional
# industry-standard context fields (task, immediate_cause, property_damage, hazard_status, reported_to;
# eval/nlu15/LABELING_15.md). Opt-in: with FORM unset or 10 nothing changes.
SCHEMA_PATHS = {"10": SCHEMA_PATH, "15": ROOT / "schema" / "incident_form_15.json"}
CATALOGUE_PATH = ROOT / "data" / "followup_catalogue.json"

TOOLS = ("update_report", "submit_report", "cancel_report", "no_op")

# v6, compact answers (ANSWER=compact): the model writes only the fields object for an update, or one letter
# for the other tools. Half of a full answer, {"tool":"update_report","args":{...}}, was boilerplate the
# grammar forces anyway, and every forced token still costs a decode step on the NPU. Code turns a compact
# answer back into the same tool call (to_tool_call), so everything after the model is unchanged.
COMPACT_CODES = {"S": "submit_report", "C": "cancel_report", "N": "no_op"}
DEFAULT_ANSWER = "full"                                  # compact (v6) failed its board rule (DECISION_v6.md)


def answer_mode() -> str:
    m = os.environ.get("ANSWER", DEFAULT_ANSWER)
    if m not in ("full", "compact"):
        raise ValueError(f"ANSWER must be full or compact, not {m!r}")
    return m


def to_tool_call(answer) -> dict | None:
    """A parsed model answer, full or compact, as a tool call {"tool", "args"?}; None if it is neither."""
    if isinstance(answer, dict):
        if "tool" in answer:                                     # full form
            return answer if answer.get("tool") in TOOLS else None
        return {"tool": "update_report", "args": answer}         # compact: the fields object
    if isinstance(answer, str) and answer in COMPACT_CODES:
        return {"tool": COMPACT_CODES[answer]}
    return None


def to_compact(call: dict) -> str:
    """A tool call written the compact way (for prompt examples and tests)."""
    if call.get("tool") == "update_report" and call.get("args"):
        return json.dumps(call["args"], ensure_ascii=False, separators=(",", ":"))
    code = {v: k for k, v in COMPACT_CODES.items()}.get(call.get("tool"), "N")
    return json.dumps(code)


def form_version() -> str:
    v = os.environ.get("FORM", "10")
    if v not in SCHEMA_PATHS:
        raise ValueError(f"FORM must be 10 or 15, not {v!r}")
    return v


def load_schema(path: Path | None = None) -> dict:
    return json.loads(Path(path or SCHEMA_PATHS[form_version()]).read_text(encoding="utf-8"))


def load_catalogue(path: Path = CATALOGUE_PATH) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def keyed_fields(schema: dict) -> list[dict]:
    """The fields the LLM may fill (those with a one-letter key), in schema order."""
    return [f for f in schema["fields"] if f.get("key")]


def llm_json_schema(schema: dict, only: list[str] | None = None, compact: bool = False) -> dict:
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
    if compact:                                                  # v6: the fields object, or S / C / N
        return {"anyOf": [{"type": "object", "properties": props, "additionalProperties": False},
                          {"type": "string", "enum": list(COMPACT_CODES)}]}
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
        """Mandatory fields still empty, in the schema's `ask_order` (the order we
        ask in), then the description."""
        order = self.schema.get("ask_order", [])
        keyed = sorted((f for f in self.schema["fields"] if f.get("mandatory") and f.get("key")),
                       key=lambda f: order.index(f["key"]) if f["key"] in order else len(order))
        out = [f for f in keyed if not self.values.get(f["name"])]
        out += [f for f in self.schema["fields"]
                if f.get("mandatory") and f["name"] == "description" and not self.description]
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
