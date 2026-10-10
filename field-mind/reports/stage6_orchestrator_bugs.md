# Stage 6 / orchestrator bugs 1 + 2 — report

## Status

DONE — the DEGRADED latch (known bug 1, both halves) and `_merge` discarding
the belief accumulator (known bug 2) are fixed in
`fieldmind/agent/orchestrator.py`. New self-test file `bench/test_orchestrator.py`
(17 checks) injects the conditions the mock backend cannot produce. Mock
aggregate metrics are unchanged (mock triggers neither bug); the fixes are
verified by the targeted tests + mutation checks.

## Bug 1 — the DEGRADED latch

`derive_state` returns `DEGRADED` when `wm.degraded_mode` is set **or**
`len(wm.trusted_tags) < len(TAGS)`. Two independent latches fed it:

1. **`wm.degraded_mode`** was set to `f"llm_{env.status}"` on any non-ok
   Diagnostician reply (timeout / invalid schema) and **nothing ever cleared
   it**. One failed call on tick N → every later tick returns `DEGRADED`.
   Ground truth never contains `DEGRADED`, so a single API hiccup turned
   `ep_N01_normal` into 56 false positives / 80 ticks.
2. **`wm.trusted_tags`** was only ever `.discard()`-ed on a VALIDITY fact and
   **never restored**. A one-tick stuck/spike reading removed a tag for the
   rest of the episode → `len(trusted) < 6` → `DEGRADED` latched the same way.

### Fix

- **Top of `tick()`**: recompute `wm.degraded_mode` from the degradation-ladder
  rung only — `None` at rung 0, else `f"rung{rung}:{LADDER[rung]}"`. A transient
  `llm_*` marker set later in the *same* tick still lands in that tick's
  `Assessment.degraded_mode` (honest telemetry) but is gone by the next tick.
  The ladder — `degrade()` / `recover()`, which own the persistent state — is
  untouched; `self.rung` is now the single source of truth for it.
- **After `checks.run()`**: `wm.trusted_tags = set(TAGS) - {tags with a VALIDITY
  fact this tick}`, i.e. recomputed every tick instead of monotonically
  shrinking. A genuinely frozen instrument re-emits its VALIDITY fact every
  tick (`_validity` runs over every tag regardless of the trusted set), so it
  stays untrusted; a transient fault that clears restores the tag next tick.
  L1 still reports any balance that needs an untrusted tag as `SUSPENDED`.

`derive_state` itself is unchanged — a set `degraded_mode` or a short
`trusted_tags` still means `DEGRADED`; the fix is that neither now *persists*
past the condition that caused it.

## Bug 2 — `_merge` discarded the belief accumulator

Old body: `out = dict(model)`; the deterministic hypothesis list was used only
`if not out.get("hypotheses")`. So whenever the LLM answered with any
hypotheses, the cross-tick log-odds confidence from `update_hypotheses`
(`STEP_SUPPORT` / `STEP_ABSENT` / `STEP_CONTRA` accumulation, `world_model.py`)
was thrown away and replaced by the model's per-tick confidence. Symptom:
~9.5 distinct actions per episode from a 16-action catalogue at
`max_actions: 3`, because action selection followed the model's tick-to-tick
churn instead of the smoothed belief.

### Fix

`_merge` now walks the **model's** hypotheses (model keeps ORDER, WORDING,
`discriminator`, and which of *this tick's* fact ids it cites — those are
tick-local and were already validated by `check_citations` upstream) and for
each one that matches a deterministic hypothesis (by `cause`, then `case_ref`)
takes the **deterministic accumulated `confidence`**. Then:

- a model-only hypothesis (no deterministic match) is kept but its confidence
  is capped at 0.5 — it may be raised, it has not accumulated belief;
- a deterministic hypothesis the model did **not** mention is **not dropped** —
  it is appended (marked `carried`, `supports: []` since its accumulated fact
  ids are stale tick-local ones), so a tick where the LLM forgot a hypothesis
  does not retire it;
- an empty model reply leaves the deterministic list fully in place.

`headline` = model's or deterministic's; `unexplained` = model's then
deterministic's; ranks renumbered 1..n.

## Verification

