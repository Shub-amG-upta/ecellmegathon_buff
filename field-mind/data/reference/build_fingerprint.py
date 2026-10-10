"""
Build a statistical fingerprint of the real AFBC boiler reference data.
Reads data/reference/xinan_completed_data.csv (86,400 rows, 5 s sampling, 120 h).
Writes data/reference/fingerprint.json.

Does NOT touch the simulator. Every number here is a MEASURED property of the
real trace; the accompanying FINGERPRINT.md says which simulator parameter each
one constrains.
"""
import json
import numpy as np
import pandas as pd

CSV = "data/reference/xinan_completed_data.csv"
OUT = "data/reference/fingerprint.json"

DT_S = 5.0                       # sample period, seconds (verified below)
MPA_TO_KGCM2 = 10.197162129779  # 1 MPa = 10.19716 kg/cm2  (gauge -> gauge)

# our tag  <-  their column   (CLAUDE.md verified mapping)
COLMAP = {
    "steam_flow":     "ZZQBCHLL.AV_0#",   # main steam flow, t/h
    "bed_temp_avg":   "TE_8313B.AV_0#",    # bed temperature, degC
    "ms_temperature": "TE_8332A.AV_0#",    # main steam temperature, degC
    "drum_pressure":  "PTCA_8322A.AV_0#",  # MPa  -> converted to kg/cm2(g)
}
LOAD = "steam_flow"   # load proxy for load-following regressions

# lags of interest, in samples (5 s each)
LAGS = {
    "5s": 1, "10s": 2, "30s": 6, "1min": 12, "5min": 60, "10min": 120,
    "30min": 360, "1h": 720, "6h": 4320, "12h": 8640, "24h": 17280,
}


def acf_at(x, lag):
    """Sample autocorrelation of x at a single lag (mean-removed, biased-N)."""
    x = np.asarray(x, float)
    x = x - x.mean()
    n = len(x)
    denom = np.dot(x, x)
    if denom == 0:
        return float("nan")
    return float(np.dot(x[:n - lag], x[lag:]) / denom)


