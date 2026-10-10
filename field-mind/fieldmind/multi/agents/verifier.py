"""
Verifier (model agent, a Job, P3).

`multi.compact.ver_schema` off: the single agent's L5, unchanged (Phase 1).
`ver_schema` on (Phase 2 commit 6): the compact prompt
(compact.build_verification), pass/fail per shown claim by line number,
capped at multi.answer_caps.verifier. No repair call, as in the single agent.
The line map built with the prompt travels with the job; the gate expands the
verdicts through it and folds them with the single agent's Verifier.apply.
The counters (calls, retries) stay on the single agent's Verifier; the gate
counts disagreements after expansion.

Runs on the gate's checked diagnosis claims and the evidence tick's facts.
Writes nothing to the board: the gate applies its answer to the verdict.
"""

from __future__ import annotations

from .. import compact, grammar
from ._call import call_model

NAME = "verifier"
PRIORITY = 3


class VerifierAgent:
    def __init__(self, ver, cfg: dict, mcfg: dict | None = None):
        self.ver = ver
        self.cfg = cfg
        mcfg = mcfg or {}
        self.sw = compact.compact_switches(mcfg.get("compact"))
        self.cap = (mcfg.get("answer_caps") or {}).get("verifier", 30)
        self.guard_cpt = mcfg.get("compact_guard_cpt")
        self.grammar = bool(mcfg.get("grammar", False)) and self.sw["ver_schema"]
        if self.sw["ver_schema"]:
            self.templates = {"body": compact.load_prompt("ver_compact.txt"),
                              "rules_full": compact.load_prompt("ver_rules_full.txt"),
                              "rules_compact": compact.load_prompt("ver_rules_compact.txt")}

    def should_run(self, level: str, top_conf: float, rung: int) -> bool:
        return rung < 1 and self.ver.should_run(level, top_conf)

    def make_job(self, bb, scheduler, tick: int, claims: dict, submit_s: float):
        facts = bb.read("facts").facts_of(tick)
        if self.sw["ver_schema"]:
            return self._compact_job(bb, scheduler, tick, claims, facts, submit_s)

        def work():
            with bb.step(NAME):
                return self.ver.run(tick, claims, facts)

        return scheduler.new_job(NAME, evidence_tick=tick, priority=PRIORITY,
                                 max_answer_tokens=self.cfg["max_tokens"],
                                 submit_s=submit_s, work=work)

    def _compact_job(self, bb, scheduler, tick, claims, facts, submit_s):
        prompt, line_map, hint = compact.build_verification(
            self.sw, self.templates, facts=facts, claims=claims,
            evidence_tick=tick)
        # Flagged, never cut: facts are all the tick's facts (as in Phase 1).
        line_map["over_limit"] = compact.over_limit(prompt, self.cap, self.guard_cpt)
        ver = self.ver

        def work():
            with bb.step(NAME):
                ver.calls += 1
                env, _ = call_model(ver.backend, prompt, NAME, self.cap, hint,
                                    NAME, tick, compact.check_ver_answer_shape,
                                    repair_template=None,
                                    log_prompts=ver.log_prompts)
                ver.retries += env.retries
                return env

        job = scheduler.new_job(NAME, evidence_tick=tick, priority=PRIORITY,
                                max_answer_tokens=self.cap, submit_s=submit_s,
                                work=work)
        job.line_map = line_map
        if self.grammar:
            job.grammar = grammar.to_gbnf(grammar.verification(
                len(line_map["claims"]), len(line_map["facts"])))
        return job
