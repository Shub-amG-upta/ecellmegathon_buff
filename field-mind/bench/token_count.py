#!/usr/bin/env python3
"""
Real tokenizer counts for prompts and answers (multi-agent Phase 2, gate G3).

    .venv/bin/python bench/token_count.py --fetch
    .venv/bin/python bench/token_count.py --prompts results/x/prompts.jsonl
    .venv/bin/python bench/token_count.py --answers

Counts what the model actually receives: the prompt as ONE user message in
the model's own chat template (that is what LlamaServerBackend._body sends to
/v1/chat/completions), tokenized with the model's tokenizer. Not chars/4.

Tokenizers: the four model-choice candidates (reports/phase0b_lanes_model_
choice.md). `tokenizer.json` + `tokenizer_config.json` (+ `chat_template.jinja`
when the repo keeps the template there) are fetched from Hugging Face at a
PINNED revision into bench/tokenizers/<name>/ (gitignored: licence terms) and
checked against the sha256 in the committed bench/tokenizers/manifest.json.
The two gated repos (Llama 3.2, Gemma 3) are read from ungated mirrors unless
HF_TOKEN is set (HUMAN DECISION, Phase 2 plan review).

NOT VERIFIED HERE: that these tokenizers equal the ones inside the GGUF files
on the board. Check against each lane's /tokenize or timings.prompt_n when the
board is attached.

Host-side tooling (needs `tokenizers` and `jinja2`); never imported by
fieldmind/.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import statistics as st
import sys
import urllib.request
from pathlib import Path

DIR = Path(__file__).parent / "tokenizers"
MANIFEST = DIR / "manifest.json"
FILES = ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja")

# name -> (official repo, ungated mirror or None, template variables)
MODELS = {
    "llama3.2-3b": ("meta-llama/Llama-3.2-3B-Instruct", "unsloth/Llama-3.2-3B-Instruct", {}),
    "qwen3-1.7b": ("Qwen/Qwen3-1.7B", None, {"enable_thinking": False}),
    "gemma3-1b": ("google/gemma-3-1b-it", "unsloth/gemma-3-1b-it", {}),
    "qwen2.5-0.5b": ("Qwen/Qwen2.5-0.5B-Instruct", None, {}),
}

# CITED: CLAUDE.md "Rules that must not be broken" / plan p.3 rule 4, p.5.
PROMPT_CAP = 1280
ANSWER_CAPS = {"diagnostician": 60, "verifier": 30, "text_reader": 50, "query": 120}


def cap_for(role: str) -> int | None:
    """Answer cap of a role; compact roles (`diagnostician_ids`) share their
    agent's cap."""
    for agent, cap in ANSWER_CAPS.items():
        if role.startswith(agent):
            return cap
    return None


def over_budget(prompt_tokens: int, role: str) -> bool:
    """Gate G3 (HUMAN DECISION c): prompt tokens + the agent's answer cap must
    be < 1280, because a fixed 1280 context holds both."""
    return prompt_tokens + (cap_for(role) or 0) >= PROMPT_CAP


# ----------------------------------------------------------------- fetch
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get(url: str, token: str | None) -> bytes | None:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch() -> None:
    token = os.environ.get("HF_TOKEN")
    old = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest = {}
    for name, (official, mirror, _) in MODELS.items():
        repo = official if (token or mirror is None) else mirror
        info = json.loads(_get(f"https://huggingface.co/api/models/{repo}", token))
        rev = old.get(name, {}).get("revision") if old.get(name, {}).get("repo") == repo \
            else None
        rev = rev or info["sha"]                    # pin: first fetch records it
        out = DIR / name
        out.mkdir(parents=True, exist_ok=True)
        files = {}
        for fn in FILES:
            data = _get(f"https://huggingface.co/{repo}/resolve/{rev}/{fn}", token)
            if data is None:
                continue
            (out / fn).write_bytes(data)
            files[fn] = hashlib.sha256(data).hexdigest()
        if "tokenizer.json" not in files:
            raise SystemExit(f"{name}: {repo}@{rev} has no tokenizer.json -- not "
                             f"substituting another model; fix MODELS")
        manifest[name] = {"repo": repo, "official_repo": official,
                          "mirror_used": repo != official, "revision": rev,
                          "files": files}
        print(f"{name:14s} {repo}@{rev[:10]}  {sorted(files)}")
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