def ols(y, X):
    """y ~ a + b*X  (single regressor). Returns slope, intercept, R^2."""
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    A = np.column_stack([np.ones_like(X), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    intercept, slope = coef
    resid = y - A @ coef
    ss_res = float(np.dot(resid, resid))
    ss_tot = float(np.dot(y - y.mean(), y - y.mean()))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(slope), float(intercept), float(r2)


def resolution(x):
    """Smallest nonzero gap between adjacent DISTINCT values = quantiser step."""
    u = np.unique(np.round(np.asarray(x, float), 6))
    if len(u) < 2:
        return float("nan")
    d = np.diff(u)
    d = d[d > 1e-9]
    return float(d.min()) if len(d) else float("nan")


def diff_lag(x, lag):
    return np.asarray(x, float)[lag:] - np.asarray(x, float)[:-lag]


def main():
    raw = pd.read_csv(CSV)
    ts = pd.to_datetime(raw["date"], format="%Y/%m/%d %H:%M:%S")

    # --- sampling regularity -------------------------------------------------
    dt = ts.diff().dropna().dt.total_seconds().to_numpy()
    sampling = {
        "n_rows": int(len(raw)),
        "start": str(ts.iloc[0]),
        "end": str(ts.iloc[-1]),
        "span_hours": float((ts.iloc[-1] - ts.iloc[0]).total_seconds() / 3600.0),
        "dt_seconds_median": float(np.median(dt)),
        "dt_seconds_min": float(dt.min()),
        "dt_seconds_max": float(dt.max()),
        "n_gaps_gt_1p5x": int((dt > 1.5 * DT_S).sum()),
        "n_duplicate_timestamps": int((dt == 0).sum()),
    }

    # --- assemble our-tag frame -------------------------------------------------
    df = pd.DataFrame({tag: raw[col].astype(float) for tag, col in COLMAP.items()})
    df["drum_pressure"] = df["drum_pressure"] * MPA_TO_KGCM2   # MPa(g) -> kg/cm2(g)

    units = {
        "steam_flow": "t/h", "bed_temp_avg": "degC",
        "ms_temperature": "degC", "drum_pressure": "kg/cm2(g)",
    }

    # keep the pressure tag in its native MPa(g) too, nothing lost in conversion
    press_mpa = raw[COLMAP["drum_pressure"]].astype(float).to_numpy()
    native_pressure_mpa = {
        "mean": float(press_mpa.mean()),
        "std_full_5day": float(press_mpa.std(ddof=1)),
        "min": float(press_mpa.min()),
        "max": float(press_mpa.max()),
        "quantiser_step": resolution(press_mpa),
        "per_sample_increment_std": float(np.diff(press_mpa).std(ddof=1)),
    }

    tags = {}
    for tag in COLMAP:
        x = df[tag].to_numpy()
        d1 = np.diff(x)                       # per-sample increment
        d2 = np.diff(x, n=2)                  # second difference

        # per-lag drift: std / mean-abs / rms of (x[t+L] - x[t])
        drift = {}
        for name, L in LAGS.items():
            if L >= len(x):
                continue
            dl = diff_lag(x, L)
            drift[name] = {
                "increment_std": float(dl.std(ddof=1)),
                "increment_mean_abs": float(np.abs(dl).mean()),
                "increment_rms": float(np.sqrt(np.mean(dl ** 2))),
            }

        # per-lag autocorrelation of the LEVEL series
        acf = {name: acf_at(x, L) for name, L in LAGS.items() if L < len(x)}

        # AR(1) fit on the level series
        xc = x - x.mean()
        phi = float(np.dot(xc[:-1], xc[1:]) / np.dot(xc[:-1], xc[:-1]))
        innov = xc[1:] - phi * xc[:-1]
        tau_s = float(-DT_S / np.log(phi)) if 0 < phi < 1 else float("nan")

        # within-1h-block range (average peak-to-peak inside a rolling hour)
        blk = 720
        nblk = len(x) // blk
        pp = [np.ptp(x[i * blk:(i + 1) * blk]) for i in range(nblk)]

        tags[tag] = {
            "unit": units[tag],
            "source_column": COLMAP[tag],
            "level": {
                "mean": float(x.mean()),
                "std_full_5day": float(x.std(ddof=1)),
                "cov_pct": float(100.0 * x.std(ddof=1) / abs(x.mean())),
                "min": float(x.min()),
                "max": float(x.max()),
                "p05": float(np.percentile(x, 5)),
                "p50": float(np.percentile(x, 50)),
                "p95": float(np.percentile(x, 95)),
                "range_full": float(x.max() - x.min()),
                "net_change_end_minus_start": float(x[-1] - x[0]),
                "quantiser_step": resolution(x),
            },
            "measurement_noise": {
                "per_sample_increment_std": float(d1.std(ddof=1)),
                "per_sample_increment_mad_std": float(1.4826 * np.median(np.abs(d1 - np.median(d1)))),
                "hf_noise_est_2nd_diff": float(d2.std(ddof=1) / np.sqrt(6.0)),
                "increment_lag1_acf": acf_at(d1, 1),
                "noise_to_5day_drift_ratio": float(d1.std(ddof=1) / x.std(ddof=1)),
                "comment": "per_sample_increment_std is signal+noise; where "
                           "increment_lag1_acf is strongly positive the smooth drift "
                           "dominates and hf_noise_est_2nd_diff is the better estimate "
                           "of true white measurement noise. A white-noise sim gives "
                           "increment std = sigma*sqrt(2) and increment lag-1 acf ~= -0.5.",
            },
            "autocorrelation_level": acf,
            "ar1": {
                "phi": phi,
                "innovation_std": float(innov.std(ddof=1)),
                "timescale_seconds": tau_s,
                "timescale_minutes": tau_s / 60.0 if tau_s == tau_s else float("nan"),
            },
            "drift_by_horizon": drift,
            "within_1h_block_ptp": {
                "mean": float(np.mean(pp)),
                "p50": float(np.percentile(pp, 50)),
                "p95": float(np.percentile(pp, 95)),
                "max": float(np.max(pp)),
            },
        }

    # --- cross-tag correlations --------------------------------------------------
    order = list(COLMAP.keys())
    lvl = df[order].to_numpy()
    inc1m = np.column_stack([diff_lag(df[t].to_numpy(), 12) for t in order])
    inc1h = np.column_stack([diff_lag(df[t].to_numpy(), 720) for t in order])

    def cmat(M):
        C = np.corrcoef(M, rowvar=False)
        return {order[i]: {order[j]: float(C[i, j]) for j in range(len(order))}
                for i in range(len(order))}

    cross = {
        "pearson_level": cmat(lvl),
        "pearson_increment_1min": cmat(inc1m),
        "pearson_increment_1h": cmat(inc1h),
        "note": "level correlation is dominated by the shared slow load trend; "
                "increment correlation isolates co-movement of the dynamics.",
    }

    # --- load-following coefficients ------------------------------------------
    load_lvl = df[LOAD].to_numpy()
    load_stats = {
        "proxy": LOAD,
        "unit": "t/h",
        "mean": float(load_lvl.mean()),
        "std": float(load_lvl.std(ddof=1)),
        "min": float(load_lvl.min()),
        "max": float(load_lvl.max()),
        "p05_p95": [float(np.percentile(load_lvl, 5)), float(np.percentile(load_lvl, 95))],
    }
    lf = {}
    for tag in ("bed_temp_avg", "ms_temperature", "drum_pressure"):
        y = df[tag].to_numpy()
        s_l, i_l, r2_l = ols(y, load_lvl)
        d_load_1h = diff_lag(load_lvl, 720)
        d_tag_1h = diff_lag(y, 720)
        s_d, i_d, r2_d = ols(d_tag_1h, d_load_1h)
        d_load_1m = diff_lag(load_lvl, 12)
        d_tag_1m = diff_lag(y, 12)
        s_m, i_m, r2_m = ols(d_tag_1m, d_load_1m)
        lf[tag] = {
            "unit_per_tph": units[tag].replace("(g)", "") + " per t/h",
            "level_regression": {"slope": s_l, "intercept": i_l, "r2": r2_l},
            "delta_1h_regression": {"slope": s_d, "r2": r2_d},
            "delta_1min_regression": {"slope": s_m, "r2": r2_m},
        }

    fingerprint = {
        "_about": "Statistical fingerprint of data/reference/xinan_completed_data.csv "
                  "(real AFBC-class boiler, ~60 t/h, 9.77 MPa, 5 s sampling, 120 h). "
                  "Calibration target for data/generator/sim.py. All numbers MEASURED "
                  "from the real trace. See data/reference/FINGERPRINT.md.",
        "_generated": str(pd.Timestamp.now("UTC")),
        "_source_csv": CSV,
        "_column_mapping": COLMAP,
        "_pressure_conversion": f"PTCA_8322A.AV_0# in MPa(g) multiplied by {MPA_TO_KGCM2} -> kg/cm2(g)",
        "sampling": sampling,
        "native_pressure_mpa": native_pressure_mpa,
        "tags": tags,
        "cross_tag_correlation": cross,
        "load_following": {"load": load_stats, "coefficients": lf},
    }

    with open(OUT, "w") as f:
        json.dump(fingerprint, f, indent=2)
    print("wrote", OUT)

    # ---- console summary for the working log --------------------------------
    print("\nsampling:", sampling["dt_seconds_median"], "s median,",
          sampling["n_gaps_gt_1p5x"], "gaps,", sampling["span_hours"], "h")
    print(f"\n{'tag':16s} {'mean':>10s} {'sd_5day':>9s} {'inc_std':>9s} "
          f"{'incMADstd':>9s} {'acf_1':>8s} {'acf_1h':>8s} {'phi':>10s} {'tau_min':>9s}")
    for tag, t in tags.items():
        print(f"{tag:16s} {t['level']['mean']:10.3f} {t['level']['std_full_5day']:9.3f} "
              f"{t['measurement_noise']['per_sample_increment_std']:9.4f} "
              f"{t['measurement_noise']['per_sample_increment_mad_std']:9.4f} "
              f"{t['autocorrelation_level']['5s']:8.4f} "
              f"{t['autocorrelation_level']['1h']:8.4f} "
              f"{t['ar1']['phi']:10.6f} {t['ar1']['timescale_minutes']:9.2f}")
    print("\n1h drift (increment_std) / 5day sd / within-1h ptp mean:")
    for tag, t in tags.items():
        print(f"{tag:16s} {t['drift_by_horizon']['1h']['increment_std']:9.3f}  "
              f"{t['level']['std_full_5day']:9.3f}  "
              f"{t['within_1h_block_ptp']['mean']:9.3f}")
    print("\ncross-tag pearson (level):")
    for a in order:
        print(f"  {a:16s}", " ".join(f"{cross['pearson_level'][a][b]:+.3f}" for b in order))
    print("\ncross-tag pearson (1h increments):")
    for a in order:
        print(f"  {a:16s}", " ".join(f"{cross['pearson_increment_1h'][a][b]:+.3f}" for b in order))
    print("\nload-following (load proxy = steam_flow, "
          f"range {load_stats['min']:.2f}-{load_stats['max']:.2f} t/h, sd {load_stats['std']:.3f}):")
    for tag, c in lf.items():
        print(f"  {tag:16s} level slope {c['level_regression']['slope']:+8.4f} "
              f"(r2 {c['level_regression']['r2']:.3f})   "
              f"d1h slope {c['delta_1h_regression']['slope']:+8.4f} "
              f"(r2 {c['delta_1h_regression']['r2']:.3f})")


if __name__ == "__main__":
    main()
