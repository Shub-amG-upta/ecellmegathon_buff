"""
=============================================================================
 COMPACT PROMPTS AND ID-ONLY ANSWERS  (multi-agent Phase 2, "Shrink the prompt")
=============================================================================

Pure functions: prompt text in, parsed answers out. No board, no backend.

  record-facts   written by CODE from records.json (human decision a): no
                 model call, own ids R1...
  note-facts     the text reader's answer for ONE engineer note, checked
                 against a fixed vocabulary before it reaches the board.

Stdlib only (copied to the device with the rest of fieldmind/).
=============================================================================
"""

from __future__ import annotations

import math
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


# ======================================================================
#  Record-facts: code, no model
# ======================================================================
def record_facts(records_view: dict) -> list[dict]:
    """One record-fact per item of the Retriever's records view (already
    time-filtered to now, already code-written text). Ids R1.. in view order."""
    out = []
    for key, val in (records_view or {}).items():
        for item in (val if isinstance(val, list) else [val]):
            out.append({"id": f"R{len(out) + 1}", "key": key,
                        "text": f"{key.replace('_', ' ')}: {item}"})
    return out


# ======================================================================
#  Note-facts: the text reader's vocabulary, prompt and answer check
# ======================================================================
class NoteVocab:
    """What a note-fact may say. Subjects: the six tags, the asset model's
    equipment, and the configured extra subjects. Kinds and states: fixed
    lists from the config (multi.text_reader)."""

    def __init__(self, tags, equipment, tcfg: dict):
        self.tags = list(tags)
        self.equipment = list(equipment)
        self.extra = list(tcfg["extra_subjects"])
        self.kinds = list(tcfg["kinds"])
        self.states = list(tcfg["states"])
        self.max_pairs = int(tcfg["max_pairs"])
        self.subjects = set(self.tags) | set(self.equipment) | set(self.extra)


def text_reader_prompt(template: str, note: dict, vocab: NoteVocab) -> str:
    """Rules, the dictionary, and the ONE raw note inside a data fence."""
    return template.format(
        max_pairs=vocab.max_pairs, tags=", ".join(vocab.tags),
        equipment=", ".join(vocab.equipment), extra=", ".join(vocab.extra),
        states=", ".join(vocab.states), note=note.get("text", ""))


def check_note_answer_shape(payload: dict) -> tuple[bool, str]:
    """Shape only (decides whether a repair call is worth making)."""
    if not isinstance(payload.get("k"), str):
        return False, "missing kind k"
    s = payload.get("s")
    if not isinstance(s, list):
        return False, "s must be a list of [subject, state] pairs"
    for pair in s:
        if not (isinstance(pair, list) and len(pair) == 2
                and all(isinstance(x, str) for x in pair)):
            return False, "s must be a list of [subject, state] pairs"
    return True, ""


def check_note_answer(payload: dict, vocab: NoteVocab) -> tuple[bool, str]:
    """The gate's check: every value must come from the vocabulary. A note-fact
    with one unknown subject or state is rejected whole -- half a reading is
    worse than none, because the reader cannot tell it is half."""
    ok, why = check_note_answer_shape(payload)
    if not ok:
        return False, why
    if payload["k"] not in vocab.kinds:
        return False, f"unknown kind {payload['k']!r}"
    if len(payload["s"]) > vocab.max_pairs:
        return False, f"more than {vocab.max_pairs} pairs"
    for subject, state in payload["s"]:
        if subject not in vocab.subjects:
            return False, f"unknown subject {subject!r}"
        if state not in vocab.states:
            return False, f"unknown state {state!r}"
    return True, ""


def note_fact(nf_id: str, note: dict, payload: dict) -> dict:
    """The stored note-fact. `when` and `reliability` come from the note's own
    metadata (code), not from the model; `note_id` links back to the raw note."""
    return {"id": nf_id, "note_id": note.get("id"), "status": "ok",
            "kind": payload["k"],
            "pairs": [list(p) for p in payload["s"]],
            "t": note.get("t", 0.0), "author": note.get("author", "?"),
            "reliability": note.get("reliability")}


def _age(now_s: float, t: float) -> str:
    m = max(0.0, (now_s - t) / 60.0)
    if m < 90:
        return f"{m:.0f} min ago"
    if m < 48 * 60:
        return f"{m / 60:.0f} h ago"
    return f"{m / 1440:.0f} d ago"


