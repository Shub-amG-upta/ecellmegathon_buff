"""bench/token_count.py: real tokenizer counts and the G3 budget predicate.

The pinned counts below were produced by the Hugging Face tokenizers and
agree exactly with llama.cpp's own tokenizer (llama-tokenize on the vocab-only
GGUFs) for Llama 3 and Qwen2 -- reports/multi_phase2_prompt_shrink.md. Skipped
when the tokenizer files have not been fetched (bench/token_count.py --fetch).
"""

import json

import pytest

pytest.importorskip("tokenizers")
pytest.importorskip("jinja2")

from bench import token_count as tc

if not tc.MANIFEST.exists() or not all((tc.DIR / n / "tokenizer.json").exists()
                                       for n in tc.MODELS):
    pytest.skip("tokenizer files not fetched", allow_module_level=True)

WORST = ('{"g":"A","r":[["RCA-11",0.6,["t143.F1","t143.F2"]],'
         '["RCA-07",0.3,["t143.F1","t143.F2"]],["RCA-09",0.2,["t143.F1","t143.F2"]]],'
         '"sep":"RCA-11","n":["N3"],"x":[]}')
WORST_TOKENS = {"llama3.2-3b": 89, "qwen3-1.7b": 105, "gemma3-1b": 111,
                "qwen2.5-0.5b": 105}
# tokens the chat template adds around a one-user-message prompt
OVERHEAD = {"llama3.2-3b": 35, "qwen3-1.7b": 12, "gemma3-1b": 9, "qwen2.5-0.5b": 29}


@pytest.fixture(scope="module")
def counters():
    return {n: tc.Counter(n) for n in tc.MODELS}


def test_worst_case_answer_is_the_first_answer_case():
    assert next(iter(tc.answer_cases().values())) == WORST


@pytest.mark.parametrize("name", sorted(WORST_TOKENS))
def test_worst_case_answer_token_count(counters, name):
    assert counters[name].raw_tokens(WORST) == WORST_TOKENS[name]


@pytest.mark.parametrize("name", sorted(OVERHEAD))
def test_prompt_is_counted_inside_the_chat_template(counters, name):
    c = counters[name]
    body = "FACTS:\nt84.F1 [BALANCE/ALARM] water balance residual +1.8 TPH"
    assert c.prompt_tokens(body) - c.raw_tokens(body) == OVERHEAD[name]
    wrapped = c.wrap(body)
    assert wrapped.count(body) == 1 and not wrapped.endswith(body)


def test_generation_prompt_is_part_of_the_count(counters):
    c = counters["gemma3-1b"]
    assert c.wrap("x").endswith("<start_of_turn>model\n")
    assert counters["llama3.2-3b"].wrap("x").endswith("assistant<|end_header_id|>\n\n")


def test_bos_is_counted_once(counters):
    ids = counters["llama3.2-3b"].tok.encode(
        counters["llama3.2-3b"].wrap("x"), add_special_tokens=False).ids
    bos = counters["llama3.2-3b"].tok.token_to_id("<|begin_of_text|>")
    assert ids.count(bos) == 1
    assert counters["llama3.2-3b"].prompt_tokens("x") == len(ids)


def test_qwen3_is_counted_with_thinking_off(counters):
    c = counters["qwen3-1.7b"]
    assert c.wrap("x").endswith("<think>\n\n</think>\n\n")
    assert c.prompt_tokens("x") == c.prompt_tokens("x", enable_thinking=True) + 4


@pytest.mark.parametrize("role,cap", [("diagnostician", 60), ("diagnostician_ids", 60),
                                      ("verifier", 30), ("text_reader", 50),
                                      ("query", 120), ("generic", None)])
def test_answer_caps_per_agent(role, cap):
    assert tc.cap_for(role) == cap


@pytest.mark.parametrize("tokens,role,over", [
    (1219, "diagnostician", False), (1220, "diagnostician", True),   # + 60
    (1249, "verifier", False), (1250, "verifier", True),             # + 30
    (1229, "text_reader", False), (1230, "text_reader", True),       # + 50
    (1279, "generic", False), (1280, "generic", True),               # no cap
])
def test_budget_is_prompt_plus_answer_cap_under_1280(tokens, role, over):
    assert tc.over_budget(tokens, role) is over


def test_count_prompts_flags_only_the_prompt_over_budget(tmp_path, counters):
    c = counters["gemma3-1b"]
    word = "level "
    n = 1
    while c.prompt_tokens(word * (n + 1)) + 60 < 1280:   # largest that fits
        n += 1
    fits, over = word * n, word * (n + 1)
    log = tmp_path / "p.jsonl"
    log.write_text("".join(json.dumps({"role": "diagnostician", "prompt": p}) + "\n"
                           for p in (fits, over, "short")))
    s = tc.count_prompts(str(log))["gemma3-1b"]["diagnostician"]
    assert (s["n"], s["over_1280_with_cap"], s["over_1280"]) == (3, 1, 0)
    assert s["max"] == c.prompt_tokens(over)


def test_file_that_does_not_match_the_manifest_is_refused(tmp_path, monkeypatch):
    man = json.loads(tc.MANIFEST.read_text())
    man["qwen2.5-0.5b"]["files"]["tokenizer.json"] = "0" * 64
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(man))
    monkeypatch.setattr(tc, "MANIFEST", bad)
    with pytest.raises(SystemExit):
        tc.Counter("qwen2.5-0.5b")
