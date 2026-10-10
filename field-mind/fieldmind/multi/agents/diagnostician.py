"""
Diagnostician (model agent, a Job).

`multi.compact.schema` off: the single agent's L4, unchanged (Phase 1). The
job's work is Diagnostician.run on THIS tick's evidence; the Diagnostician
object was constructed on the LaneBackend facade, so its call goes to whichever
lane the scheduler picks.

`schema` on (Phase 2 commit 5): the compact prompt (compact.build_diagnosis),
answer format B' capped at multi.answer_caps.diagnostician, one repair call.
The line map built with the prompt travels with the job; the gate expands the
answer through it. The counters (calls, parse failures) stay on the single
agent's Diagnostician, so the run file's diag_calls / parse_failure_rate keep
their meaning.

Reads the board (facts, retrieval, findings, belief, trust, notefacts,
recordfacts) and writes nothing: its envelope returns as a Result, and only the
gate puts it on the board.
"""

from __future__ import annotations

from types import SimpleNamespace

from ...agent.l1_symbolize import evidence_packet
from ...agent.orchestrator import evidence_max_facts, wm_summary
from ...schemas import TAGS
from .. import compact, grammar, sides
from ._call import call_model

NAME = "diagnostician"
# Phase 3: a side's job is placed by `diag_<side>` (multi.fixed_placement);
# its envelope keeps agent "diagnostician", so the bench tools read it as one.
SIDE_FACT_LINES = 8     # CITED: plan p.15 token budget, "this side's facts (up to 8 lines)"
SIDE_NOTE = {           # one line naming the side; plan p.5 agent table
    "water": "This prompt covers the WATER side only: drum level, feed water flow, steam flow.",
    "heat": "This prompt covers the HEAT side only: bed temperature, drum pressure, main "
            "steam temperature, with steam flow as the load.",
}


def priority_for(level: str) -> int:
    """P1 urgent diagnosis (trip predicted / CRITICAL fact, i.e. triage URGENT),
    else P2 (plan p.10)."""
    return 1 if level == "URGENT" else 2