def note_fact_text(nf: dict, now_s: float) -> str:
    """The line a diagnosis prompt shows for a note-fact (written by code)."""
    if nf["kind"] == "INSTR":
        body = "instruction-like text, ignored"
    else:
        body = "; ".join(f"{s} {st}" for s, st in nf["pairs"]) or "no listed subject"
    rel = "?" if nf.get("reliability") is None else f"{nf['reliability']:g}"
    return (f"{nf['id']} [note, {nf['kind']}, rel {rel}, "
            f"{_age(now_s, nf['t'])}] {body}")


# ======================================================================
#  Compact diagnostician (Phase 2 commit 5): per-section switches, numbered
#  prompt, answer format B' (amendment 1), expansion by code
# ======================================================================
# Switches (multi.compact). `schema` turns on B' and numbered lines; the other
# four shrink one section each and need `schema` (their text speaks in line
# numbers). All off = the single agent's prompt, byte for byte.
SECTIONS = ("schema", "rules", "cases", "notes", "world")
# Compact verifier (commit 6): `ver_schema` turns on its line-number answer;
# `ver_rules` and `ver_claims` need it.
VER_SECTIONS = ("ver_schema", "ver_rules", "ver_claims")
ALL_SECTIONS = SECTIONS + VER_SECTIONS
CASE_ORDERS = ("score", "shuffled")
NOTE_SOURCES = ("reader", "raw")
MAX_CLAIMS = 3          # HUMAN DECISION (b): claims beyond rank 3 are not shown

SEV_ORDER = {"CRITICAL": 0, "ALARM": 1, "WATCH": 2, "INFO": 3}   # = evidence_packet
MAX_FACT_LINES = 9      # HUMAN DECISION, amendment 1 item 2 (single-digit fact lines)
MAX_NOTE_LINES = 4      # HUMAN DECISION, amendment 4 item 3 (plan p.15: up to 4)
MAX_RECORD_LINES = 4    # HUMAN DECISION, amendment 4 item 3
MAX_RANKED = 3          # B' worst case measured with 3 cases (commit 3)
GUARD_MIN_CASES = 2     # DESIGN CHOICE: the guard never leaves fewer than 2 cases
PROMPT_LIMIT = 1280     # CITED: plan rule 4 / CLAUDE.md, prompt + answer cap
NO_CASE_FITS = "model: no listed case fits"
NO_BELIEF_CONF = 0.3    # CITED: fieldmind/agent/orchestrator.merge default (amendment 2)


def compact_switches(ccfg: dict | None) -> dict:
    """The five section switches and the case order, validated."""
    ccfg = ccfg or {}
    sw = {s: bool(ccfg.get(s, False)) for s in ALL_SECTIONS}
    sw["case_order"] = ccfg.get("case_order", "score")
    # multi.compact.group_letters (default on = every earlier prompt, byte for
    # byte): the "[group X]" tag on each case line and the answer's leading "g".
    # Off: neither is shown or asked for (reports/multi_llm_impact_design.md: the
    # model wrote the most common letter in 89% of answers and picked from it).
    sw["group_letters"] = bool(ccfg.get("group_letters", True))
    # multi.compact.note_source (with `notes` on): `reader` (default, every
    # earlier prompt) shows the text reader's note-facts; `raw` shows the
    # selected notes' own text ("[author] text", retrieval's selection, the same
    # line caps), with the code-written record-facts and the token guard
    # unchanged. Needs the model text reader off (compact.text_reader: false).
    sw["note_source"] = ccfg.get("note_source", "reader")
    if sw["note_source"] not in NOTE_SOURCES:
        raise ValueError(f"multi.compact.note_source must be one of {NOTE_SOURCES}")
    if sw["note_source"] == "raw" and not (sw["notes"] and sw["schema"]):
        raise ValueError("multi.compact.note_source raw needs `notes` and `schema` on")
    if sw["case_order"] not in CASE_ORDERS:
        raise ValueError(f"multi.compact.case_order must be one of {CASE_ORDERS}")
    needs = [s for s in SECTIONS[1:] if sw[s]]
    if needs and not sw["schema"]:
        raise ValueError(f"multi.compact {needs} need `schema` on: their compact "
                         f"text speaks in line numbers")
    vneeds = [s for s in VER_SECTIONS[1:] if sw[s]]
    if vneeds and not sw["ver_schema"]:
        raise ValueError(f"multi.compact {vneeds} need `ver_schema` on")
    return sw


