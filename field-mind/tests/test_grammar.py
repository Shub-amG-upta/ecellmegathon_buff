"""multi.grammar (fieldmind/multi/grammar.py): the GBNF sent with every model
job. Outcome-based: what the grammars accept and reject, what the gate makes of
every sentence they allow, and what the server request of a real run carries."""

import json
import random
import re
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.multi import compact, grammar as G
from fieldmind.runtime.llm_backend import LlamaServerBackend, MockBackend
from fieldmind.schemas import TAGS

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_B02_tube_leak_fast").exists(),
                               reason="dev episodes not generated")


def from_gbnf(text: str) -> dict:
    """The GBNF text back into rules, so a test can ask the grammar the SERVER
    was sent what it accepts (not the rules the code meant to send)."""
    rules = {}
    for line in text.strip().splitlines():
        name, _, body = line.partition(" ::= ")
        alts, cur = [], []
        for tok in re.findall(r'"(?:[^"\\]|\\.)*"|\||[\w-]+', body):
            if tok == "|":
                alts.append(cur)
                cur = []
            elif tok.startswith('"'):
                cur.append(("lit", json.loads(tok)))
            else:
                cur.append(("ref", tok))
        rules[name] = alts + [cur]
    return rules


def _lm(n_facts, n_cases, n_ctx):
    return {"cases": [{"case_id": f"RCA-{i:02d}", "root_cause": f"cause {i}"}
                      for i in range(1, n_cases + 1)],
            "facts": [f"F{i}" for i in range(1, n_facts + 1)],
            "fact_detail": ["d"] * n_facts, "conf": [0.5] * n_cases,
            "context": [f"n{i}" for i in range(n_ctx)], "evidence_tick": 7}


# ------------------------------------------------------------------ diagnosis
def test_diagnosis_grammar_accepts_the_format_and_nothing_around_it():
    g = from_gbnf(G.to_gbnf(G.diagnosis(8, 4, 6, ["A", "C", "C", "E"])))
    good = '{"g":"A","r":[[2,[1,3]],[1,[1]]],"sep":2,"n":[1],"x":[]}'
    assert G.accepts(g, good)
    assert G.accepts(g, '{"g":"E","r":[],"sep":null,"n":[],"x":[8]}')     # no case fits
    for bad in ("```json\n" + good,                      # code fence (Gemma 1B, board)
                good + "\n", " " + good,                 # anything around it
                good.replace(",", ", "),                 # whitespace inside
                '{"g":"A","r":[[5,[1]]],"sep":null,"n":[],"x":[]}',          # case line not shown
                '{"g":"A","r":[[1,[9]]],"sep":null,"n":[],"x":[]}',          # fact line not shown
                '{"g":"A","r":[[2,[1]],[2,[3]]],"sep":null,"n":[],"x":[]}',  # a case twice
                '{"g":"A","r":[[2,[3,1]]],"sep":null,"n":[],"x":[]}',        # not ascending
                '{"g":"A","r":[[2,[1,1]]],"sep":null,"n":[],"x":[]}',        # a fact twice
                '{"g":"B","r":[],"sep":null,"n":[],"x":[]}',                 # letter not shown
                '{"g":"A","r":[],"sep":null,"n":[7],"x":[]}',                # note line not shown
                '{"r":[[2,[1]]]}'):                                          # partial object
        assert not G.accepts(g, bad), bad


@pytest.mark.parametrize("n_facts,n_cases,n_ctx", [(8, 4, 6), (9, 4, 8), (1, 1, 0), (0, 0, 0),
                                                    (3, 2, 1)])