| step | result |
|---|---|
| new self-tests | `python bench/test_orchestrator.py` → **17 / 17**. Covers: stale `llm_timeout` cleared at tick top; `derive_state` still maps a set marker / short trusted set to DEGRADED (so the *reset* is the fix); VALIDITY fact drops a tag then it is re-trusted once the fact stops; `_merge` keeps deterministic confidence, keeps the model's current-tick citation, does not drop a model-omitted hypothesis, caps a model-only idea at 0.5, survives an empty model reply. |
| L1 self-tests | `python bench/test_checks.py` → **20 / 20** (unchanged). |
| full harness | `python run_demo.py --all --backend mock` → **30/30**. `Q1 0.743`, `Q2_top1 0.334`, `Q2_top3 0.505 → 0.507`, `Q3 1.0`, `Q4 0.38 / 0.926`, `Q5 0.91`, `Q6 31.5`, `S4 0.45`, `S7 0.0`. Mock triggers neither bug (it never returns a non-ok status and its hypotheses ARE the deterministic ranking), so the aggregate is expected to be flat; the +0.002 on Q2_top3 is carried hypotheses occasionally surfacing in the top 3. |
| text ablation | `run_demo.py --all --backend mock --ablate-text` → Q1 0.743, Q2_top1 0.334, Q3 1.0 (unchanged). |
| independent re-derivation | distinct actions / fault episode: **8.54 with the fix, 8.54 with `_merge` reverted** (git stash) — identical on mock, because mock's hypothesis list already equals the deterministic ranking so the accumulator swap changes confidence values but not top-2 `cause` / `case_ref`, and actions come from `case_ref`. The 9.5 → lower improvement is only observable on a backend whose hypothesis ordering actually churns (gemini / litert), which is not run here. |
| mutation check | 3 mutations, each caught by `test_orchestrator.py`: (1) delete the `wm.degraded_mode` reset → "a healthy tick does NOT report DEGRADED …" FAILS; (2) restore the `.discard()` latch → "once the VALIDITY fact stops firing the tag is trusted again" FAILS; (3) `_merge` takes `mh` confidence for a matched hypothesis → "deterministic confidence wins: B keeps 0.40" FAILS. All restored → 17/17. |

## Direction checks

n/a — no physical sign involved. The behavioural invariants asserted:

| invariant | expected | result |
|---|---|---|
| transient LLM failure on tick N | tick N+1 state reflects the plant, not the failure | OK — `test_orchestrator` 1a |
| instrument fault clears | tag re-enters `trusted_tags` next tick | OK — 1b |
| LLM answers | accumulated confidence survives the merge | OK — bug 2 |
| LLM omits a hypothesis it raised earlier | hypothesis is carried, not retired | OK — bug 2 |
| LLM returns nothing parseable | deterministic ranking is the output | OK — bug 2 |

## Constants introduced

| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| model-only hypothesis confidence cap (`_merge`) | 0.5 | ASSUMED — a model-raised cause with no accumulated log-odds and no retrieved case should not outrank an accumulated one; 0.5 = "plausible, unconfirmed" on the sigmoid | 0.4–0.6; below 0.4 a genuine new lead is buried, above 0.6 it can top an accumulated hypothesis on its first tick | ranking of model-introduced causes; `gate.approve` reads `hypotheses[:2]` |

## Numbers that changed

| quantity | before | after |
|---|---|---|
| `wm.degraded_mode` after a one-off LLM timeout | latched for the rest of the episode | cleared at the next tick top |
| `wm.trusted_tags` after a transient VALIDITY fact | tag gone for the rest of the episode | restored next tick |
| `_merge` on a non-empty model reply | model hypotheses + model confidence, deterministic list discarded | model order/wording/citation + **deterministic accumulated confidence**, model-omitted hypotheses carried |
| mock `Q2_top3` | 0.505 | 0.507 |
| every other mock Q/S metric | — | unchanged |

## Disagreements recorded, not resolved

1. **The two bugs' headline symptoms are not reproducible on the mock backend**,
   so the fixes' *benefit* (not just their correctness) is verified only by the
   injected-condition tests, not by a metric delta. A `gemini` run on the 30
   episodes would show the DEGRADED false-positive collapse and the
   distinct-actions drop directly; it is not run here (no key wired in this
   pass). Flagged for the device/cloud run.

## Blocked / needs a decision

None. Both fixes are self-contained and the ladder contract
(`degrade`/`recover`) is preserved for when a thermal/energy-pressure hook is
wired to call it.

## What I could not verify

- **The distinct-actions improvement magnitude.** CLAUDE.md cites 9.5/episode
  as the bug-2 symptom; on mock it is 8.54 before and after (mock's hypotheses
  already equal the deterministic ranking). The real number needs a reasoning
  backend.
- **Interaction with a live degradation ladder.** `degrade()` / `recover()` are
  never called yet. The tick-top `degraded_mode` recompute derives the rung
  string from `self.rung`, which those methods maintain, so it should be
  consistent — but that path has no test because nothing exercises the ladder.