def group_letters(groups_doc: dict, library_ids: list[str]) -> tuple[dict, dict]:
    """case id -> group letter, and letter -> group id. The look-alike groups of
    data/kb/case_groups.json get A, B, C... in file order, then each singleton
    its own letter in file order, then any other library case."""
    letters, groups = {}, {}

    def nxt():
        return chr(ord("A") + len(groups))
    for g in groups_doc.get("groups", []):
        L = nxt()
        groups[L] = g["id"]
        for m in g["members"]:
            letters[m] = L
    for cid in list(groups_doc.get("singletons", [])) + list(library_ids):
        if cid not in letters:
            L = nxt()
            groups[L] = cid
            letters[cid] = L
    return letters, groups


def first_sentence(text: str) -> str:
    """The short discriminating check: `discriminating_evidence` up to its first
    full stop followed by a space (a mechanical rule; no case data edited)."""
    text = (text or "").strip()
    i = text.find(". ")
    return text if i < 0 else text[:i + 1]


def signature_text(signature: dict) -> str:
    from ..kb.stores import CaseLibrary
    return ", ".join(f"{t} {d}" + ("" if b == "-" else f" {b}")
                     for (t, d, b) in CaseLibrary._to_triples(signature or {}))


def _record_lines_full(rv: dict) -> list[str]:
    """The single agent's record lines (l4_diagnose.build_prompt), unnumbered."""
    out = []
    if rv.get("coal_lab_report"):
        out.append(f"coal lab report: {rv['coal_lab_report']}")
    for m in rv.get("maintenance_history", []):
        out.append(f"maintenance: {m}")
    if rv.get("boiler_water_conductivity"):
        out.append(f"boiler water conductivity: {rv['boiler_water_conductivity']}")
    for a in rv.get("alarm_log", []):
        out.append(f"alarm log: {a}")
    return out


def _numbered(lines: list[str], empty: str) -> str:
    return "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1)) or empty


def _belief_conf(case: dict, live: list) -> float:
    """Amendment 2 option (ii): belief's own confidence for this case (a live
    entry; `merge` caps a model-only case at 0.5), else the 0.3 default."""
    for h in live:
        if h.cause == (case.get("root_cause") or case.get("cause")):
            return float(h.confidence)
    for h in live:
        if case.get("case_id") and h.case_ref == case.get("case_id"):
            return float(h.confidence)
    return NO_BELIEF_CONF


