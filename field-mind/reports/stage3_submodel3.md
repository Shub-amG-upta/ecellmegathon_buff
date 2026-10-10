# Stage 3 / sub-model 3 — dissolved-solids balance / boiler-water conductivity — report

## Status
DONE — `sim.py` integrates the dissolved-solids pool alongside the water and
energy balances:

```
M_bw          = _m_liq + CIRCUIT_WATER_MASS_KG          [kg]  (TRUE water, not the swelled level)
c_bw          = 1e6 * m_solids / M_bw                   [ppm]
dm_solids/dt  = c_fw * W_feed - c_bw * (W_blowdown + W_leak)   [kg/s]
kappa_bw      = c_bw * K_US_PER_PPM                     [uS/cm]
```

`m_solids` is initialised at the steady-state equilibrium
(`C_BW_NOMINAL_PPM · 1e-6 · M_bw(0)`), so a no-fault episode holds `kappa_bw`
flat with **no tuning** (DERIVATIONS §5.4). `c_bw_ppm` / `kappa_bw_uS` are
exposed per step in `self.diag` (not one of the six frozen emitted tags).
`BoilerSim.water_chemistry_log()` samples the §8 schema and
`notes_gen.build_records` / `episode_build` now carry it into `records.json`
(L3 still does not read it — known bug 3; format frozen, wiring is later).
Episodes NOT regenerated. `configs/base.yaml` NOT touched.

## Verification
| step | result |
|---|---|
| ran the module | `python3 -m data.generator.sim` — **exit 0**. Two expected `RuntimeWarning`s (family-A feed-shortage inventory-clamp guard, as in sub-models 1–2). |
| self-tests | `_self_test()` — **10 groups pass**. New sub-model-3 groups: **(8)** no-fault `kappa_bw` starts at 816 µS/cm and drifts **+0.3 %** over 180 min (< 3 %); **(9)** family-B leak 3.5 t/h → `kappa_bw` monotone-falling, **−16.7 %** by ~50 min, −30 % by 90 min; **(10)** family-A feed-short (eff 0.77) → `kappa_bw` **rises +14 %**, does not fall (would false-positive as family B). |
| direction checks | 8 / 8 OK (unchanged from sub-model 2 — the solids balance adds no new tag sign, its two directions are self-test 9/10). |
| independent re-derivation | **conductivity trajectory** by a route the ODE does not use — the closed-form first-order approach to the cycles-of-concentration steady state (`c_bw_ss = c_fw·W_feed/(W_bd+W_leak) = 106 ppm`, `τ = M_bw/(W_bd+W_leak) = 136 min`, `c_bw(t) = c_ss + (c_0−c_ss)·e^(−t/τ)`). Closed-form vs Euler ODE for a 3.5 t/h leak: **+30 min 672 vs 672, +60 min 577 vs 578, +90 min 500 vs 501 µS/cm — ≤ 0.2 %.** Separately, the sim reproduces the DERIVATIONS §5.3 table (produced by the standalone `scratchpad/ds_preview.py`, a third code path with its own water balance) to ~1 µS/cm — see table below. |
| mutation check | 8 mutations, all caught. New: **M7** flip the leak/blowdown solids-removal sign (`w_bd + w_leak` → `w_bd − w_leak`) → family-B `kappa_bw` *rises* to 1195 µS/cm instead of falling — caught by self-test 9. **M8** start `m_solids` off equilibrium (×0.7) → no-fault `kappa_bw` drifts **+10.9 %** — caught by self-test 8. |