class DiagnosticianAgent:
    def __init__(self, diag, cfg: dict, mcfg: dict | None = None,
                 letters: dict | None = None):
        self.diag = diag
        self.cfg = cfg
        mcfg = mcfg or {}
        self.sw = compact.compact_switches(mcfg.get("compact"))
        self.letters = letters or {}
        self.cap = (mcfg.get("answer_caps") or {}).get("diagnostician", 60)
        self.guard_cpt = mcfg.get("compact_guard_cpt")
        self.split = bool(mcfg.get("split", False))
        self.raw_notes = self.sw["notes"] and self.sw["note_source"] == "raw"
        self.on_change = bool(mcfg.get("split_on_change", False))
        self.grammar = bool(mcfg.get("grammar", False))
        if self.grammar and not self.sw["schema"]:
            raise ValueError("multi.grammar needs multi.compact.schema on: the grammar "
                             "is the compact (B') answer format")
        if self.on_change and not self.split:
            raise ValueError("multi.split_on_change needs multi.split on")
        if self.split and not self.sw["schema"]:
            raise ValueError("multi.split needs multi.compact.schema on: the side "
                             "prompts are the compact (B') prompts")
        if self.sw["schema"]:
            # group_letters off: the same body without the leading "g" in the
            # answer format and example
            body = ("diag_compact.txt" if self.sw["group_letters"]
                    else "diag_compact_nogroup.txt")
            self.templates = {"body": compact.load_prompt(body),
                              "rules_full": compact.load_prompt("diag_rules_full.txt"),
                              "rules_compact": compact.load_prompt("diag_rules_compact.txt")}
            self.repair = compact.load_prompt("repair_lines.txt")

    def make_job(self, bb, scheduler, tick: int, level: str, rung: int,
                 submit_s: float):
        if self.sw["schema"]:
            return self._compact_job(bb, scheduler, tick, level, rung, submit_s)
        facts = bb.read("facts").facts_of(tick)
        retrieved = bb.read("retrieval")
        summary_view = SimpleNamespace(open_findings=bb.read("findings"),
                                       hypotheses=bb.read("belief"),
                                       trusted_tags=bb.read("trust"))

        def work():
            with bb.step(NAME):
                evidence = evidence_packet(
                    facts, max_facts=evidence_max_facts(level, rung))
                return self.diag.run(tick, evidence, dict(retrieved), facts,
                                     wm_summary(summary_view))

        return scheduler.new_job(NAME, evidence_tick=tick,
                                 priority=priority_for(level),
                                 max_answer_tokens=self.cfg["max_tokens"],
                                 submit_s=submit_s, work=work)

    def sides_to_run(self, bb, tick: int) -> list[str]:
        """The active sides (non-INFO fact or open finding); both when neither
        side has evidence (Phase 3 decision 5)."""
        return sides.sides_to_run(bb.read("facts").facts_of(tick), bb.read("findings"))

    @staticmethod
    def fingerprint(bb, side: str, raw_notes: bool = False) -> tuple:
        """The evidence a side's diagnosis depends on, as on the board now.
        `raw_notes` (note_source raw): the side's selected raw notes count as
        evidence, so a newly arrived note makes the side be asked again (with
        the text reader on, its note-facts do that)."""
        retrieved = bb.read("retrieval")
        shown = ([c.get("case_id") for c in
                  sides.cases_for_side(retrieved.get("cases", []), side)]
                 + [e.get("case_id") for e in retrieved.get("experience", [])])
        if raw_notes:
            shown += ["note:" + str(n.get("id")) for n in retrieved.get("notes", [])
                      if side in sides.note_sides(n)]
        return sides.side_fingerprint(bb.read("signature").get("signature", {}),
                                      bb.read("findings"),
                                      list(bb.read("notefacts").values()), side,
                                      case_ids=shown)

    def make_jobs(self, bb, scheduler, tick: int, level: str, rung: int,
                  submit_s: float) -> list:
        """Phase 3: one job per side to run (`multi.split` on); else one job.
        With `multi.split_on_change`, no job for a side whose evidence is
        unchanged since its cached answer (plan: "a side's diagnosis is
        requested only if its signature, its open findings or its note-facts
        changed"); the gate re-uses that answer."""
        if not self.split:
            return [self.make_job(bb, scheduler, tick, level, rung, submit_s)]
        cached = bb.read("side_answers")
        jobs = []
        for s in self.sides_to_run(bb, tick):
            fp = self.fingerprint(bb, s, self.raw_notes)
            if self.on_change and s in cached and cached[s]["fingerprint"] == fp:
                continue
            job = self._compact_job(bb, scheduler, tick, level, rung, submit_s, side=s)
            job.line_map["fingerprint"] = fp
            # telemetry: the cache this job was made against ("same" is only
            # possible with split_on_change off)
            job.line_map["info"]["cache"] = ("none" if s not in cached else
                                             "same" if cached[s]["fingerprint"] == fp
                                             else "changed")
            jobs.append(job)
        return jobs

    # ------------------------------------------------------------------
    def _compact_job(self, bb, scheduler, tick, level, rung, submit_s, side=None):
        all_facts = bb.read("facts").facts_of(tick)
        retrieved = dict(bb.read("retrieval"))
        notefacts = dict(bb.read("notefacts"))
        belief = bb.read("belief")
        trust = bb.read("trust")
        summary_view = SimpleNamespace(open_findings=bb.read("findings"),
                                       hypotheses=belief, trusted_tags=trust)
        facts, extra = all_facts, {}
        if side is not None:
            # the side's view: its facts, its cases, its notes; retrieval and
            # belief themselves are not split (decision 4)
            facts = sides.facts_for_side(all_facts, side)
            retrieved["cases"] = sides.cases_for_side(retrieved.get("cases", []), side)
            if self.sw["notes"] and self.sw["note_source"] == "raw":
                # raw notes: by the note's own tags (a note with none goes to both)
                retrieved["notes"] = [n for n in retrieved.get("notes", [])
                                      if side in sides.note_sides(n)]
            elif self.sw["notes"]:
                # note-facts: by the note-fact's own subjects (commit 0 design)
                notefacts = {k: v for k, v in notefacts.items()
                             if v.get("status") != "ok" or side in sides.notefact_sides(v)}
            else:
                # raw notes (the notes-section ablation): by the note's tags
                retrieved["notes"] = [n for n in retrieved.get("notes", [])
                                      if side in sides.note_sides(n)]
            extra = {"max_fact_lines": SIDE_FACT_LINES, "side_note": SIDE_NOTE[side],
                     "other_line": sides.other_side_line(all_facts, side)}
        prompt, line_map, hint, info = compact.build_diagnosis(
            self.sw, self.templates, facts=facts,
            level_cap=evidence_max_facts(level, rung), retrieved=retrieved,
            notefacts=notefacts,
            recordfacts=list(bb.read("recordfacts")), belief=list(belief),
            untrusted=sorted(set(TAGS) - set(trust)),
            wm_text=wm_summary(summary_view), now_s=submit_s,
            evidence_tick=tick, letters=self.letters, cap=self.cap,
            guard_cpt=self.guard_cpt, **extra)
        line_map["side"] = side
        info.update(side=side, fact_ids=list(line_map["facts"]),
                    case_ids=[c["case_id"] for c in line_map["cases"]])
        line_map["info"] = info
        diag = self.diag

        def work():
            with bb.step(NAME):
                diag.calls += 1
                env, failed = call_model(
                    diag.backend, prompt, NAME, self.cap, hint, NAME, tick,
                    compact.check_diag_answer_shape, repair_template=self.repair,
                    log_prompts=diag.log_prompts)
                diag.parse_failures += failed
                diag.retries += env.retries
                env.retrieved_cases = list(line_map["retrieved_cases"])
                return env

        job = scheduler.new_job(NAME if side is None else f"diag_{side}",
                                evidence_tick=tick, priority=priority_for(level),
                                max_answer_tokens=self.cap,
                                submit_s=submit_s, work=work,
                                side="all" if side is None else side)
        job.line_map = line_map
        if self.grammar:
            # exactly the lines THIS prompt showed
            job.grammar = grammar.to_gbnf(grammar.diagnosis(
                len(line_map["facts"]), len(line_map["cases"]),
                len(line_map["context"]), line_map["letters"],
                with_group=self.sw["group_letters"]))
        return job