def build_diagnosis(sw: dict, templates: dict, *, facts, level_cap: int,
                    retrieved: dict, notefacts: dict, recordfacts: list,
                    belief: list, untrusted: list, wm_text: str, now_s: float,
                    evidence_tick: int, letters: dict, cap: int,
                    guard_cpt: float | None = None,
                    max_fact_lines: int = MAX_FACT_LINES,
                    side_note: str | None = None,
                    other_line: str | None = None) -> tuple[str, dict, dict, dict]:
    """The compact diagnosis prompt (sw["schema"] must be on). Returns
    (prompt, line_map, mock hint, info). The line map is what the gate expands
    the answer through: fixed here, never recomputed when the answer arrives.

    Phase 3 (a side's prompt): `max_fact_lines` (8, plan p.15), `side_note` (one
    line naming the side, put before the rules) and `other_line` (the code-written
    line about the other side, put after the world line). Their defaults leave
    the Phase 2 prompt byte for byte."""
    if not sw["schema"]:
        raise ValueError("build_diagnosis is the schema-on path")
    # ---- facts: evidence_packet order, the level cap, then at most 9 lines
    ranked = sorted(facts, key=lambda f: (SEV_ORDER.get(f.severity, 9), f.id))
    by_level = ranked[:level_cap]
    shown_facts = by_level[:max_fact_lines]
    fact_lines = [f"[{f.check}/{f.severity}] {f.detail}" for f in shown_facts]
    omitted = len(ranked) - len(shown_facts)

    # ---- cases (and own past episodes), in the switch's order
    cases = [dict(c, _kind="case") for c in retrieved.get("cases", [])]
    cases += [dict(e, _kind="exp") for e in retrieved.get("experience", [])]
    if sw["case_order"] == "shuffled":
        import random
        random.Random(evidence_tick).shuffle(cases)

    # ---- context: notes then records
    if sw["notes"] and sw.get("note_source") == "raw":
        # raw note text, newest first (as note-facts are), record-facts as usual
        raw = sorted(retrieved.get("notes", []), key=lambda n: -n.get("t", 0.0))
        notes = [(n.get("id"), f"{n.get('id')} [note, {n.get('author', '?')}, "
                  f"{_age(now_s, n.get('t', 0.0))}] {n.get('text', '')}") for n in raw]
        recs = [(r["id"], f"{r['id']} {r['text']}") for r in recordfacts]
    elif sw["notes"]:
        sel = {n.get("id") for n in retrieved.get("notes", [])}
        nfs = sorted((v for k, v in notefacts.items()
                      if k in sel and v.get("status") == "ok"),
                     key=lambda v: -v.get("t", 0.0))
        notes = [(nf["id"], note_fact_text(nf, now_s)) for nf in nfs]
        recs = [(r["id"], f"{r['id']} {r['text']}") for r in recordfacts]
    else:
        notes = [(n.get("id"), f"[{n.get('author', '?')}] {n['text']}")
                 for n in retrieved.get("notes", [])]
        recs = [(f"rec{i}", t) for i, t in
                enumerate(_record_lines_full(retrieved.get("records") or {}), 1)]
    notes_dropped = [i for i, _ in notes[MAX_NOTE_LINES:]] if sw["notes"] else []
    recs_dropped = [i for i, _ in recs[MAX_RECORD_LINES:]] if sw["notes"] else []
    if sw["notes"]:
        notes, recs = notes[:MAX_NOTE_LINES], recs[:MAX_RECORD_LINES]

    live = [h for h in belief if not h.retired]
    if sw["world"]:
        top = sorted(live, key=lambda h: -h.confidence)[:3]
        world = ("belief (from code): "
                 + (", ".join(f"{h.case_ref or h.cause} {h.confidence:.2f}" for h in top)
                    or "none"))
        if untrusted:
            world += " | UNTRUSTED TAGS: " + ", ".join(untrusted)
    else:
        world = wm_text
    if other_line:
        world += f"\nOTHER SIDE (from code): {other_line}"

    if sw["rules"]:
        rules = templates["rules_compact"]
    else:
        rules = templates["rules_full"].format(
            operator_question=retrieved.get("operator_query") or "(none stated)")
    if side_note:
        rules = side_note + "\n" + rules

    def case_line(c):
        cid = c.get("case_id", "EXP")
        if c["_kind"] == "exp":
            tag = "unconfirmed" if c.get("unverified") else "confirmed"
            return (f"{cid} [own past episode, {tag}, seen {c.get('occurrences', 1)}x] "
                    f"cause={c.get('root_cause', '?')}")
        tag = f" [group {letters.get(cid, '?')}]" if sw.get("group_letters", True) else ""
        if sw["cases"]:
            return (f"{cid}{tag} {signature_text(c.get('signature'))}; "
                    f"check: {first_sentence(c.get('discriminating_evidence', ''))}")
        return (f"{cid}{tag} ({c['score']:.2f}) {c.get('title', '')}: "
                f"cause={c.get('root_cause', '?')}; "
                f"discriminator={c.get('discriminating_evidence', '?')}")

    def render(cases, notes, recs):
        fl = _numbered(fact_lines, "(no facts above threshold)")
        if omitted:
            fl += f"\n({omitted} lower-severity facts omitted for context budget)"
        ctx = [t for _, t in notes] + [t for _, t in recs]
        return templates["body"].format(
            rules=rules, facts=fl,
            cases=_numbered([case_line(c) for c in cases],
                            "(no similar case retrieved)"),
            context=_numbered(ctx, "(none relevant)"), world=world)

    # ---- guard: a safety net, fitted chars-per-token (multi.compact_guard_cpt).
    # Only for the fully compact prompt (every section on), the one gate G3
    # holds to the limit. An ablation prompt (a section left long) is over the
    # limit by design; dropping lines from it would change which cases the
    # model sees and so break the G1a comparison. It is flagged, not cut.
    prompt = render(cases, notes, recs)
    guard_dropped = []
    full = all(sw[s] for s in SECTIONS)
    if guard_cpt and full:
        est = lambda p: math.ceil(len(p) / guard_cpt)
        while est(prompt) + cap >= PROMPT_LIMIT:
            if recs:
                guard_dropped.append(recs.pop()[0])
            elif notes:
                guard_dropped.append(notes.pop()[0])
            elif len(cases) > GUARD_MIN_CASES:
                guard_dropped.append(cases.pop().get("case_id"))
            else:
                break
            prompt = render(cases, notes, recs)

    line_map = {
        "kind": "diagnosis", "evidence_tick": evidence_tick,
        "facts": [f.id for f in shown_facts],
        "fact_detail": [f.detail for f in shown_facts],
        "cases": [{"case_id": c.get("case_id"), "root_cause": c.get("root_cause"),
                   "discriminating_evidence": c.get("discriminating_evidence", "")}
                  for c in cases],
        "letters": [letters.get(c.get("case_id"), "?") for c in cases],
        "conf": [_belief_conf(c, live) for c in cases],
        "context": [i for i, _ in notes] + [i for i, _ in recs],
        "retrieved_cases": [c.get("case_id") for c in retrieved.get("cases", [])],
    }
    hint = {"compact": True,
            "cases_by_score": [c.get("case_id") for c in retrieved.get("cases", [])],
            "case_lines": [c.get("case_id") for c in cases],
            "facts": [f.id for f in facts],
            "fact_lines": list(line_map["facts"]),
            "letters": list(line_map["letters"])}
    info = {"fact_lines": len(shown_facts),
            "facts_dropped_by_line_cap": [f.id for f in by_level[max_fact_lines:]],
            "notes_dropped": notes_dropped, "records_dropped": recs_dropped,
            "guard_fired": bool(guard_dropped), "guard_dropped": guard_dropped,
            # estimate still over the limit: an ablation prompt (never cut), or
            # a full prompt the guard could not cut enough. Flagged and counted;
            # the prompt is still sent (the tick never fails).
            "over_limit_unguarded": bool(guard_cpt and not full and
                                         math.ceil(len(prompt) / guard_cpt) + cap
                                         >= PROMPT_LIMIT),
            "over_limit_after_guard": bool(guard_cpt and full and
                                           math.ceil(len(prompt) / guard_cpt) + cap
                                           >= PROMPT_LIMIT)}
    return prompt, line_map, hint, info