# ----------------------------------------------------------------- count
class Counter:
    """One model's tokenizer and chat template."""

    def __init__(self, name: str):
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from tokenizers import Tokenizer

        if not MANIFEST.exists():
            raise SystemExit("no manifest: run bench/token_count.py --fetch")
        man = json.loads(MANIFEST.read_text())[name]
        d = DIR / name
        for fn, sha in man["files"].items():
            if not (d / fn).exists() or _sha(d / fn) != sha:
                raise SystemExit(f"{name}/{fn}: missing or sha256 differs from the "
                                 f"manifest; run --fetch")
        self.name = name
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        cfg = json.loads((d / "tokenizer_config.json").read_text())
        tpl = (d / "chat_template.jinja").read_text() \
            if (d / "chat_template.jinja").exists() else cfg["chat_template"]

        def text(v):
            return v["content"] if isinstance(v, dict) else (v or "")

        def raise_exception(msg):
            raise ValueError(msg)

        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        env.globals["raise_exception"] = raise_exception
        env.globals["strftime_now"] = lambda f: datetime.date(2026, 10, 3).strftime(f)
        self.template = env.from_string(tpl)
        self.vars = dict(MODELS[name][2], bos_token=text(cfg.get("bos_token")),
                         eos_token=text(cfg.get("eos_token")))

    def wrap(self, prompt: str, **override) -> str:
        """The prompt as a single user message, generation prompt added."""
        return self.template.render(
            messages=[{"role": "user", "content": prompt}],
            add_generation_prompt=True, **{**self.vars, **override})

    def prompt_tokens(self, prompt: str, **override) -> int:
        # the template writes BOS itself where the model has one
        return len(self.tok.encode(self.wrap(prompt, **override),
                                   add_special_tokens=False).ids)

    def raw_tokens(self, text: str) -> int:
        """Tokens of bare text (an answer, or a prompt without its template)."""
        return len(self.tok.encode(text, add_special_tokens=False).ids)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def count_prompts(path: str) -> dict:
    rows = [json.loads(l) for l in open(path)]
    out = {}
    for name in MODELS:
        c = Counter(name)
        by_role: dict[str, list] = {}
        for r in rows:
            n = c.prompt_tokens(r["prompt"])
            by_role.setdefault(r["role"], []).append((n, len(r["prompt"]) / max(1, c.raw_tokens(r["prompt"]))))
        out[name] = {}
        for role, xs in sorted(by_role.items()):
            toks = [x[0] for x in xs]
            out[name][role] = {
                "n": len(toks), "mean": round(st.mean(toks), 1),
                "p95": pct(toks, 0.95), "max": max(toks),
                "over_1280": sum(t >= PROMPT_CAP for t in toks),
                "answer_cap": cap_for(role),
                "over_1280_with_cap": sum(over_budget(t, role) for t in toks),
                "min_chars_per_token": round(min(x[1] for x in xs), 3),
            }
    return out


# ------------------------------------------------- the ID-only answer check
def _ans(obj, compact=True) -> str:
    return json.dumps(obj, separators=(",", ":")) if compact else json.dumps(obj)


def answer_cases() -> dict[str, str]:
    """Legal ID-only diagnosis answers (plan p.13), worst case first. Tick 143
    is a three-digit tick; dev episodes run to tick ~170."""
    F = lambda *i: [f"t143.F{k}" for k in i]
    L = lambda *i: [f"F{k}" for k in i]
    three = lambda cites: [["RCA-11", 0.6, cites(1, 2)], ["RCA-07", 0.3, cites(1, 2)],
                           ["RCA-09", 0.2, cites(1, 2)]]
    return {
        "WORST: 3 cases x 2 stamped cites, compact":
            _ans({"g": "A", "r": three(F), "sep": "RCA-11", "n": ["N3"], "x": []}),
        "same, with spaces (default json.dumps)":
            _ans({"g": "A", "r": three(F), "sep": "RCA-11", "n": ["N3"], "x": []}, False),
        "plan p.13 example as printed (2 cases, 3 cites)":
            '{"g": "W",\n "r": [["RCA-01", 0.6, ["t84.F1", "t84.F2"]], ["RCA-16", 0.3, ["t84.F2"]]],\n'
            ' "sep": "RCA-01", "n": ["N3"], "x": []}',
        "option: local fact ids (F1), 3 x 2":
            _ans({"g": "A", "r": three(L), "sep": "RCA-11", "n": ["N3"], "x": []}),
        "option: stamped, 3 cases x 1 cite":
            _ans({"g": "A", "r": [[c, p, f[:1]] for c, p, f in three(F)],
                  "sep": "RCA-11", "n": ["N3"], "x": []}),
        "option: stamped, 2 cases x 2 cites":
            _ans({"g": "A", "r": three(F)[:2], "sep": "RCA-11", "n": ["N3"], "x": []}),
        "option: cites once for the whole answer (stamped x2), 3 cases":
            _ans({"g": "A", "r": [["RCA-11", 0.6], ["RCA-07", 0.3], ["RCA-09", 0.2]],
                  "f": F(1, 2), "sep": "RCA-11", "n": ["N3"], "x": []}),
        "option: local ids, no confidences, 3 x 2":
            _ans({"g": "A", "r": [[c, f] for c, _, f in three(L)],
                  "sep": "RCA-11", "n": ["N3"], "x": []}),
    }