def test_every_diagnosis_the_grammar_allows_is_clean_for_the_gate(n_facts, n_cases, n_ctx):
    g = G.diagnosis(n_facts, n_cases, n_ctx, ["A", "B", "C", "D"][:n_cases])
    lm, rng = _lm(n_facts, n_cases, n_ctx), random.Random(n_facts * 100 + n_cases)
    for s in {G.longest(g)} | {G.sample(g, rng) for _ in range(400)}:
        p = json.loads(s)
        assert compact.check_diag_answer_shape(p) == (True, ""), s
        payload, info = compact.expand_diag_answer(p, lm)
        assert (info["bad_case_lines"], info["bad_fact_lines"], info["bad_x_lines"],
                info["bad_note_lines"], info["bad_sep"]) == (0, 0, 0, 0, False), s
        assert len(payload["hypotheses"]) == len(p["r"]) <= G.MAX_RANKED
        assert all(len(set(h["supports"])) == len(h["supports"]) <= G.MAX_FACTS_PER_CASE
                   for h in payload["hypotheses"])


# ------------------------------------------------------------------ verifier
def test_every_verdict_the_grammar_allows_judges_each_claim_once():
    for n_claims, n_facts in ((3, 12), (1, 1), (2, 0), (0, 4)):
        g = G.verification(n_claims, n_facts)
        lm = {"claims": [f"claim {i}" for i in range(n_claims)],
              "facts": [f"F{i}" for i in range(1, n_facts + 1)],
              "fact_detail": ["d"] * n_facts, "evidence_tick": 3}
        rng = random.Random(n_claims)
        for s in {G.longest(g)} | {G.sample(g, rng) for _ in range(200)}:
            p = json.loads(s)
            assert compact.check_ver_answer_shape(p) == (True, ""), s
            payload, info = compact.expand_ver_answer(p, lm)
            assert (info["not_judged"], info["conflicting"], info["bad_claim_lines"],
                    info["bad_fact_line"]) == ([], [], 0, False), s
            assert len(payload["checks"]) == n_claims
    g = from_gbnf(G.to_gbnf(G.verification(3, 12)))
    assert G.accepts(g, '{"v":[[1,"p"],[2,"f"],[3,"f"]],"c":12}')
    for bad in ('{"v":[[1,"p"]],"c":null}',                       # two claims not judged
                '{"v":[[1,"p"],[2,"f"],[3,"f"]],"c":13}',         # fact line not shown
                '{"v":[[1,"p"],[1,"f"],[3,"f"]],"c":null}',       # a claim twice
                '```json\n{"v":["RCA-01","f"], "c": "1. Feed'):   # the board's free answer
        assert not G.accepts(g, bad), bad


# ------------------------------------------------------------------ text reader
def _vocab():
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    return compact.NoteVocab(TAGS, ["BFP_A", "FEED_VALVE"], cfg["multi"]["text_reader"])


def test_every_note_reading_the_grammar_allows_passes_the_gates_vocabulary_check():
    v = _vocab()
    g = G.note(v.kinds, sorted(v.subjects), v.states, v.max_pairs)
    rng = random.Random(5)
    for s in {G.longest(g)} | {G.sample(g, rng) for _ in range(400)}:
        assert compact.check_note_answer(json.loads(s), v) == (True, ""), s
    g = from_gbnf(G.to_gbnf(g))
    assert G.accepts(g, '{"k":"OTHER","s":[]}')
    for bad in ('{"k":"OTHER","s":[["ASH_SLURRY_PUMP","MENTIONED"]]}',   # subject (board run)
                '{"k":"OBS","s":[["drum_level","swing"]]}',              # state (board run)
                '{"k":"NOTE","s":[]}',
                '{"k":"OBS","s":[' + ",".join(['["BFP_A","LOW"]'] * (v.max_pairs + 1)) + "]}"):
        assert not G.accepts(g, bad), bad


def test_longest_answers_stay_within_the_character_budget_of_their_caps():
    """Characters, not tokens: the token counts are measured on the lanes'
    own tokenizers (report). A longer longest answer needs that re-measured."""
    v = yaml.safe_load((ROOT / "configs/base.yaml").read_text())["multi"]["text_reader"]
    assert len(G.longest(G.diagnosis(9, 4, 8, list("ABCD")))) == 82
    assert len(G.longest(G.verification(compact.MAX_CLAIMS, 25))) == 40
    assert v["max_pairs"] == 3