def combine_side_payloads(payloads: list[dict]) -> dict:
    """Phase 3 decision 1: expanded side answers, in side order (the side with
    the more severe evidence first), into one payload for the single agent's
    merge. A case named by both sides appears once, at its first place, with
    both sides' citations (first side's first); `unexplained` lines are kept
    once each. Confidence stays the code-supplied value (option (ii))."""
    hyps, at = [], {}
    unexplained = []
    for p in payloads:
        for h in p.get("hypotheses", []):
            key = h.get("case_ref") or h.get("cause")
            if key in at:
                kept = hyps[at[key]]
                kept["supports"] += [x for x in h.get("supports", [])
                                     if x not in kept["supports"]]
                continue
            at[key] = len(hyps)
            hyps.append(dict(h, supports=list(h.get("supports", []))))
        unexplained += [u for u in p.get("unexplained", []) if u not in unexplained]
    for i, h in enumerate(hyps):
        h["rank"] = i + 1
    return {"headline": "", "hypotheses": hyps, "unexplained": unexplained}


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def check_diag_answer_shape(p: dict) -> tuple[bool, str]:
    """B' shape only (decides the one repair call). Only `r` is required; `g`,
    `sep`, `n`, `x` are checked when present. Range is NOT checked here: an
    out-of-range fact line is an invented citation, counted by the gate."""
    r = p.get("r")
    if not isinstance(r, list):
        return False, 'missing ranked list "r"'
    if len(r) > MAX_RANKED:
        return False, f'"r" has more than {MAX_RANKED} cases'
    for e in r:
        if not (isinstance(e, list) and len(e) == 2 and _is_int(e[0])
                and isinstance(e[1], list) and all(_is_int(x) for x in e[1])):
            return False, '"r" entries must be [case line, [fact lines]]'
    for k in ("n", "x"):
        if k in p and not (isinstance(p[k], list) and all(_is_int(x) for x in p[k])):
            return False, f'"{k}" must be a list of line numbers'
    if p.get("sep") is not None and not _is_int(p["sep"]):
        return False, '"sep" must be a line number or null'
    if "g" in p and not isinstance(p["g"], str):
        return False, '"g" must be a group letter'
    return True, ""


