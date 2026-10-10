"""app/grammar.py: the generic engine ported from FieldMind."""
import random

from app.grammar import accepts, choice, lit, longest, ref, sample, to_gbnf


def _tool_grammar():
    rules = {}
    choice(rules, "t", ["water_leak", "fire_smoke"])
    choice(rules, "s", ["low", "high"])
    rules["root"] = [[lit('{"tool":"no_op"}')],
                     [lit('{"tool":"update_report","args":{"t":"'), ref("t"), lit('"}}')],
                     [lit('{"tool":"update_report","args":{"t":"'), ref("t"),
                      lit('","s":"'), ref("s"), lit('"}}')]]
    return rules


def test_accepts_exactly_the_language():
    g = _tool_grammar()
    assert accepts(g, '{"tool":"no_op"}')
    assert accepts(g, '{"tool":"update_report","args":{"t":"water_leak","s":"high"}}')
    assert not accepts(g, '{"tool":"update_report","args":{"t":"flood"}}')       # not an enum value
    assert not accepts(g, ' {"tool":"no_op"}')                                  # no whitespace
    assert not accepts(g, '{"tool":"no_op"}\n')


def test_every_sample_is_accepted_and_longest_is_the_longest():
    g, rng = _tool_grammar(), random.Random(0)
    samples = {sample(g, rng) for _ in range(200)}
    assert all(accepts(g, s) for s in samples)
    assert len(samples) == 1 + 2 + 4
    assert accepts(g, longest(g)) and len(longest(g)) == max(map(len, samples))


def test_gbnf_text():
    text = to_gbnf({"root": [[lit('{"a":'), ref("my_val"), lit("}")]],
                    "my_val": [[lit('"x"')], [lit("1")]]})
    assert text.splitlines()[0] == 'root ::= "{\\"a\\":" my-val "}"'
    assert text.splitlines()[1] == 'my-val ::= "\\"x\\"" | "1"'


# ---- the incident tool-call grammar (no whitespace) ---------------------------------
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from app.form import load_schema  # noqa: E402
from app.grammar import incident_gbnf  # noqa: E402

_TOK = re.compile(r'\s*("(?:\\.|[^"\\])*"|\[(?:\\.|[^\]\\])*\]|[A-Za-z0-9-]+|\{\d+,\d+\}|[()|*+?])')


def gbnf_to_regex(text: str, start: str = "root") -> re.Pattern:
    """Our GBNF subset (no recursion) as a Python regex, to test the grammar without a model."""
    rules = dict(line.split(" ::= ", 1) for line in text.strip().splitlines())
    memo: dict = {}

    def expand(name):
        if name not in memo:
            out, pos, body = [], 0, rules[name]
            while pos < len(body):
                m = _TOK.match(body, pos)
                assert m, f"cannot read {body[pos:]!r} in rule {name}"
                tok, pos = m.group(1), m.end()
                if tok.startswith('"'):
                    out.append(re.escape(tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")))
                elif tok.startswith("[") or tok.startswith("{") or tok in "()|*+?":
                    out.append(tok)
                else:
                    out.append(f"(?:{expand(tok)})")
            memo[name] = "".join(out)
        return memo[name]
    return re.compile(expand(start))


def test_every_gold_answer_is_in_the_grammar_and_whitespace_is_not():
    rx = gbnf_to_regex(incident_gbnf(load_schema()))
    cases = Path(__file__).resolve().parent.parent / "eval" / "nlu" / "cases.jsonl"
    n = 0
    for line in cases.read_text().splitlines():
        for t in json.loads(line)["turns"]:
            g = t["gold"]
            call = {"tool": g["tool"], **({"args": g["args"]} if g["args"] else {})}
            compact = json.dumps(call, separators=(",", ":"), ensure_ascii=False)
            assert rx.fullmatch(compact), compact
            assert not rx.fullmatch(json.dumps(call, ensure_ascii=False)) or compact == json.dumps(call)
            n += 1
    assert n == 250


def test_grammar_rejects_what_the_model_must_not_write():
    rx = gbnf_to_regex(incident_gbnf(load_schema()))
    ok = '{"tool":"update_report","args":{"t":"fire","l":"lab"}}'
    assert rx.fullmatch(ok)
    for bad in ['{"tool":"update_report","args":{"t":"flood"}}',            # not an allowed type
                '{"tool": "update_report","args":{"t":"fire"}}',           # a space
                '{"tool":"update_report","args":{}}',                      # update with nothing
                '{"tool":"update_report","args":{"x":"1"}}',               # unknown field
                '{"tool":"update_report","args":{"l":"' + "a" * 61 + '"}}',  # too long
                '{"tool":"update_report","args":{"w":["a","b","c","d","e"]}}',  # too many items
                '{"tool":"delete_everything"}']:
        assert not rx.fullmatch(bad), bad
