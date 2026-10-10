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