def expand_diag_answer(p: dict, lm: dict) -> tuple[dict, dict]:
    """B' answer -> the single agent's payload shape, through the job's line
    map. Fact ids are the evidence tick's LOCAL ids (the gate stamps a
    cross-tick answer). Returns (payload, info for telemetry)."""
    cases, fids = lm["cases"], lm["facts"]
    nf = len(fids)
    fact_id = lambda k: fids[k - 1] if 1 <= k <= nf else f"line{k}"
    hyps, seen = [], set()
    bad_case = bad_fact = 0
    for cl, fls in p.get("r", []):
        if not 1 <= cl <= len(cases) or cl in seen:
            bad_case += 1
            continue
        seen.add(cl)
        c = cases[cl - 1]
        bad_fact += sum(1 for k in fls if not 1 <= k <= nf)
        hyps.append({"rank": len(hyps) + 1,
                     "cause": c.get("root_cause") or "unknown",
                     "confidence": lm["conf"][cl - 1],
                     "supports": [fact_id(k) for k in fls],
                     "case_ref": c.get("case_id"),
                     "discriminator": c.get("discriminating_evidence", "")})
    # Text for the engineer: the fact id always carries its evidence tick, so a
    # late answer can never be read as naming a fact of the current tick.
    t = lm["evidence_tick"]
    unexplained = [f"t{t}.{fids[k - 1]} not explained by the ranked cases: "
                   f"{lm['fact_detail'][k - 1]}" for k in p.get("x", []) or []
                   if 1 <= k <= nf]
    bad_x = sum(1 for k in p.get("x", []) or [] if not 1 <= k <= nf)
    ctx = lm["context"]
    n_ids = [ctx[k - 1] for k in p.get("n", []) or [] if 1 <= k <= len(ctx)]
    bad_n = sum(1 for k in p.get("n", []) or [] if not 1 <= k <= len(ctx))
    sep = p.get("sep")
    sep_id = cases[sep - 1].get("case_id") if _is_int(sep) and 1 <= sep <= len(cases) else None
    if not hyps:
        unexplained.append(NO_CASE_FITS)
    payload = {"headline": "", "hypotheses": hyps, "unexplained": unexplained}
    info = {"g": p.get("g"), "sep": sep_id, "n": n_ids, "empty_ranking": not hyps,
            "bad_case_lines": bad_case, "bad_fact_lines": bad_fact,
            "bad_x_lines": bad_x, "bad_note_lines": bad_n,
            "bad_sep": sep is not None and sep_id is None}
    return payload, info


# ======================================================================
#  Compact verifier (Phase 2 commit 6): pass/fail per shown claim by line
#  number (amendment 1 item 3); narrow view, facts and claims only
# ======================================================================
def build_verification(sw: dict, templates: dict, *, facts, claims: dict,
                       evidence_tick: int) -> tuple[str, dict, dict]:
    """The compact verifier prompt (sw["ver_schema"] must be on). Facts: every
    fact of the evidence tick, in L1 order, as in Phase 1. Claims: at most the
    top 3, each with its case id, its cause (whole, or the first sentence with
    `ver_claims`) and its cited fact LINES. No confidence, no discriminator
    (human decision (b)). Returns (prompt, line_map, mock hint)."""
    if not sw["ver_schema"]:
        raise ValueError("build_verification is the ver_schema-on path")
    fids = [f.id for f in facts]
    line_of = {fid: i for i, fid in enumerate(fids, 1)}
    # same-tick supports are local (F3) or stamped with this tick: shown as
    # their line. A support stamped with an EARLIER tick (a late answer in real
    # time) is shown as that tick, `t83`, so it is not mistaken for an invented
    # id; anything else names no fact and is shown as `?`.
    def cite(x):
        tick, _, fid = x.rpartition(".")
        if not tick:
            return str(line_of[x]) if x in line_of else "?"
        if tick == f"t{evidence_tick}":
            return str(line_of[fid]) if fid in line_of else "?"
        return tick if tick.startswith("t") and tick[1:].isdigit() else "?"
    shown = list(claims.get("hypotheses", []))[:MAX_CLAIMS]
    lines = []
    for h in shown:
        cite_txt = ", ".join(cite(x) for x in h.get("supports", [])) or "none"
        cause = h.get("cause") or "?"
        if sw["ver_claims"]:
            cause = first_sentence(cause)
        lines.append(f"{h.get('case_ref') or 'model idea'}: {cause} [cites {cite_txt}]")
    rules = templates["rules_compact" if sw["ver_rules"] else "rules_full"]
    prompt = templates["body"].format(
        rules=rules,
        facts=_numbered([f"[{f.check}/{f.severity}] {f.detail}" for f in facts],
                        "(no facts)"),
        claims=_numbered(lines, "(no claims)"))
    line_map = {"kind": "verification", "evidence_tick": evidence_tick,
                "claims": [h.get("cause") for h in shown], "facts": fids,
                "fact_detail": [f.detail for f in facts]}
    hint = {"compact_ver": True, "n_claims": len(shown)}
    return prompt, line_map, hint


