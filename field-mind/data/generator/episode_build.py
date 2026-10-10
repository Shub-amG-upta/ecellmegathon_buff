"""
=============================================================================
 EPISODE BUILDER  (Plan §3.3 catalogue, §3.4 tiers, §3.5 distractors)
=============================================================================

Builds all 30 episodes: 6 normal + 24 faults across five families.

    N  6  normal, load swings, soot-blow          tests false positives
    A  6  water side, feed path                   feed < steam, heat side untouched
    B  5  water side, leak                        feed > steam yet level only holds
    C  6  fuel / heat side                        pressure and bed fall together
    D  4  bed temperature                         bed climbs at steady load
    E  3  slow drift                              nothing crosses a limit

MODALITY-NECESSITY TIERS (§3.4) -- the most important dataset decision. If
every episode were solvable from the time series alone we would have built a
time-series benchmark with text stapled on, and the multimodal claim would be
empty. So:

    tier A  12  solvable from the numbers alone
    tier B  12  needs the numbers plus ONE text source
    tier C   6  needs two sources, or memory of a past episode

The evaluator checks tier compliance directly (T8): run with text removed and
confirm tier B/C accuracy collapses. If it does not, the episode is
mislabelled and gets rebuilt.

Run:  python -m data.generator.episode_build --out data/episodes
=============================================================================
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import yaml

from .sim import BoilerSim, EpisodeSpec, Schedule
from .notes_gen import build_notes, build_records, build_queries

# The bed-temperature DEVIATION criterion in `state_timeline` is load-normalised
# (stage 4): a bed running hotter because the plant is running harder is normal
# operation, and L1 already strips the same load component out before it flags a
# bed drift (`_load_normalised_bed_slope`). A raw |bed - 850| band would
# manufacture DEVIATION labels the detector is built never to raise. LOAD_COEF
# is read from the config AT GENERATION TIME and stamped into every
# ground_truth.json (`state_timeline_load_coef`) so a later re-fit of the
# coefficient cannot silently relabel the episodes.
_CFG = yaml.safe_load(
    (Path(__file__).parent.parent.parent / "configs/base.yaml").read_text())
LOAD_COEF = _CFG["checks"]["load_coef_degc_per_tph"]
_NOM_STEAM = 67.0


# =======================================================================
#  THE 30-EPISODE CATALOGUE
# =======================================================================

def catalogue() -> list[EpisodeSpec]:
    eps: list[EpisodeSpec] = []
    S = Schedule

    # ---- N: normal (6). Steady operation and load swings. Tests Q5. ----
    for i in range(6):
        eps.append(EpisodeSpec(
            episode_id=f"ep_N{i+1:02d}_normal", family="N", tier="A",
            duration_min=[45, 60, 60, 90, 45, 75][i],
            schedules=([S(1200, "load_demand", 60.0, 10.0)] if i in (2, 4) else []),
            root_cause_id="NONE", root_cause_text="No fault. Normal operation.",
            correct_action_ids=[], seed=100 + i))

    # ---- A: water side, feed path (6). Feed cannot deliver. ----
    # Severity re-anchored (stage 4) to RCA Case 1: level 50% -> 20% Lo alarm in
    # ~25 min, manual trip on low-low at ~35 min, "declined monotonically for
    # 30 minutes". With the geometry K (0.586) the RCA rate and its stated
    # 4-5 t/h deficit over-determine the fault (DERIVATIONS 3.7); we match the
    # RATE, so feed_valve_effectiveness ~ 0.78 (not 0.805 -- the closed-form in
    # 3.7 ignored the level controller clawing feed back as the error grows).
    # Each variant's effectiveness is solved against ITS OWN seed: near the feed
    # cap ~= steam demand the fault is a knife-edge (a 0.02 change in
    # effectiveness swings time-to-trip from ~15 min to never), and the sub-
    # model-4 slow load OU (sigma 2.1 t/h) sets a different baseline load per
    # seed. That load sensitivity is physical -- a marginal feed restriction is
    # more or less survivable depending on the load at the time -- so it is kept,
    # and severities are picked per scenario, not from one global number.
    a_specs = [
        ("fcv_seize", 0.81, 8.0, "RCA-01", "FCV actuator stem seizure",
         ["ACT-014", "ACT-002", "ACT-031"], "B", ["timeseries", "notes"]),
        ("fcv_seize_fast", 0.75, 3.0, "RCA-01", "FCV actuator stem seizure",
         ["ACT-014", "ACT-002", "ACT-031"], "A", ["timeseries"]),
        ("bfp_suction", 0.75, 4.0, "RCA-02", "BFP suction loss from low deaerator level",
         ["ACT-005", "ACT-002", "ACT-031"], "B", ["timeseries", "notes"]),
        ("strainer_choke", 0.83, 25.0, "RCA-03", "Feed strainer progressively choked",
         ["ACT-007", "ACT-014"], "C", ["timeseries", "notes", "records"]),
        ("fcv_caught", 0.82, 6.0, "RCA-01", "FCV actuator stem seizure, operator intervened",
         ["ACT-014", "ACT-031"], "A", ["timeseries"]),
        ("fcv_seize_repeat", 0.82, 8.0, "RCA-01", "FCV actuator stem seizure",
         ["ACT-014", "ACT-002", "ACT-031"], "C", ["timeseries", "notes"]),
    ]
    for i, (name, eff, ramp, rc, txt, acts, tier, mods) in enumerate(a_specs):
        onset = 600.0
        sch = [S(onset, "feed_valve_effectiveness", eff, ramp)]
        caught = "caught" in name
        if caught:
            # The operator opens the bypass: effectiveness partly recovers,
            # well before the level reaches the inventory clamp / trip band.
            sch.append(S(onset + 1100, "feed_valve_effectiveness", 0.95, 4.0))
        eps.append(EpisodeSpec(
            episode_id=f"ep_A{i+1:02d}_{name}", family="A", tier=tier,
            duration_min=[70, 45, 60, 120, 60, 65][i], schedules=sch,
            root_cause_id=rc, root_cause_text=txt, required_modalities=mods,
            correct_action_ids=acts, fault_onset_s=onset, caught_in_time=caught,
            contributory=(["CBD valve left ~40% open at handover"] if i == 0 else []),
            repeat_of=("ep_A01_fcv_seize" if name.endswith("repeat") else None),
            dropout_tag=("feed_water_flow" if i == 4 else None),
            injection_present=(i == 1), seed=200 + i))

    # ---- B: water side, leak (5). Feed exceeds steam yet level only holds. ----
    # Severity re-anchored (stage 4) to RCA Case 11: over ~8 h the feed-to-steam
    # gap reached only 2-3 t/h and boiler-water conductivity fell; the unit was
    # tripped in a controlled manner, it never reached a level trip. Episodes
    # here are the compressed benchmark form of that -- leak sized so the gap
    # lands at the RCA's 2-3 t/h, not the old 4-7.
    b_specs = [
        ("tube_leak", 2.2, 12.0, "C", ["timeseries", "notes", "records"]),
        ("tube_leak_fast", 3.5, 4.0, "A", ["timeseries"]),
        ("tube_leak_slow", 1.8, 30.0, "B", ["timeseries", "notes"]),
        ("cbd_left_open", 2.5, 2.0, "B", ["timeseries", "notes"]),
        ("tube_leak_repeat", 2.2, 12.0, "C", ["timeseries", "notes"]),
    ]
    for i, (name, leak, ramp, tier, mods) in enumerate(b_specs):
        cbd = "cbd" in name
        eps.append(EpisodeSpec(
            episode_id=f"ep_B{i+1:02d}_{name}", family="B", tier=tier,
            duration_min=[90, 45, 150, 60, 90][i],
            schedules=[S(900, "leak_tph", leak, ramp)],
            root_cause_id=("RCA-01b" if cbd else "RCA-04"),
            root_cause_text=("CBD valve left open at handover" if cbd
                             else "Water wall tube erosion and split"),
            required_modalities=mods,
            correct_action_ids=(["ACT-021", "ACT-002"] if cbd
                                else ["ACT-009", "ACT-031", "ACT-002"]),
            fault_onset_s=900.0,
            repeat_of=("ep_B01_tube_leak" if name.endswith("repeat") else None),
            dropout_tag=("drum_level" if i == 2 else None), seed=300 + i))

    # ---- C: fuel / heat side (6). Pressure and bed fall together. ----
    # Severity re-anchored (stage 4) to RCA Case 6: at constant load the outlet
    # pressure drifted 66 -> 63 kg/cm2 over ~35 min and 66 -> 58 over ~65 min
    # (with a compounding second feeder failure), burner support was taken near
    # 58 and there was NO hard trip. The pressure integrator's quasi-steady sag
    # is ~ 60 * fuel_availability, so 0.95 -> ~58 (wet-coal cases) and 0.89 ->
    # ~53, grazing the 55 alarm (the sharp feeder-trip cases). The old 0.62-0.88
    # drove pressure to 37-52 -- past every RCA figure.
    c_specs = [
        ("wet_coal", 0.955, 15.0, "RCA-05", "Wet coal bridging in the hopper",
         ["ACT-011", "ACT-012"], "C", ["timeseries", "notes", "records"]),
        ("feeder_trip", 0.89, 1.0, "RCA-06", "Coal feeder 2 tripped on overload",
         ["ACT-012", "ACT-002"], "A", ["timeseries"]),
        ("wet_coal_mild", 0.96, 25.0, "RCA-05", "Wet coal bridging in the hopper",
         ["ACT-011", "ACT-012"], "B", ["timeseries", "notes"]),
        ("feeder_trip_caught", 0.89, 1.0, "RCA-06", "Coal feeder 2 tripped, restarted",
         ["ACT-012", "ACT-002"], "A", ["timeseries"]),
        ("low_cv_coal", 0.95, 20.0, "RCA-05", "Low calorific value coal consignment",
         ["ACT-011", "ACT-018"], "C", ["timeseries", "records"]),
        ("wet_coal_repeat", 0.96, 15.0, "RCA-05", "Wet coal bridging in the hopper",
         ["ACT-011", "ACT-012"], "B", ["timeseries", "notes"]),
    ]
    for i, (name, hi, ramp, rc, txt, acts, tier, mods) in enumerate(c_specs):
        sch = [Schedule(900, "fuel_availability", hi, ramp)]
        caught = "caught" in name
        if caught:
            sch.append(Schedule(900 + 1200, "fuel_availability", 1.15, 3.0))
        eps.append(EpisodeSpec(
            episode_id=f"ep_C{i+1:02d}_{name}", family="C", tier=tier,
            duration_min=[90, 55, 100, 60, 110, 90][i], schedules=sch,
            root_cause_id=rc, root_cause_text=txt, required_modalities=mods,
            correct_action_ids=acts, fault_onset_s=900.0, caught_in_time=caught,
            repeat_of=("ep_C01_wet_coal" if name.endswith("repeat") else None),
            dropout_tag=("drum_pressure" if i == 4 else None),
            injection_present=(i == 2), seed=400 + i))

    # ---- D: bed temperature (4). Bed climbs toward the 940 degC trip. ----
    # Severity re-anchored (stage 4) to RCA Case 7: after a high-CV coal change
    # the bed rose SLOWLY -- "rising slowly across all compartments" at T+40 min,
    # 880 degC alarm at ~90 min, 940 degC DCF trip at ~125 min. The corrected
    # bed lag (tau 139 s) reaches its target in minutes, so the RCA's slow
    # approach is carried by a LONG driver ramp (the fuel changeover / ash-
    # recirculation depletion the case describes), not by a fast step. Episode
    # durations extended so the RCA trajectory fits the window.
    d_specs = [
        ("high_cv_coal", "coal_cv_factor", 1.14, 120.0, "RCA-07",
         "High-CV coal fired without feeder recalibration",
         ["ACT-018", "ACT-019", "ACT-002"], "C", ["timeseries", "records"]),
        ("low_primary_air", "primary_air", 0.86, 70.0, "RCA-08",
         "Primary air damper left low after maintenance",
         ["ACT-019", "ACT-018"], "B", ["timeseries", "notes"]),
        ("high_cv_severe", "coal_cv_factor", 1.16, 55.0, "RCA-07",
         "High-CV coal fired without feeder recalibration",
         ["ACT-018", "ACT-019", "ACT-002"], "A", ["timeseries"]),
        ("low_pa_caught", "primary_air", 0.86, 40.0, "RCA-08",
         "Primary air damper left low, corrected by operator",
         ["ACT-019"], "A", ["timeseries"]),
    ]
    for i, (name, drv, tgt, ramp, rc, txt, acts, tier, mods) in enumerate(d_specs):
        sch = [Schedule(600, drv, tgt, ramp)]
        caught = "caught" in name
        if caught:
            sch.append(Schedule(600 + 1500, drv, 1.0, 5.0))
        eps.append(EpisodeSpec(
            episode_id=f"ep_D{i+1:02d}_{name}", family="D", tier=tier,
            duration_min=[160, 130, 80, 85][i], schedules=sch,
            root_cause_id=rc, root_cause_text=txt, required_modalities=mods,
            correct_action_ids=acts, fault_onset_s=600.0, caught_in_time=caught,
            seed=500 + i))

    # ---- E: slow drift (3). THE FAMILY THAT MATTERS MOST. ----
    # Nothing ever crosses a threshold. The only signal is a slow change in the
    # RELATIONSHIP between tags at constant load. This is also the case that
    # breaks naive self-learning, because an adaptive baseline will happily
    # learn the drift as the new normal (guarded by baseline freeze, §8.2).
    # Absorption drops only a FEW PERCENT. It has to be gentle enough that
    # nothing crosses a limit -- that is the definition of family E, and an
    # episode here that trips is a mislabelled episode, not a severe one.
    # No RCA case carries a fouling-drift timeline, so these severities are a
    # BENCHMARK DESIGN CHOICE (recorded in the stage-4 report): the constraint
    # is only "crosses nothing" (checked by bench/validate_data.py C6), with a
    # margin held against the ~+/-3 degC process-disturbance swing (stage 4).
    # seeds chosen for a low no-fault load-OU baseline so absorption can be
    # pushed to the strong end of "a few percent" (0.984-0.986) while bed_max
    # stays a clear margin under the 880 alarm (checked by validate_data C6).
    for i, (absorb, ramp, dur, seed) in enumerate([(0.984, 110.0, 200, 600),
                                                   (0.986, 130.0, 180, 603),
                                                   (0.984, 100.0, 160, 604)]):
        eps.append(EpisodeSpec(
            episode_id=f"ep_E{i+1:02d}_fouling_drift", family="E", tier="B",
            duration_min=dur,
            schedules=[Schedule(300, "heat_absorption", absorb, ramp)],
            root_cause_id="RCA-09",
            root_cause_text="Progressive heat transfer surface fouling",
            required_modalities=["timeseries", "records"],
            correct_action_ids=["ACT-024", "ACT-025"],
            fault_onset_s=300.0, seed=seed))

    return _apply_case_map(eps)


# =======================================================================
#  DEV SET (Session 1b). Decides keep/revert; the 30 reporting episodes above
#  only report, once per phase.
# =======================================================================
# Same scenario specs as catalogue(), new seeds. Fault seeds are the reporting
# seed + 1000. The extra no-fault episodes use 1100-1111. None of these overlap
# the seeds already in use: 0-3 (sim self-tests, validate_data suite), 100-604
# (reporting), 700-711 / 900-911 / 950-969 (threshold calibration).
DEV_SEED_OFFSET = 1000
DEV_NOFAULT_SEEDS = list(range(1100, 1112))      # 12 episodes, two per N spec
# A spec the gate rejects is retried with seed + 10000, then + 20000. Severities
# are never touched. Every rejection is printed and goes in the report.
DEV_RETRY_OFFSETS = (0, 10000, 20000)


def dev_catalogue() -> list[EpisodeSpec]:
    import copy
    base = catalogue()                       # case map already applied
    faults = [s for s in base if s.family != "N"]
    normals = [s for s in base if s.family == "N"]
    out: list[EpisodeSpec] = []

    def dev_id(eid: str) -> str:
        return "dev_" + eid[len("ep_"):]

    for s in faults:
        d = copy.deepcopy(s)
        d.episode_id = dev_id(s.episode_id)
        d.seed = s.seed + DEV_SEED_OFFSET
        d.repeat_of = dev_id(s.repeat_of) if s.repeat_of else None
        out.append(d)
    for i, seed in enumerate(DEV_NOFAULT_SEEDS):
        d = copy.deepcopy(normals[i % len(normals)])
        d.episode_id = f"dev_N{i+1:02d}_normal"
        d.seed = seed
        out.append(d)
    return out


# =======================================================================
#  Ground truth, derived from the simulation rather than asserted
# =======================================================================

# A raw per-sample state must persist this long before it becomes a committed
# segment boundary (stage 4). Without it the emitted trace flickers across a
# threshold every few samples -- especially the new pressure < 63 and gap > 2.5
# DEVIATION terms, and the noisy load-normalised bed reference -- and the
# timeline shatters into hundreds of one-second segments. 60 s of on-delay is
# also how a real annunciator with a sustain timer behaves.
_STATE_DEBOUNCE_S = 60.0


def _raw_state(r: dict) -> str:
    lvl, bed = r["drum_level"], r["bed_temp_avg"]
    gap = abs(r["feed_water_flow"] - r["steam_flow"])
    # load-normalised bed reference: the bed a healthy plant runs at this steam
    # flow. LOAD_COEF is stamped into ground_truth.json.
    bed_ref = 850.0 + LOAD_COEF * (r["steam_flow"] - _NOM_STEAM)
    if lvl < 10 or bed > 940:
        return "TRIP_IMMINENT"
    if lvl < 20 or bed > 880 or r["drum_pressure"] < 55:
        return "ALARM"
    if (abs(lvl - 50) > 6 or abs(bed - bed_ref) > 25
            or r["drum_pressure"] < 63.0 or gap > 2.5):
        # pressure < 63: the RCA Case 6 combustion-deficit deviation point
        #   (66 -> 63 at T+35 min) -- there was no pressure term before, so an
        #   RCA-faithful controlled sag registered as NORMAL (stage 4).
        # gap > 2.5: family B (RCA Case 11) is "feed 2-3 t/h over steam yet
        #   level holds" -- the old > 4 band never saw it once leaks were sized
        #   to the RCA.
        return "DEVIATION"
    return "NORMAL"


# Stage 5: the invented case ids the specs above were written against
# (RCA-01b, RCA-02..RCA-09) are replaced by the real RCA library. The
# episode -> real-case mapping is data, kept in one place so the advisor can
# review it without diffing 24 directories. `null` means the episode's
# documented mechanism has no standalone case in docs/Boiler_Failure_Case_
# Studies_RCA.pdf -> T2/T6 are NOT-APPLICABLE for it (bench/evaluator.py),
# not scored zero. See reports/stage5_case_library.md.
_CASE_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "kb" / "episode_case_map.json"


def _apply_case_map(eps: list[EpisodeSpec]) -> list[EpisodeSpec]:
    if not _CASE_MAP_PATH.exists():
        return eps
    m = {e["episode"]: e for e in json.loads(_CASE_MAP_PATH.read_text())["episodes"]}
    for spec in eps:
        e = m.get(spec.episode_id)
        if e is None:
            continue
        spec.root_cause_id = e["new"]              # may be None -> JSON null
        # correct_action_ids also come from the mapped case (same fixed
        # catalogue); [] where the mechanism has no real case -> T4 returns
        # applicable:false (bench/evaluator.py), same fix as T2.
        spec.correct_action_ids = list(e.get("actions", []))
    return eps


def state_timeline(spec: EpisodeSpec, rows: list[dict]) -> list:
    """Label each time range by the state the PHYSICS is actually in.

    Derived from the simulated tag values, not from the fault schedule alone: a
    fault that was injected but has not yet moved anything measurable is
    genuinely still NORMAL, and labelling it otherwise would make detection
    lead time (T6) meaningless. A candidate new state must hold for
    `_STATE_DEBOUNCE_S` before it commits, so noise flicker across a threshold
    does not shatter the timeline.
    """
    dt = rows[1]["t"] - rows[0]["t"] if len(rows) > 1 else 5.0
    hold = max(1, int(round(_STATE_DEBOUNCE_S / dt)))
    tl, cur, start = [], "NORMAL", 0.0
    cand, cand_n = "NORMAL", 0
    for r in rows:
        s = _raw_state(r)
        if s == cur:
            cand, cand_n = cur, 0
            continue
        if s == cand:
            cand_n += 1
        else:
            cand, cand_n = s, 1
        if cand_n >= hold:
            # commit the transition at the time the candidate first appeared
            tl.append([start, r["t"] - (cand_n - 1) * dt, cur])
            cur, start = cand, r["t"] - (cand_n - 1) * dt
            cand_n = 0
    tl.append([start, rows[-1]["t"], cur])
    return tl


def build_gt_and_timeline(spec: EpisodeSpec, rows: list[dict],
                          notes: list[dict] | None = None) -> dict:
    """The ground truth, derived from the simulated rows. Split out so the
    validation gate (bench/validate_data.py) can build the same structure for
    its self-tests without touching disk."""
    tl = state_timeline(spec, rows)
    trip = next((s for s, _, st in tl if st == "TRIP_IMMINENT"), None)
    return {
        "episode_id": spec.episode_id, "family": spec.family, "tier": spec.tier,
        "duration_min": spec.duration_min, "tick_period_s": 30,
        "required_modalities": spec.required_modalities,
        "fault_onset_t": spec.fault_onset_s, "trip_t": trip,
        "root_cause_id": spec.root_cause_id,
        "root_cause_text": spec.root_cause_text,
        "contributory": spec.contributory,
        "state_timeline": tl,
        # stage 4: the bed DEVIATION criterion in `state_timeline` is normalised
        # by this coefficient. Recorded so a later re-fit is a visible change.
        "state_timeline_load_coef": LOAD_COEF,
        "correct_action_ids": spec.correct_action_ids,
        "distractor_note_ids": [n["id"] for n in (notes or []) if n.get("distractor")],
        "repeat_of_episode_id": spec.repeat_of,
        "injection_present": spec.injection_present,
        "dropout_tag": spec.dropout_tag,
        "caught_in_time": spec.caught_in_time,
        "_provenance": "SYNTHETIC. Generated by data/generator. Not measured plant data.",
    }


def build_episode(spec: EpisodeSpec, out_dir: Path,
                  validate: bool = True) -> dict | None:
    """Build one episode in memory, run it through the validation gate, and
    write the files ONLY if it passes. Returns the ground-truth dict, or None
    if the gate failed (nothing is written)."""
    rng = random.Random(spec.seed)

    sim = BoilerSim(spec)
    rows = sim.run()
    notes = build_notes(spec, rows, rng)
    wchem = sim.water_chemistry_log(rng)
    records = build_records(spec, rng, wchem)
    gt = build_gt_and_timeline(spec, rows, notes)

    # ---- THE VALIDATION GATE. A failing episode is never written. ----
    if validate:
        from bench.validate_data import validate_episode
        rep = validate_episode(spec, rows, gt, diag=sim.diag,
                               check_determinism=True)
        if rep.n_warn:
            print(rep.text())
        if rep.failed:
            print(f"  !! {spec.episode_id}: REJECTED -- not written")
            for f in rep.findings:
                if f.level == "FAIL":
                    print(f"       {f}")
            return None

    d = out_dir / spec.episode_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "timeseries.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(d / "notes.jsonl", "w") as fh:
        for n in notes:
            fh.write(json.dumps(n) + "\n")
    (d / "records.json").write_text(json.dumps(records, indent=2))
    (d / "query.txt").write_text("\n".join(build_queries(spec)))
    (d / "ground_truth.json").write_text(json.dumps(gt, indent=2))
    return gt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="which", choices=["report", "dev"],
                    default="report", help="report: the 30 reporting episodes; "
                    "dev: the disjoint development set (default out data/episodes_dev)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default="", help="substring filter on episode id")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the validation gate (debugging only)")
    args = ap.parse_args()

    out = Path(args.out or ("data/episodes_dev" if args.which == "dev"
                            else "data/episodes"))
    specs = [s for s in (dev_catalogue() if args.which == "dev" else catalogue())
             if args.only in s.episode_id]
    fams: dict[str, int] = {}
    n_ok = n_fail = 0
    for spec in specs:
        gt = build_episode(spec, out, validate=not args.no_validate)
        if gt is None and args.which == "dev":
            # Dev only: retry with a fresh seed, never a changed severity.
            base_seed = spec.seed
            for off in DEV_RETRY_OFFSETS[1:]:
                spec.seed = base_seed + off
                print(f"  RETRY {spec.episode_id}: seed {base_seed} -> {spec.seed}")
                gt = build_episode(spec, out, validate=not args.no_validate)
                if gt is not None:
                    break
        if gt is None:
            n_fail += 1
            continue
        n_ok += 1
        fams[spec.family] = fams.get(spec.family, 0) + 1
        onset = spec.fault_onset_s or 0.0
        ttt = None if gt["trip_t"] is None else round((gt["trip_t"] - onset) / 60, 1)
        print(f"  {spec.episode_id:30s} tier {spec.tier}  "
              f"{spec.duration_min:5.0f} min  trip@{ttt} min-from-onset")
    print(f"\n{n_ok}/{len(specs)} episodes written to {out}"
          + (f"  ({n_fail} REJECTED by the gate)" if n_fail else ""))
    print("by family:", dict(sorted(fams.items())))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