# ------------------------------------------------------------------ the request
def test_server_request_is_unchanged_without_a_grammar_and_carries_one_with_it():
    b = LlamaServerBackend(url="http://x", lane="npu", model_file="m.gguf")
    plain = b._body("PROMPT", 60)
    assert plain == {"messages": [{"role": "user", "content": "PROMPT"}], "max_tokens": 60,
                     "temperature": 0.0, "seed": 0, "cache_prompt": False, "stream": False}
    assert b._body("PROMPT", 60, 'root ::= "x"\n') == {**plain, "grammar": 'root ::= "x"\n'}


def _cfg(grammar_on: bool):
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/fast.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["grammar"] = grammar_on
    return cfg


def _recorded_calls(monkeypatch, grammar_on: bool, able: bool = True) -> list:
    """Every model call of one dev episode as (role, prompt, kwargs beyond the
    usual four), on a mock that can (or cannot) take a grammar."""
    calls, orig = [], MockBackend.generate

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None, **extra):
        calls.append((role, prompt, extra))
        return orig(self, prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)

    monkeypatch.setattr(MockBackend, "generate", generate)
    monkeypatch.setattr(MockBackend, "supports_grammar", able, raising=False)
    run = run_episode(Episode(DEV / "dev_B02_tube_leak_fast"), _cfg(grammar_on), arch="multi")
    return calls, run


@needs_dev
def test_every_model_call_of_a_run_is_sent_the_grammar_of_its_own_prompt(monkeypatch):
    calls, run = _recorded_calls(monkeypatch, grammar_on=True)
    assert run["multi"]["grammar"] is True
    roles = {r for r, _, _ in calls}
    assert {"diagnostician", "verifier"} <= roles and len(roles) == 3      # + the text reader
    assert all(set(extra) == {"grammar"} for _, _, extra in calls)
    n_lines = lambda prompt, head: len(re.findall(
        r"^\d+\. ", prompt.split(head, 1)[1].split("\n\n", 1)[0], flags=re.M))
    seen = set()
    for role, prompt, extra in calls:
        g = from_gbnf(extra["grammar"])
        if role == "diagnostician" and "Previous output" not in prompt:
            n = n_lines(prompt, "FACTS:\n")
            tail = lambda k: G.accepts(g, k, start="unexpl")
            # the last fact line shown is allowed, the next one is not
            assert tail(f"[{n}]") and not tail(f"[{n + 1}]"), (n, prompt[:200])
            seen.add("diagnostician")
        elif role == "verifier":
            n = n_lines(prompt, "CLAIMS:\n")
            verdicts = "".join(("," if k > 1 else "") + f'[{k},"p"]' for k in range(1, n + 1))
            assert G.accepts(g, '{"v":[' + verdicts + '],"c":null}')
            assert not G.accepts(g, '{"v":[],"c":null}') or n == 0
            seen.add("verifier")
        elif role not in ("diagnostician",):
            assert G.accepts(g, '{"k":"OBS","s":[["drum_level","LOW"]]}')
            assert not G.accepts(g, '{"k":"OBS","s":[["ASH_SLURRY_PUMP","LOW"]]}')
            seen.add("text_reader")
    assert seen == {"diagnostician", "verifier", "text_reader"}


@needs_dev
def test_no_grammar_reaches_a_backend_when_off_or_when_it_cannot_take_one(monkeypatch):
    calls, run = _recorded_calls(monkeypatch, grammar_on=False)
    assert calls and all(extra == {} for _, _, extra in calls) and run["multi"]["grammar"] is False
    calls, _ = _recorded_calls(monkeypatch, grammar_on=True, able=False)
    assert calls and all(extra == {} for _, _, extra in calls)