def over_limit(prompt: str, cap: int, guard_cpt: float | None) -> bool:
    """The guard's estimate (fitted chars per token) of prompt + answer cap
    reaching PROMPT_LIMIT. Used to FLAG a prompt; nothing is cut."""
    return bool(guard_cpt) and math.ceil(len(prompt) / guard_cpt) + cap >= PROMPT_LIMIT


def check_ver_answer_shape(p: dict) -> tuple[bool, str]:
    """`v` a list of [claim line, "p"|"f"]; `c` a line number or null if present."""
    v = p.get("v")
    if not isinstance(v, list):
        return False, 'missing verdict list "v"'
    for e in v:
        if not (isinstance(e, list) and len(e) == 2 and _is_int(e[0])
                and e[1] in ("p", "f")):
            return False, '"v" entries must be [claim line, "p" or "f"]'
    if p.get("c") is not None and not _is_int(p["c"]):
        return False, '"c" must be a fact line or null'
    return True, ""


def expand_ver_answer(p: dict, lm: dict) -> tuple[dict, dict]:
    """Line-number verdicts -> the single agent's verifier payload, so its
    Verifier.apply folds them unchanged. A shown claim with no verdict is NOT
    JUDGED: no check is written for it (apply leaves it alone) and it is
    counted, never as a pass. The first verdict for a line wins."""
    claims, fids = lm["claims"], lm["facts"]
    verdicts: dict[int, set] = {}
    bad = 0
    for line, verdict in p.get("v", []):
        if not 1 <= line <= len(claims):
            bad += 1
            continue
        verdicts.setdefault(line, set()).add(verdict)
    # One verdict per claim. A claim given both "p" and "f" contradicts itself:
    # it is NOT JUDGED (neither pass nor fail), counted.
    conflicting = sorted(l for l, v in verdicts.items() if len(v) > 1)
    judged = {l for l, v in verdicts.items() if len(v) == 1}
    checks = [{"claim": claims[l - 1],
               "verdict": "fail" if verdicts[l] == {"f"} else "pass"}
              for l in sorted(judged)]
    c = p.get("c")
    contra = None
    if _is_int(c) and 1 <= c <= len(fids):
        contra = f"t{lm['evidence_tick']}.{fids[c - 1]} {lm['fact_detail'][c - 1]}"
    failed = any(x["verdict"] == "fail" for x in checks)
    not_judged = [i for i in range(1, len(claims) + 1) if i not in judged]
    # An empty or partial verdict must not read as agreement (amendment 1
    # item 3): `agree` is False when a claim failed, None (unknown) when any
    # shown claim was not judged, True only when every claim passed.
    agree = False if failed else (None if not_judged else True)
    payload = {"checks": checks, "strongest_contradiction": contra,
               "revised_confidence": None, "agree": agree}
    info = {"not_judged": not_judged, "conflicting": conflicting,
            "bad_claim_lines": bad,
            "bad_fact_line": c is not None and contra is None,
            "failed": [x["claim"] for x in checks if x["verdict"] == "fail"]}
    return payload, info
