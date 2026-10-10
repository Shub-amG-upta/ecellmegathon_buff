"""
=============================================================================
 ANSWER GRAMMARS  (multi.grammar; HUMAN DECISION 2026-10-05: "enforce the
 strict limits on the server")
=============================================================================

One grammar per model job, built from what THAT prompt showed, sent to
llama-server with the request (`grammar`, GBNF). The server then cannot emit
anything else: no code fence, no prose, no line number that was not shown, no
word outside the note vocabulary, and no answer longer than the grammar's
longest sentence, which is kept under the job's answer cap so an answer is
never cut off mid-JSON.

  diagnosis     {"g":"A","r":[[2,[1,3]],[1,[1]]],"sep":2,"n":[1],"x":[]}
  verification  {"v":[[1,"p"],[2,"f"]],"c":3}
  note          {"k":"OBS","s":[["drum_level","LOW"]]}

No whitespace anywhere. Lists of line numbers are strictly ascending (so no
line twice); ranked case lines are distinct; the verifier judges every shown
claim once, in order.

WHAT THIS CHANGES IN A MEASUREMENT. With a grammar the wrong-format rate is
zero by construction and no longer measures the model; the gate's range checks
(bad_case_lines, bad_fact_lines, unknown subject) can no longer fire either.
What is left to measure is whether the answer is RIGHT. Off (`multi.grammar:
false`) = the earlier behaviour, byte for byte.

A grammar here is a dict {rule name: [alternative, ...]}; an alternative is a
list of ("lit", text) and ("ref", rule name). No rule refers back to itself
(every language is finite), so `accepts`, `longest` and `sample` are exact.

Stdlib only (copied to the device with the rest of fieldmind/).
=============================================================================
"""

from __future__ import annotations

# DESIGN CHOICE (not derived): how many items each list may hold. They exist so
# the LONGEST answer the grammar allows fits the job's cap (diagnostician 60,
# verifier 30, text reader 50; CLAUDE.md), measured on the lanes' own
# tokenizers (reports/multi_phase4b_grammar.md). Range: 1 to the number of
# lines shown. Affects: how many facts a ranked case may cite, how many notes
# and unexplained facts an answer may name. The format without a grammar has no
# such limit (and is cut off by the cap instead).
MAX_RANKED = 3          # = compact.MAX_RANKED (B' worst case measured with 3 cases)
MAX_FACTS_PER_CASE = 3
MAX_NOTES_USED = 2
MAX_UNEXPLAINED = 2


def lit(text: str) -> tuple:
    return ("lit", text)


def ref(name: str) -> tuple:
    return ("ref", name)


def _ascending(rules: dict, name: str, n: int, most: int) -> str:
    """Rules for 1 to `most` strictly ascending numbers from 1..n, comma
    separated. Returns the start rule's name (callers add the empty list)."""
    for depth in range(1, most + 1):
        for lo in range(1, n + 1):
            alts = []
            for j in range(lo, n + 1):
                alts.append([lit(str(j))])
                if depth > 1 and j < n:
                    alts.append([lit(f"{j},"), ref(f"{name}{depth - 1}x{j + 1}")])
            rules[f"{name}{depth}x{lo}"] = alts
    return f"{name}{most}x1"


def _number_list(rules: dict, name: str, n: int, most: int) -> None:
    """`name` ::= "[]" or "[" ascending numbers "]"."""
    most = min(most, n)
    if most < 1:
        rules[name] = [[lit("[]")]]
        return
    start = _ascending(rules, name + "s", n, most)
    rules[name] = [[lit("[]")], [lit("["), ref(start), lit("]")]]


def diagnosis(n_facts: int, n_cases: int, n_context: int, letters: list[str],
              with_group: bool = True) -> dict:
    """Answer format B' for a prompt that showed `n_facts` fact lines,
    `n_cases` case lines (their group `letters`) and `n_context` note and
    record lines."""
    rules: dict = {}
    _number_list(rules, "facts", n_facts, MAX_FACTS_PER_CASE)
    _number_list(rules, "notes", n_context, MAX_NOTES_USED)
    _number_list(rules, "unexpl", n_facts, MAX_UNEXPLAINED)
    # ranked cases: 0 to MAX_RANKED entries, case lines distinct (any order)
    most = min(MAX_RANKED, n_cases)

    def ranked(used: tuple) -> str:
        name = "rank" + "".join(map(str, used))
        if name not in rules:
            alts = []
            for c in range(1, n_cases + 1):
                if c in used:
                    continue
                entry = [lit(f"[{c},"), ref("facts"), lit("]")]
                alts.append(entry)
                if len(used) + 1 < most:
                    alts.append(entry + [lit(","), ref(ranked(used + (c,)))])
            rules[name] = alts
        return name

    r = [[lit("[]")]] + ([[lit("["), ref(ranked(())), lit("]")]] if most else [])
    rules["r"] = r
    rules["g"] = [[lit(x)] for x in (sorted(set(letters)) or ["?"])]
    rules["sep"] = [[lit("null")]] + [[lit(str(c))] for c in range(1, n_cases + 1)]
    if not with_group:                      # multi.compact.group_letters off
        del rules["g"]
        rules["root"] = [[lit('{"r":'), ref("r"), lit(',"sep":'), ref("sep"),
                          lit(',"n":'), ref("notes"), lit(',"x":'), ref("unexpl"), lit("}")]]
        return rules
    rules["root"] = [[lit('{"g":"'), ref("g"), lit('","r":'), ref("r"),
                      lit(',"sep":'), ref("sep"), lit(',"n":'), ref("notes"),
                      lit(',"x":'), ref("unexpl"), lit("}")]]
    return rules


def verification(n_claims: int, n_facts: int) -> dict:
    """One verdict for every shown claim, in order; `c` a fact line or null."""
    rules = {"pf": [[lit("p")], [lit("f")]],
             "c": [[lit("null")]] + [[lit(str(k))] for k in range(1, n_facts + 1)]}
    body = []
    for k in range(1, n_claims + 1):
        body += [lit(("," if k > 1 else "") + f'[{k},"'), ref("pf"), lit('"]')]
    rules["root"] = [[lit('{"v":[')] + body + [lit('],"c":'), ref("c"), lit("}")]]
    return rules


def note(kinds: list[str], subjects: list[str], states: list[str], max_pairs: int) -> dict:
    """A kind and 0 to `max_pairs` [subject, state] pairs, vocabulary only."""
    rules = {"kind": [[lit(k)] for k in kinds],
             "subject": [[lit(s)] for s in sorted(subjects)],
             "state": [[lit(s)] for s in states],
             "pair": [[lit('["'), ref("subject"), lit('","'), ref("state"), lit('"]')]]}
    pairs = [[lit("[]")]]
    for n in range(1, max_pairs + 1):
        alt = [lit("[")]
        for i in range(n):
            alt += ([lit(",")] if i else []) + [ref("pair")]
        pairs.append(alt + [lit("]")])
    rules["pairs"] = pairs
    rules["root"] = [[lit('{"k":"'), ref("kind"), lit('","s":'), ref("pairs"), lit("}")]]
    return rules


# ======================================================================
#  The grammar as GBNF text (what the server gets), and exact host-side
#  tools over the same rules (tests, the token-fit measurement)
# ======================================================================
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
        """Every position the symbols can reach from i."""
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
    """The longest sentence, by characters (the token-fit check tokenizes it)."""
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
