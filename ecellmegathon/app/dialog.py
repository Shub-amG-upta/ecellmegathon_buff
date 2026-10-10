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
import time
from datetime import datetime

from app import gate, report
from app.form import FormState, llm_json_schema, load_catalogue, load_schema
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
                 clock=_now, report_dir=None, max_tokens: int = 128):
        self.backend = backend
        self.schema = schema or load_schema()
        self.catalogue = load_catalogue() if catalogue is None else catalogue
        self.clock = clock
        self.report_dir = report_dir or report.REPORT_DIR
        self.max_tokens = max_tokens
        self.system = system_prompt(self.schema)
        self.json_schema = llm_json_schema(self.schema)
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
        reply = self.backend.generate(user_message(self.form.compact(), self.asked, text),
                                      system=self.system, json_schema=self.json_schema,
                                      max_tokens=self.max_tokens)
        llm = {"llm_prefill": reply.prefill_ms, "llm_decode": reply.decode_ms,
               "llm_wall": reply.latency_ms, "device": reply.backend, "model": reply.model,
               "prefill_tokens": reply.prefill_tokens, "decode_tokens": reply.decode_tokens}
        if reply.status != "ok":
            self.heard.pop()
            return self._result(transcript=text, llm=llm, t0=t0, error=reply.error or reply.status,
                                say="Sorry, I had trouble with that. Could you say it again?")
        call, err = self._parse(reply.text)
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

    # ---- internals ---------------------------------------------------------
    @staticmethod
    def _parse(text: str) -> tuple[dict, str]:
        """The schema makes this valid JSON; still never trust it blindly."""
        try:
            call = json.loads(text)
            if isinstance(call, dict) and call.get("tool") in (
                    "update_report", "submit_report", "cancel_report", "no_op"):
                if not isinstance(call.get("args", {}), dict):
                    call["args"] = {}
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
               "models": {"llm": llm.get("model"), "llm_device": llm.get("device")}}
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
            "error": error,
        }
        return self.last