def count_answers() -> dict:
    cases = answer_cases()
    counters = {n: Counter(n) for n in MODELS}
    return {label: {"chars": len(text),
                    **{n: c.raw_tokens(text) for n, c in counters.items()}}
            for label, text in cases.items()}


# ------------------------------------------- independent route: llama.cpp
# llama.cpp's own tokenizer (a different implementation, and the one the
# board runs), on the vocab-only GGUFs from its repo (models/). There is no
# Gemma 3 vocab file there, so Gemma is not cross-checked. The qwen2 vocab
# has no <think> token, so Qwen3 is cross-checked on bare text only.
LLAMA_CPP_VOCAB = {
    "llama3.2-3b": ("ggml-vocab-llama-bpe.gguf", True),
    "qwen2.5-0.5b": ("ggml-vocab-qwen2.gguf", True),
    "qwen3-1.7b": ("ggml-vocab-qwen2.gguf", False),     # bare text only
}


def llama_cpp_count(gguf: Path, text: str) -> int:
    import subprocess
    r = subprocess.run(["llama-tokenize", "-m", str(gguf), "--stdin", "--ids",
                        "--no-bos", "--no-escape", "--log-disable"],
                       input=text, capture_output=True, text=True, timeout=120)
    line = r.stdout.strip().splitlines()[-1]
    return len(json.loads(line))


def crosscheck(path: str, vocab_dir: str, every: int) -> list[dict]:
    rows = [json.loads(l) for l in open(path)][::every]
    texts = [("prompt", r["prompt"]) for r in rows] + \
            [("answer", a) for a in answer_cases().values()]
    out = []
    for name, (fn, templated) in LLAMA_CPP_VOCAB.items():
        c = Counter(name)
        gguf = Path(vocab_dir) / fn
        ours = theirs = n_diff = 0
        for kind, t in texts:
            wrap = templated and kind == "prompt"
            a = c.prompt_tokens(t) if wrap else c.raw_tokens(t)
            b = llama_cpp_count(gguf, c.wrap(t) if wrap else t)
            ours, theirs, n_diff = ours + a, theirs + b, n_diff + (a != b)
        out.append({"tokenizer": name, "vocab": fn, "templated": templated,
                    "n_texts": len(texts), "tokens_hf": ours,
                    "tokens_llama_cpp": theirs, "texts_differing": n_diff,
                    "rel_diff": round(abs(ours - theirs) / theirs, 6)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--crosscheck", metavar="VOCAB_DIR",
                    help="with --prompts: recount a sample with llama-tokenize "
                         "on the vocab-only GGUFs in this directory")
    ap.add_argument("--every", type=int, default=30,
                    help="--crosscheck sample: every Nth prompt of the log")
    ap.add_argument("--prompts", help="a --record-prompts JSONL log")
    ap.add_argument("--answers", action="store_true",
                    help="token counts of the legal ID-only answers")
    ap.add_argument("--json", help="also write the result here")
    args = ap.parse_args()
    if args.fetch:
        fetch()
        return
    res = {}
    if args.crosscheck:
        res["crosscheck"] = crosscheck(args.prompts, args.crosscheck, args.every)
        for r in res["crosscheck"]:
            print(r)
        if args.json:
            Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
        return
    if args.prompts:
        res["prompts"] = count_prompts(args.prompts)
        print(f"prompt tokens, chat template applied  ({args.prompts})")
        print(f"{'tokenizer':13s} {'role':15s} {'n':>5s} {'mean':>7s} {'p95':>5s} "
              f"{'max':>5s} {'>=1280':>6s} {'cap':>4s} {'+cap>=1280':>10s} {'min ch/tok':>10s}")
        for name, roles in res["prompts"].items():
            for role, s in roles.items():
                print(f"{name:13s} {role:15s} {s['n']:5d} {s['mean']:7.1f} {s['p95']:5d} "
                      f"{s['max']:5d} {s['over_1280']:6d} {str(s['answer_cap']):>4s} "
                      f"{s['over_1280_with_cap']:10d} {s['min_chars_per_token']:10.3f}")
    if args.answers:
        res["answers"] = count_answers()
        names = list(MODELS)
        print("answer tokens (bare text, no template)")
        print(f"{'chars':>5s} " + " ".join(f"{n:>12s}" for n in names) + "  answer")
        for label, s in res["answers"].items():
            print(f"{s['chars']:5d} " + " ".join(f"{s[n]:12d}" for n in names) + f"  {label}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
    if not (args.prompts or args.answers):
        ap.error("give --fetch, --prompts LOG or --answers")


if __name__ == "__main__":
    sys.exit(main())
