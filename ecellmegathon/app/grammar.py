"""
GBNF grammars built in code, sent to llama-server with the request.

Ported from FieldMind (fieldmind/multi/grammar.py): the generic engine only.
FieldMind's three answer builders (diagnosis, verification, note) were specific
to the boiler agent and were dropped. On the board, per-request grammars took
FieldMind's unusable-answer rate from 25-100% to 0.

Primary path in this project: send a JSON schema (`json_schema=` on the
backend) and let llama-server compile it, because report fields hold free text
and this engine only describes finite languages. Use this engine when the
output must be shorter than what the schema compiler produces (it allows
optional whitespace) or when the schema route misbehaves at our llama.cpp build.

A grammar is a dict {rule name: [alternative, ...]}; an alternative is a list
of ("lit", text) and ("ref", rule name). No rule refers back to itself (every
language is finite), so `accepts`, `longest` and `sample` are exact.

Stdlib only.
"""

from __future__ import annotations


def lit(text: str) -> tuple:
    return ("lit", text)


def ref(name: str) -> tuple:
    return ("ref", name)


def choice(rules: dict, name: str, values: list[str]) -> str:
    """`name` ::= one of `values`, as literal text. Returns `name`."""
    rules[name] = [[lit(v)] for v in values]
    return name


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_gbnf(rules: dict) -> str:
    """GBNF with `root` first. Rule names are made of letters, digits and
    dashes (GBNF's rule-name alphabet)."""
    name = lambda n: n.replace("_", "-")
    order = ["root"] + sorted(k for k in rules if k != "root")
    out = []
    for k in order:
        alts = [" ".join(_quote(v) if kind == "lit" else name(v) for kind, v in alt)
                for alt in rules[k]]
        out.append(f"{name(k)} ::= " + " | ".join(alts))
    return "\n".join(out) + "\n"


def accepts(rules: dict, text: str, start: str = "root") -> bool:
    """True when `text` is a sentence of the grammar (exact)."""
    def ends(sym_list: tuple, i: int) -> set:
        pos = {i}
        for kind, v in sym_list:
            nxt = set()
            for p in pos:
                if kind == "lit":
                    if text.startswith(v, p):
                        nxt.add(p + len(v))
                else:
                    for alt in rules[v]:
                        nxt |= ends(tuple(alt), p)
            pos = nxt
            if not pos:
                break
        return pos
    return len(text) in ends((("ref", start),), 0)


def longest(rules: dict, start: str = "root") -> str:
    """The longest sentence, by characters (tokenize it to check the answer cap)."""
    memo: dict = {}

    def best(name: str) -> str:
        if name not in memo:
            memo[name] = max(("".join(v if kind == "lit" else best(v) for kind, v in alt)
                              for alt in rules[name]), key=len)
        return memo[name]
    return best(start)


def sample(rules: dict, rng, start: str = "root") -> str:
    """A random sentence (uniform over alternatives at each rule)."""
    return "".join(v if kind == "lit" else sample(rules, rng, v)
                   for kind, v in rng.choice(rules[start]))
