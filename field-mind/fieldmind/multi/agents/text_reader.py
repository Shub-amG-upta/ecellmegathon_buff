"""
Text reader (model agent, a Job per note, P3; P2 when the note names a tag
that has a non-INFO fact this tick -- plan p.10).

Reads ONE engineer note when it arrives and turns it into a note-fact: kind
and up to three [subject, state] pairs from a fixed vocabulary. Raw note text
is seen by this agent only; diagnosis prompts get note-facts. Records do NOT
come here: record-facts are written by code (human decision a).

Owns `notefacts`, but writes only what the gate has checked.
"""

from __future__ import annotations

from .. import compact, grammar
from ._call import call_model

NAME = "text_reader"
ROLE = "text_reader"


class TextReaderAgent:
    def __init__(self, backend, notes_store, vocab, mcfg: dict,
                 log_prompts: bool = False):
        self.backend = backend
        self.store = notes_store
        self.vocab = vocab
        self.cap = mcfg["answer_caps"]["text_reader"]
        self.log_prompts = log_prompts
        self.template = compact.load_prompt("text_reader.txt")
        self.repair = compact.load_prompt("repair_lines.txt")
        # multi.grammar: the vocabulary is fixed, so one grammar for every note
        self.grammar = (grammar.to_gbnf(grammar.note(
            vocab.kinds, sorted(vocab.subjects), vocab.states, vocab.max_pairs))
            if mcfg.get("grammar") else None)
        self.calls = 0
        self.parse_failures = 0
        self.rejected = 0

    def arrivals(self, bb, now_s: float) -> list[dict]:
        """Notes that have arrived (t <= now) and have not been read. Nothing
        from the future is ever read."""
        seen = bb.read("notefacts")
        return [n for n in sorted(self.store.notes, key=lambda n: n.get("t", 0.0))
                if n.get("t", 0.0) <= now_s and n.get("id") not in seen]

    def make_job(self, bb, scheduler, tick: int, note: dict, active_tags: set,
                 submit_s: float):
        prompt = compact.text_reader_prompt(self.template, note, self.vocab)

        def work():
            with bb.step(NAME):
                self.calls += 1
                env, failed = call_model(
                    self.backend, prompt, ROLE, self.cap, {"note": dict(note)},
                    NAME, tick, compact.check_note_answer_shape,
                    repair_template=self.repair, log_prompts=self.log_prompts)
                self.parse_failures += failed
                return env

        priority = 2 if set(note.get("tags", [])) & active_tags else 3
        job = scheduler.new_job(NAME, evidence_tick=tick, priority=priority,
                                max_answer_tokens=self.cap, submit_s=submit_s,
                                work=work, side=str(note.get("id")))
        job.line_map = {"note": note}
        job.grammar = self.grammar
        return job

    def accept(self, bb, result, checked: tuple[bool, str]) -> dict:
        """Put the gate-checked reading on the board. A rejected reading is
        recorded too (so the note is not read again every tick)."""
        note = result.job.line_map["note"]
        ok, why = checked
        board = dict(bb.read("notefacts"))
        if ok:
            n_ok = sum(1 for v in board.values() if v["status"] == "ok")
            entry = compact.note_fact(f"N{n_ok + 1}", note, result.envelope.payload)
        else:
            self.rejected += 1
            entry = {"id": None, "note_id": note.get("id"), "status": "rejected",
                     "why": why}
        board[note.get("id")] = entry
        bb.write("notefacts", board, NAME)
        return entry