### DERIVATIONS §5.3 reproduction (sim.py vs the standalone `ds_preview.py`)
| scenario | window | sim.py `kappa_bw` | `ds_preview.py` / §5.3 |
|---|---|---|---|
| no fault | 90 / 240 min | 816 → 818 (**+0.3 %** / +0.1 %) | 816 → 816 (0.0 %) |
| leak 3.5 t/h (Case 11) | 90 min | 816 → 570 (**−30.2 %**); +30 m 777, +60 m 662 | 816 → 568 (−30 %); +30 m 776, +60 m 661 |
| leak 3.5 t/h | 150 min | 816 → 433 (−46.9 %); +90 m 569, +120 m 494 | 816 → 434 (−47 %); +90 m 568, +120 m 493 |
| CBD stuck open (family B) | 90 min | 816 → 627 (−23.2 %); +30 m 782, +60 m 698 | 816 → 625 (−23 %); +30 m 781, +60 m 696 |
| feed-short eff 0.77 (Case 1) | 55 min | 816 → 933 (**+14.3 %**) | 816 → 933 (+14 %) |
| feed-short eff 0.85 (mild) | 55 min | 816 → 838 (+2.6 %) | — |

## Direction checks
| claim (CLAUDE.md sign discipline) | expected | measured | verdict |
|---|---|---|---|
| tube leak / stuck-open blowdown → boiler-water conductivity **falls** | `kappa_bw` monotone ↓, ≤ −10 % by ~50 min | leak 3.5: −16.7 % @ 50 min, −30 % @ 90 min, monotone | **OK** (self-test 9) |
| feed shortage at normal blowdown → conductivity **rises** | `kappa_bw` flat-to-rising, never a hard fall | eff 0.77: +14 %; eff 0.85: +2.6 % | **OK** (self-test 10) |
| no fault → conductivity **flat** (equilibrium init) | ‖drift‖ < 3 % over 180 min | +0.3 % | **OK** (self-test 8) |
| family A vs family B discriminable on conductivity direction | A rises / flat, B falls hard | A +14 %, B −30 % | **OK** — asymmetric leak-exclusion signal (DERIVATIONS §5.3) |

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `K_US_PER_PPM` | 1.8 µS/cm per ppm | **ASSUMED** — NaCl-equivalent `EC ≈ 1.8·TDS[ppm]` | 1.4 – 2.0 | absolute `kappa_bw` scale; **not** the trend direction |
| `KAPPA_FW_US` | 12 µS/cm | **ASSUMED** — small captive plant, imperfect polishing | 6 – 18 | absolute `kappa_bw` level; not direction |
| `C_FW_PPM` | 6.667 ppm | **DERIVED** — `KAPPA_FW_US / K_US_PER_PPM` | — | feedwater solids input to the balance |
| `_NOMINAL_CYCLES` | 68 | **DERIVED** — `(steam + blowdown)/blowdown = (67+1)/1` | — | equilibrium `c_bw` |
| `C_BW_NOMINAL_PPM` | 453.3 ppm | **DERIVED** — `C_FW_PPM · cycles` | — | `m_solids` initial value (equilibrium) |
| `KAPPA_BW_NOMINAL` | 816 µS/cm | **DERIVED** — `KAPPA_FW_US · cycles` = `C_BW_NOMINAL_PPM · K_US_PER_PPM` | — | self-test 8 anchor; `water_chemistry_log` baseline |

`M_bw` reuses `CIRCUIT_WATER_MASS_KG = 7000 kg` (geometry, **ASSUMED** 5000 –
11000, DERIVATIONS §5.1) + the sub-model-1 `_m_liq`. No new geometry constant.

## Numbers that changed
| quantity | old | new | ratio | downstream |
|---|---|---|---|---|
| dissolved-solids pool | (not modelled — no conductivity anywhere in `sim.py`) | `_m_solids` integrated per step; `c_bw_ppm` / `kappa_bw_uS` in `self.diag` | new | `records.json water_chemistry_log`; the family-A/B conductivity discriminator (DERIVATIONS §5.3) |
| `records.json` | `build_records(spec, rng)` | `build_records(spec, rng, water_chemistry_log=None)`; `episode_build` passes `sim.water_chemistry_log(rng)` | — | adds `water_chemistry_log` key (nothing reads it yet — known bug 3). On-disk episodes unchanged (not regenerated). |
| `episode_build.build_episode` | `rows = BoilerSim(spec).run()` | `sim = BoilerSim(spec); rows = sim.run()` (keeps the sim for the chem log) | — | none — same `rows`, same CSV |

