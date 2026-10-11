"""
The turn loop: one LLM call per user turn, everything else is code.

  text → prompt → LLM (JSON-schema constrained) → tool call
       → gate (drop what the user didn't say) → merge → time → severity floor
       → mandatory check → ask the next question, or read back, or submit

Every sentence the assistant says comes from a template here; the model never
writes user-facing text. States: collecting → review → submitted | cancelled.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from app import fastpath, gate, postfix, report
from app.form import FormState, answer_mode, llm_json_schema, load_catalogue, load_schema, to_tool_call
from app.grammar import UPDATE_PREFIX, incident_gbnf
from app.prompt import system_prompt, user_message
from app.timeparse import resolve

GREETING = "What happened? Tell me in your own words."
REVIEW_TAIL = "Say submit, or tell me what to change."

_TYPE_SAID = {"fire": "Fire", "electrical": "Electrical incident", "water_leak": "Water leak",
              "gas_leak": "Gas leak", "chemical": "Chemical incident", "slip_trip": "Slip or trip",
              "fall_height": "Fall from height", "struck_by": "Struck by an object",
              "caught_in": "Caught in machinery", "equipment_failure": "Equipment failure",
              "lift": "Lift incident", "structural": "Structural collapse", "medical": "Medical emergency",
              "security": "Security incident", "other": "Other incident"}
_FIELD_SAID = {"incident_type": "type of incident", "severity": "severity", "location": "location",
               "date_time": "time it happened", "description": "description"}


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


class Dialog:
    def __init__(self, backend, schema: dict | None = None, catalogue: dict | None = None,
                 clock=_now, report_dir=None, max_tokens: int = 128, use_fast_path: bool = True,
                 constraint: str | None = None, answer: str | None = None):
        self.backend = backend
        self.schema = schema or load_schema()
        self.catalogue = load_catalogue() if catalogue is None else catalogue
        self.clock = clock
        self.report_dir = report_dir or report.REPORT_DIR
        self.max_tokens = max_tokens
        # how the model writes its answer: "full" tool call, or "compact" (v6: fields object or S/C/N)
        self.answer = answer or answer_mode()
        if self.answer not in ("full", "compact"):
            raise ValueError(f"answer must be full or compact, not {self.answer!r}")
        self.system = system_prompt(self.schema, self.answer)
        self.json_schema = llm_json_schema(self.schema, compact=self.answer == "compact")
        # how the answer is constrained: "schema" (llama-server compiles the JSON schema; default) or
        # "gbnf" (our own grammar, no whitespace: fewer decode tokens). LLM_CONSTRAINT picks it.
        self.constraint = constraint or os.environ.get("LLM_CONSTRAINT", "schema")
        if self.constraint not in ("schema", "gbnf"):
            raise ValueError(f"LLM_CONSTRAINT must be schema or gbnf, not {self.constraint!r}")
        self.gbnf = incident_gbnf(self.schema, compact=self.answer == "compact")
        # answer pre-fill (PREFILL=on): the fixed start {"tool":"update_report","args":{ goes into the prompt and
        # the model writes only the fields; skipped for compact answers and for turns that mention submit/cancel
        self.prefill = os.environ.get("PREFILL", "off") == "on" and self.answer == "full"
        self.gbnf_after_prefix = incident_gbnf(self.schema, after_prefix=True)
        self.type_values = next(f for f in self.schema["fields"] if f.get("key") == "t")["values"]
        self.use_fast_path = use_fast_path
        self.turn_id = 0
        self.last: dict = {}
        self.reset()

    # ---- public API (server.py and cli.py call these) -----------------------
    def reset(self) -> dict:
        self.form = FormState(self.schema, self.catalogue)
        self.state = "collecting"
        self.asked: str | None = None
        self.heard: list[str] = []            # everything said in this report (for the gate)
        self.turns: list[dict] = []           # saved with the report
        self.report_id: str | None = None
        # what answered this report's turns, saved with it (the submit turn itself is usually code)
        self.used = {"llm": None, "llm_device": None, "type_model": None, "llm_calls": 0, "fast_path_turns": 0}
        return self._result(transcript="", say=GREETING)

    def turn(self, text: str) -> dict:
        t0 = time.perf_counter()
        if self.state in ("submitted", "cancelled"):
            self.reset()                      # speaking after a finished report starts a new one
        text = (text or "").strip()
        if not text:
            return self._result(transcript="", say="Sorry, I didn't catch that. " + self._prompt_line(),
                                t0=t0)

        self.heard.append(text)
        fast = (fastpath.answer(text, self.asked, self.state, self.type_values)
                if self.use_fast_path else None)
        if fast is not None:                   # answered by code: no LLM call, no LLM timings
            call, err, llm = dict(fast), "", {"fast_path": True}
            self.used["fast_path_turns"] += 1
        else:
            reply = self.call_llm(user_message(self.form.compact(), self.asked, text), text)
            llm = {"llm_prefill": reply.prefill_ms, "llm_decode": reply.decode_ms,
                   "llm_wall": reply.latency_ms, "device": reply.backend, "model": reply.model,
                   "prefill_tokens": reply.prefill_tokens, "decode_tokens": reply.decode_tokens}
            if reply.status != "ok":
                self.heard.pop()
                return self._result(transcript=text, llm=llm, t0=t0, error=reply.error or reply.status,
                                    say="Sorry, I had trouble with that. Could you say it again?")
            call, err = self._parse(reply.text)
            self.used.update(llm=reply.model, llm_device=reply.backend, llm_calls=self.used["llm_calls"] + 1)
            for part in reply.parts or []:            # the v7 type router's model
                self.used["type_model"] = part.get("model") or self.used["type_model"]
        tool, args = call["tool"], call.get("args") or {}

        if tool == "cancel_report":
            self.state = "cancelled"
            return self._finish_turn(text, call, [], llm, t0, err,
                                     "Okay, I've discarded this report.")

        if tool == "submit_report":
            missing = self.form.missing()
            if missing:
                say = f"I still need the {_FIELD_SAID[missing[0]['name']]}. " + self._ask(missing)
                return self._finish_turn(text, call, [], llm, t0, err, say)
            return self._submit(text, call, llm, t0, err)

        dropped = []
        if tool == "update_report":
            kept, dropped = gate.check(args, " ".join(self.heard))
            kept = gate.fill_severity(kept, text, bool(self.form.values.get("severity")))
            kept = postfix.apply(kept, text, self.form.compact())      # v4 fixes (POSTFIX=off: none)
            if kept:
                if self.asked is None or not self.form.transcripts:
                    self.form.add_transcript(text)     # narration → description
                self.form.apply(kept)
                if "d" in kept:
                    self.form.date_time = resolve(kept["d"], self.clock())
            else:
                tool = "no_op"                         # nothing survived the gate

        if tool == "no_op":
            say = ("Sorry, I didn't get anything for the report from that. "
                   + (REVIEW_TAIL if self.state == "review" else self._prompt_line()))
            return self._finish_turn(text, call, dropped, llm, t0, err, say)

        return self._finish_turn(text, call, dropped, llm, t0, err, self._next_line())

    def edit(self, field: str, value) -> dict:
        """A direct edit from the review screen (no LLM, no gate: the user typed it)."""
        t0 = time.perf_counter()
        self.form.set_field(field, value)
        if field == "date_time":
            self.form.date_time = resolve(str(value), self.clock())
        return self._result(transcript="", say="Updated. " + self._next_line(), t0=t0)

    _NOT_AN_UPDATE = re.compile(r"\b(submit|cancel|discard|scrap)\b", re.I)

    def call_llm(self, message: str, text: str):
        """The one LLM call of a turn (the app and the eval both use it). With the pre-fill on, the answer's
        fixed start is written into the prompt, unless the turn may be a submit or a cancel."""
        if self.prefill and not self._NOT_AN_UPDATE.search(text):
            return self.backend.generate(message, system=self.system, max_tokens=self.max_tokens,
                                         grammar=self.gbnf_after_prefix, prefix=UPDATE_PREFIX)
        return self.backend.generate(message, system=self.system, max_tokens=self.max_tokens, **self.constrain())

    def constrain(self) -> dict:
        """The keyword argument that constrains the LLM's answer, for backend.generate."""
        return {"grammar": self.gbnf} if self.constraint == "gbnf" else {"json_schema": self.json_schema}

    # ---- internals ---------------------------------------------------------
    @staticmethod
    def _parse(text: str) -> tuple[dict, str]:
        """The model's answer as a tool call. Full ({"tool":...}) and compact (a fields object, or "S" / "C" /
        "N") are both understood. The grammar makes it valid JSON; still never trust it blindly."""
        try:
            call = to_tool_call(json.loads(text))
            if call is not None:
                if not isinstance(call.get("args", {}), dict):
                    call["args"] = {}
                if call["tool"] == "update_report" and not call["args"]:
                    return {"tool": "no_op"}, ""          # an update with nothing in it says nothing
                return call, ""
        except ValueError:
            pass
        return {"tool": "no_op"}, f"unusable model output: {text[:120]!r}"

    def _ask(self, missing: list[dict]) -> str:
        f = missing[0]
        self.asked = f.get("key")
        return f.get("ask") or "Could you describe what happened?"

    def _prompt_line(self) -> str:
        """Repeat whatever we're waiting for."""
        if self.state == "review":
            return REVIEW_TAIL
        missing = self.form.missing()
        return self._ask(missing) if missing else self._next_line()

    def _next_line(self) -> str:
        missing = self.form.missing()
        if missing:
            self.state = "collecting"
            return self._ask(missing)
        self.state, self.asked = "review", None
        return self.readback()

    def readback(self) -> str:
        v = self.form.values
        parts = [f"Here's what I have. {_TYPE_SAID[v['incident_type']]}, {v['severity']} severity.",
                 f"Where: {v['location']}.", f"When: {self._when()}."]
        for name, label in (("persons_involved", "People involved"),
                            ("equipment_involved", "Equipment"),
                            ("immediate_actions", "Actions taken"), ("witnesses", "Witnesses")):
            if v.get(name):
                parts.append(f"{label}: {', '.join(v[name])}.")
        floor = gate.severity_floor(v["incident_type"], v["severity"])
        if floor:      # never changed by code: the user confirms or corrects it
            parts.append(f"{_TYPE_SAID[v['incident_type']]} is usually at least {floor} severity; you said "
                         f"{v['severity']}. Say a new severity to change it.")
        return " ".join(parts + [REVIEW_TAIL])

    def _when(self) -> str:
        dt = self.form.date_time
        if not dt or not dt["resolved"]:
            return (dt or {}).get("phrase") or self.form.values.get("date_time", "")
        t = datetime.fromisoformat(dt["iso"])
        days = (self.clock().date() - t.date()).days
        day = {0: "today", 1: "yesterday"}.get(days, t.strftime("%d %B"))
        clock = t.strftime("%I:%M %p").lstrip("0")
        return f"{'around ' if dt['approx'] else ''}{clock} {day}"

    def _submit(self, text, call, llm, t0, err) -> dict:
        self.turns.append({"transcript": text, "tool_call": call, "dropped": []})
        rec = {"submitted_at": self.clock().isoformat(), "form": self.form.public(),
               "turns": self.turns,
               "models": dict(self.used)}
        self.report_id = report.save(rec, self.report_dir)
        self.state = "submitted"
        return self._result(transcript=text, say=f"Report {self.report_id} submitted.",
                            call=call, llm=llm, t0=t0, error=err)

    def _finish_turn(self, text, call, dropped, llm, t0, err, say) -> dict:
        self.turns.append({"transcript": text, "tool_call": call, "dropped": dropped})
        return self._result(transcript=text, say=say, call=call, dropped=dropped, llm=llm,
                            t0=t0, error=err)

    def _result(self, transcript: str, say: str, call: dict | None = None,
                dropped: list | None = None, llm: dict | None = None,
                t0: float | None = None, error: str = "") -> dict:
        """A TurnResult (BUILD_PLAN §4.2) without the STT/TTS parts, which
        server.py adds. A timing that wasn't measured is None, never 0."""
        self.turn_id += 1
        llm = llm or {}
        total = (time.perf_counter() - t0) * 1000 if t0 is not None else None
        llm_wall = llm.get("llm_wall")
        self.last = {
            "turn_id": self.turn_id, "state": self.state, "transcript": transcript,
            "say": say, "audio_url": None, "asked": self.asked,
            "form": self.form.public(), "missing": [f["name"] for f in self.form.missing()],
            "tool_call": call, "dropped": dropped or [], "report_id": self.report_id,
            "timings_ms": {"stt": None, "llm_prefill": llm.get("llm_prefill"),
                           "llm_decode": llm.get("llm_decode"), "llm_wall": llm_wall,
                           "code": (total - llm_wall) if total is not None and llm_wall is not None
                           else total,
                           "tts": None, "total": total},
            "devices": {"stt": None, "llm": llm.get("device"), "tts": None},
            "llm": {"model": llm.get("model"), "prefill_tokens": llm.get("prefill_tokens"),
                    "decode_tokens": llm.get("decode_tokens")},
            "fast_path": bool(llm.get("fast_path")),
            "error": error,
        }
        return self.last