Nominal `kappa_bw = 816 µS/cm` and the §5.3 trajectory magnitudes are the same
numbers `scratchpad/ds_preview.py` and DERIVATIONS §5.3 already recorded — this
stage puts them in the simulator, it does not change them.

## Disagreements recorded, not resolved
1. **Feed-short concentration is under-stated once the inventory clamp
   engages.** For eff ≤ ~0.80, family A drives the collapsed liquid to the
   `M_LIQ_FLOOR_KG = 1832 kg` floor (sub-model 1); `M_bw` then pins at ~8832 kg
   and a near-fixed `m_solids` stops concentrating further. So the modelled
   `kappa_bw` rise (+14 % at eff 0.77 / 55 min) is a floor on the real
   excursion, not the full picture. This was flagged in the sub-model-1 report
   (disagreement 3) and DERIVATIONS §5.3 already rates family-A conductivity as
   a **weak** signal ("not claimed as a positive Case 1 signature"), so this
   makes an already-weak signal weaker — acceptable, recorded.
2. **`K_US_PER_PPM` and `KAPPA_FW_US` are not independently identifiable from
   the nominal.** Both scale out of `KAPPA_BW_NOMINAL` (816 = 12 × 68 =
   453.3 ppm × 1.8) and out of the equilibrium init, so a wrong value of either
   leaves the nominal and the *relative* trajectory unchanged — only the
   absolute ppm figure moves. This is why mutation M8 corrupts the *init
   equilibrium* (a real, detectable error) rather than `K_US_PER_PPM`. The two
   constants stay ASSUMED with ranges; only their product with the true
   feedwater TDS is observable here.
3. **CBD-stuck-open vs tube-leak are indistinguishable on conductivity.** Both
   remove boiler water at boiler-water concentration and dilute in the same
   direction (−23 % vs −30 % at 90 min). DERIVATIONS §5.3 already states this:
   the signal discriminates **family A from family B**, not leak from blowdown.
   Unchanged, restated so it is not over-claimed.
4. **Sample-cadence assumption (online CBD analyser).** `water_chemistry_log`
   logs every 30 min of episode time — this assumes the modelled plant has an
   **online conductivity analyser**, not manual shift sampling (DERIVATIONS
   §5.5). If it is manual-only, conductivity cannot be an in-episode
   discriminator and the tier-C modality claim for the affected episodes needs
   rethinking. Open advisor question, carried from sub-model 1.

## Blocked / needs a decision
No new stop-rule hits. Carried:
- **(a)** whether the modelled plant has an online CBD conductivity analyser
  (DERIVATIONS §5.5) — decides whether the `water_chemistry_log` cadence is
  realistic and whether conductivity is a valid in-episode discriminator.
- **(b)** L3 wiring of `records.json` into the agent (known bug 3) — a later
  stage. Format is frozen (§8); this stage produces it.
- `notes_gen.base_cv` 3400 → ~4040 (from sub-model 2) still deferred to the next
  episode regeneration.

## What I could not verify
- The **absolute** `kappa_bw` level (816 µS/cm) rests on `KAPPA_FW_US = 12`
  ASSUMED — no measured feedwater conductivity for this plant. The *trajectory
  shapes and relative magnitudes* (which is what the discriminator uses) are
  independent of it.
- `CIRCUIT_WATER_MASS_KG = 7000 kg` (ASSUMED, from geometry) sets `M_bw` and
  hence the dilution time constant `τ = 136 min`. A larger circuit inventory
  slows every conductivity trajectory proportionally. Would need a validated
  circulation-circuit water model or plant data.
- Whether real family-A episodes on this plant concentrate as weakly as the
  model says (disagreement 1) — needs a boiler-water conductivity trace from a
  documented feed-shortage event.
