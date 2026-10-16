"""How the erosion forecast is trained, tuned and tested, in one place.

The protocol (fixed in code, so it can't drift towards a better-looking score):

  years      role
  ---------  ----------------------------------------------------------------
  1995-2016  TRAIN        the model learns from these
  2017-2020  VALIDATION   every choice is made here: which inputs to keep,
                          model settings, probability calibration and the
                          alert thresholds. Each validation year is predicted
                          by a model trained only on the years before it
                          ("rolling origin"), exactly like a real forecast.
  2021-2025  TEST         touched once, at the end, by a model trained on
                          1995-2020 with the frozen choices. These are the
                          numbers we report.
  2026       LIVE         the forecast for this monsoon, from a model trained
                          on everything known.

A year's label (did this cell lose >= 1 ha of vegetated land to the river
by the next dry season?) is known only once the next dry-season map exists,
so the model forecasting year v never sees labels of year v or later.

False alarms are reported, not hidden. Two alert tiers:
  * HIGH ALERT: calibrated risk at or above a threshold chosen on the
    validation years so that at least HIGH_PRECISION_TARGET of flagged
    cells really erode. Only these would trigger the (simulated) SMS.
  * WATCH: the broader list, down to the risk that flagged about
    WATCH_SHARE of cells in the validation years.
For each tier we report precision (share of flagged cells that eroded),
recall, the share of lost land caught and the false-alarm rate.
"""
import json

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from detector import forecast as fc

TRAIN_YEARS = (1995, 2016)
VAL_YEARS = (2017, 2020)
TEST_YEARS = (2021, 2025)
SEED = 42
HIGH_PRECISION_TARGET = 0.5   # at least half of HIGH ALERT cells should really erode
WATCH_SHARE = 0.20            # WATCH covers about the riskiest 20 % of bank (as in version 1)
MIN_FLAGGED = 50              # don't pick a threshold that only a handful of validation cells pass
KEEP_IF_GAIN = 0.003          # a new input group must add this much validation average precision

# Version 1's settings (kept as the starting point, and as the fair comparison).
V1_PARAMS = {"learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 40,
             "l2_regularization": 1.0, "max_iter": 300}
PARAM_GRID = [dict(V1_PARAMS, learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=leaf)
              for lr, leaves, leaf in [(0.05, 31, 40), (0.05, 63, 40), (0.05, 31, 200),
                                       (0.1, 31, 40), (0.05, 63, 200), (0.03, 63, 100)]]
TIER_NAMES = {2: "high", 1: "watch", 0: "scored"}


def make_model(params, seed=SEED):
    # No early stopping: it would hold out a random 10 % of rows, mixing
    # years. The number of trees is a tuned setting instead.
    return HistGradientBoostingClassifier(early_stopping=False, random_state=seed, **params)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_tables(cache_root, years, rebuild=False, reaches=None):
    """Cell x year tables for every reach (built from the cached dry-season
    maps if missing), concatenated. Adds `reach` (index into fc.REACHES) and
    `label`."""
    parts = []
    for ri, name in enumerate(fc.REACHES):
        if reaches and name not in reaches:
            continue
        d = cache_root / name
        path = d / "table.npz"
        tab = dict(np.load(path)) if path.exists() and not rebuild else None
        if tab is not None and list(tab.get("features", [])) != fc.FEATURES:
            tab = None  # built with other inputs: rebuild
        if tab is None:
            if not all((d / f"dry_{y}.npz").exists() for y in years):
                print(f"   !! {name}: dry-season maps missing, skipped (run run_forecast.py to download)")
                continue
            print(f">> Building the cell x year table for {name}")
            water, ndvi = [], []
            for y in years:
                z = np.load(d / f"dry_{y}.npz")
                water.append(fc.clean_water(z["water"], z["ndvi"]))
                ndvi.append(z["ndvi"])
            tab = fc.build_table(years, np.stack(water), np.stack(ndvi), name)
            del water, ndvi
            np.savez_compressed(path, **tab)
        tab.pop("features", None)
        tab["reach"] = np.full(len(tab["year"]), ri, np.int8)
        parts.append(tab)
    t = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    t["known"] = ~np.isnan(t["loss_ha"])
    t["label"] = np.where(t["known"], t["loss_ha"] >= fc.MIN_LOSS_HA, False)
    t["river"] = np.array([fc.REACHES[n]["river"] for n in fc.REACHES])[t["reach"]]
    return t


def between(year, span):
    return (year >= span[0]) & (year <= span[1])


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def ranking(score, t, sel):
    """How well a score ranks eroding vs safe cells (threshold-free)."""
    y, loss = t["label"][sel], t["loss_ha"][sel]
    ok = 0 < y.mean() < 1
    return {"auc": round(float(roc_auc_score(y, score)), 3) if ok else None,
            "avg_precision": round(float(average_precision_score(y, score)), 3) if ok else None,
            "capture_top10": round(fc.capture(score, loss, 0.10), 3),
            "capture_top20": round(fc.capture(score, loss, 0.20), 3),
            "cells": int(sel.sum()), "eroded_cells": int(y.sum()), "base_rate": round(float(y.mean()), 4),
            "lost_ha": round(float(loss.sum()), 1)}


def _keys(reach, year, row, col):
    return (((reach.astype(np.int64) * 64 + (year.astype(np.int64) - 1980)) * 16384 + row) * 16384 + col)


def tier_scores(flag, t, sel):
    """Precision / recall / false alarms for cells flagged by a tier."""
    y, loss = t["label"][sel], t["loss_ha"][sel]
    f = flag[sel]
    tp, fp = int((f & y).sum()), int((f & ~y).sum())
    fn, tn = int((~f & y).sum()), int((~f & ~y).sum())
    n_years = len(np.unique(t["year"][sel]))
    # Tolerant version: a flagged cell also counts as right if a neighbouring
    # 240 m cell eroded that year (warnings are for a stretch of bank, and
    # cell edges are arbitrary). Reported next to, never instead of, the strict one.
    eroded = _keys(t["reach"][sel][y], t["year"][sel][y], t["row"][sel][y], t["col"][sel][y])
    fr, fyr, frow, fcol = (t[k][sel][f] for k in ("reach", "year", "row", "col"))
    near = np.zeros(f.sum(), bool)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            near |= np.isin(_keys(fr, fyr, frow + dr, fcol + dc), eroded)
    precision = tp / max(tp + fp, 1)
    return {"flagged_cells": int(f.sum()), "flagged_share": round(float(f.mean()), 4),
            "precision": round(precision, 3),
            # How many times more likely a flagged cell is to erode than an average scored cell.
            "lift": round(precision / y.mean(), 1) if y.any() else None,
            "precision_within_240m": round(float(near.mean()), 3) if f.any() else None,
            "recall_cells": round(tp / max(tp + fn, 1), 3),
            "land_caught": round(float(loss[f].sum() / loss.sum()), 3) if loss.sum() > 0 else None,
            "false_alarm_rate": round(fp / max(fp + tn, 1), 4),
            "false_alarms_per_year": round(fp / max(n_years, 1), 1),
            "true_alerts_per_year": round(tp / max(n_years, 1), 1)}


def tiers_of(p, thr):
    return np.where(p >= thr["high"], 2, np.where(p >= thr["watch"], 1, 0)).astype(np.int8)


def tier_block(p, t, sel, thr):
    tier = tiers_of(p, thr)
    return {"high": tier_scores(tier == 2, t, sel), "high_or_watch": tier_scores(tier >= 1, t, sel)}


def precision_vs_coverage(p, t, sel, shares=(0.005, 0.01, 0.02, 0.05, 0.10, 0.20)):
    """The trade-off a shorter or longer list buys: for the riskiest `share`
    of bank, the precision and the share of lost land caught. Descriptive
    only; the alert tiers stay the ones fixed on the validation years."""
    y, loss, s = t["label"][sel], t["loss_ha"][sel], p[sel]
    order = np.argsort(-s, kind="stable")
    rows = []
    for share in shares:
        k = max(1, int(round(share * len(s))))
        rows.append({"share": share, "precision": round(float(y[order[:k]].mean()), 3),
                     "land_caught": round(float(loss[order[:k]].sum() / loss.sum()), 3)})
    return rows


def reliability(p, y, edges=(0, .02, .05, .1, .2, .3, .5, .7, 1.0001)):
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        s = (p >= lo) & (p < hi)
        if s.sum():
            rows.append({"from": lo, "to": min(hi, 1.0), "cells": int(s.sum()),
                         "predicted": round(float(p[s].mean()), 3), "observed": round(float(y[s].mean()), 3)})
    return rows


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def rolling_predictions(t, cols, params, years):
    """Each year v predicted by a model trained only on years < v."""
    p = np.full(len(t["year"]), np.nan)
    for v in years:
        tr = t["known"] & (t["year"] < v) & (t["year"] >= TRAIN_YEARS[0])
        te = t["known"] & (t["year"] == v)
        m = make_model(params).fit(t["X"][tr][:, cols], t["label"][tr].astype(int))
        p[te] = m.predict_proba(t["X"][te][:, cols])[:, 1]
    return p


def _val_score(t, p, val):
    y = t["label"][val]
    return float(average_precision_score(y, p[val])), float(roc_auc_score(y, p[val]))


def pick_thresholds(p, y):
    """HIGH: the lowest calibrated risk at which flagged validation cells are
    at least HIGH_PRECISION_TARGET right (so as many real losses as possible
    are caught at that precision). WATCH: the risk that flags ~WATCH_SHARE."""
    order = np.argsort(-p, kind="stable")
    ps, ys = p[order], y[order].astype(float)
    tp = np.cumsum(ys)
    k = np.arange(1, len(ps) + 1)
    last_of_tie = np.r_[ps[1:] != ps[:-1], True]       # "p >= t" includes all ties
    ok = last_of_tie & (k >= MIN_FLAGGED)
    prec = tp / k
    good = ok & (prec >= HIGH_PRECISION_TARGET)
    if good.any():
        i = np.nonzero(good)[0].max()
        met = True
    else:                                               # target not reachable: most precise tier
        i = np.nonzero(ok)[0][np.argmax(prec[ok])]
        met = False
    high = float(ps[i])
    watch = min(float(np.quantile(p, 1 - WATCH_SHARE)), high)
    return {"high": high, "watch": watch, "high_precision_target": HIGH_PRECISION_TARGET,
            "target_met_on_validation": met,
            "validation_high_precision": round(float(prec[i]), 3),
            "validation_high_flagged_share": round(float(k[i] / len(ps)), 4)}


def run(t, site_cells=None, log=print):
    """The whole protocol. Returns (report, outputs) where outputs holds the
    arrays needed for the map and the track record."""
    yr, X, known = t["year"], t["X"], t["known"]
    val = known & between(yr, VAL_YEARS)
    test = known & between(yr, TEST_YEARS)
    fit_rows = known & (yr >= TRAIN_YEARS[0]) & (yr <= VAL_YEARS[1])     # train + validation
    fi = {n: i for i, n in enumerate(fc.FEATURES)}
    log(f"   {int((known & between(yr, TRAIN_YEARS)).sum())} train, {int(val.sum())} validation, "
        f"{int(test.sum())} test cell-years; {len(np.unique(t['reach']))} reaches")

    # 1. Which inputs (validation years only).
    val_years = range(VAL_YEARS[0], VAL_YEARS[1] + 1)
    chosen = list(fc.BASE_FEATURES)
    cols = [fi[f] for f in chosen]
    base_ap, base_auc = _val_score(t, rolling_predictions(t, cols, V1_PARAMS, val_years), val)
    selection = [{"inputs": "version 1 inputs", "val_avg_precision": round(base_ap, 4),
                  "val_auc": round(base_auc, 4), "kept": True}]
    log(f"   validation, version-1 inputs: AP {base_ap:.4f}, AUC {base_auc:.4f}")
    best_ap = base_ap
    for group, feats in fc.FEATURE_GROUPS.items():
        trial = [fi[f] for f in chosen + feats]
        ap, auc = _val_score(t, rolling_predictions(t, trial, V1_PARAMS, val_years), val)
        keep = ap >= best_ap + KEEP_IF_GAIN
        selection.append({"inputs": f"+ {group}", "val_avg_precision": round(ap, 4),
                          "val_auc": round(auc, 4), "kept": bool(keep)})
        log(f"   validation, + {group:16s}: AP {ap:.4f}, AUC {auc:.4f} -> {'keep' if keep else 'drop'}")
        if keep:
            chosen += feats
            best_ap = ap
    cols = [fi[f] for f in chosen]

    # 2. Model settings (validation years only).
    tuning = []
    best = (best_ap, V1_PARAMS)
    for params in PARAM_GRID[1:]:
        ap, auc = _val_score(t, rolling_predictions(t, cols, params, val_years), val)
        tuning.append({"params": params, "val_avg_precision": round(ap, 4), "val_auc": round(auc, 4)})
        log(f"   validation, settings {params['learning_rate']}/{params['max_leaf_nodes']}/"
            f"{params['min_samples_leaf']}: AP {ap:.4f}")
        if ap > best[0] + 0.001:
            best = (ap, params)
    params = best[1]
    tuning.insert(0, {"params": V1_PARAMS, "val_avg_precision": round(best_ap, 4)})

    # 3. Calibration and alert thresholds (validation years only).
    p_val = rolling_predictions(t, cols, params, val_years)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(p_val[val], t["label"][val])
    pc_val = iso.predict(p_val[val])
    thr = pick_thresholds(pc_val, t["label"][val])
    log(f"   thresholds (from validation): HIGH >= {thr['high']:.3f} (precision "
        f"{thr['validation_high_precision']:.0%} on validation), WATCH >= {thr['watch']:.3f}")

    # 4. The test: one model trained on 1995-2020, scored on 2021-2025.
    clf = make_model(params).fit(X[fit_rows][:, cols], t["label"][fit_rows].astype(int))
    p_raw = np.full(len(yr), np.nan)
    p_raw[test] = clf.predict_proba(X[test][:, cols])[:, 1]
    p_cal = np.full(len(yr), np.nan)
    p_cal[test] = iso.predict(p_raw[test])

    # Version 1 re-run on the same data and labels: the fair before/after comparison.
    b_cols = [fi[f] for f in fc.BASE_FEATURES]
    v1 = make_model(V1_PARAMS).fit(X[fit_rows][:, b_cols], t["label"][fit_rows].astype(int))
    p_v1 = np.full(len(yr), np.nan)
    p_v1[test] = v1.predict_proba(X[test][:, b_cols])[:, 1]

    scorers = {
        "ai": lambda s: p_raw[s],
        "ai_version1": lambda s: p_v1[s],
        # "The river takes the land closest to it first."
        "closest_to_river": lambda s: -X[s, fi["dist_river_m"]],
        # "Where it eroded last year, it erodes again."
        "eroded_last_year": lambda s: X[s, fi["loss_nearby_1y_ha"]] - 1e-6 * X[s, fi["dist_river_m"]],
    }
    groups = {"all_rivers": test}
    for river in np.unique(t["river"]):
        groups[river] = test & (t["river"] == river)
    groups["jamuna_pilot_reach"] = test & (t["reach"] == list(fc.REACHES).index(fc.PILOT_REACH))
    if site_cells is not None:
        groups["kazipur_chauhali"] = test & site_cells
    report = {"protocol": {"train_years": TRAIN_YEARS, "validation_years": VAL_YEARS,
                           "test_years": TEST_YEARS,
                           "reaches": {k: v["river"] for i, (k, v) in enumerate(fc.REACHES.items())
                                       if (t["reach"] == i).any()},
                           "min_loss_ha": fc.MIN_LOSS_HA, "cell_m": fc.CELL_PX * fc.PIXEL_M},
              "input_selection": selection, "inputs_used": chosen,
              "tuning": tuning, "params": params, "thresholds": thr,
              "ranking": {}, "tiers": {}, "per_year": {}}
    for g, sel in groups.items():
        if not sel.any():
            continue
        report["ranking"][g] = {name: ranking(f(sel), t, sel) for name, f in scorers.items()}
        report["tiers"][g] = tier_block(p_cal, t, sel, thr)
    for y in range(TEST_YEARS[0], TEST_YEARS[1] + 1):
        s = test & (yr == y)
        report["per_year"][str(y)] = {"ranking": {n: ranking(f(s), t, s) for n, f in scorers.items()},
                                      "tiers": tier_block(p_cal, t, s, thr)}
    for g in report["ranking"]:
        a, b = report["ranking"][g]["ai"], report["tiers"][g]["high"]
        log(f"   TEST {g:20s}: AUC {a['auc']}, AP {a['avg_precision']}, top-20% catches "
            f"{a['capture_top20']:.0%} | HIGH ALERT precision {b['precision']:.0%} "
            f"({b['precision_within_240m']} within 240 m), catches {b['land_caught']:.0%} of lost land")

    y_test = t["label"][test]
    report["calibration"] = {"brier_raw": round(float(brier_score_loss(y_test, p_raw[test])), 4),
                             "brier_calibrated": round(float(brier_score_loss(y_test, p_cal[test])), 4),
                             "reliability_test": reliability(p_cal[test], y_test)}
    report["precision_vs_coverage"] = precision_vs_coverage(p_raw, t, test)

    # 5. Honesty checks.
    rng = np.random.default_rng(SEED)
    aucs, spreads = [], []
    for _ in range(3):
        shuf = make_model(params).fit(X[fit_rows][:, cols], rng.permutation(t["label"][fit_rows].astype(int)))
        ps = shuf.predict_proba(X[test][:, cols])[:, 1]
        aucs.append(roc_auc_score(y_test, ps))
        spreads.append(float(ps.std()))
    report["shuffled_label_check"] = {"auc_min": round(min(aucs), 3), "auc_max": round(max(aucs), 3),
                                      "prediction_spread_pct_points": round(100 * max(spreads), 2)}
    log(f"   shuffled labels: AUC {min(aucs):.2f}-{max(aucs):.2f} (no skill, as it should be)")

    # A river the model never saw: train on the Jamuna only, test on the Ganges-Padma.
    rivers = list(np.unique(t["river"]))
    if len(rivers) > 1:
        report["unseen_river"] = {}
        for held in rivers:
            tr = fit_rows & (t["river"] != held)
            s = test & (t["river"] == held)
            m = make_model(params).fit(X[tr][:, cols], t["label"][tr].astype(int))
            ph = m.predict_proba(X[s][:, cols])[:, 1]
            report["unseen_river"][held] = {"never_seen": ranking(ph, t, s),
                                            "trained_with_it": ranking(p_raw[s], t, s)}
            log(f"   {held} never seen in training: AUC {report['unseen_river'][held]['never_seen']['auc']} "
                f"(vs {report['unseen_river'][held]['trained_with_it']['auc']} when trained with it)")

    imp = permutation_importance(clf, X[test][:, cols], y_test.astype(int), scoring="average_precision",
                                 n_repeats=3, random_state=SEED, max_samples=min(30000, int(test.sum())))
    report["importance"] = {fc.FEATURE_LABELS[chosen[i]]: round(float(v), 4)
                            for i, v in sorted(enumerate(imp.importances_mean), key=lambda kv: -kv[1])}

    # 6. The live forecast: trained on everything known, calibrated on every
    # out-of-time prediction we have (validation + test years), same thresholds.
    oot = val | test
    p_oot = np.where(val, p_val, p_raw)
    iso_live = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(p_oot[oot], t["label"][oot])
    final = make_model(params).fit(X[known][:, cols], t["label"][known].astype(int))
    latest = yr == yr.max()
    p_live = iso_live.predict(final.predict_proba(X[latest][:, cols])[:, 1])
    report["live"] = {"year": int(yr.max()),
                      "cells": int(latest.sum()),
                      "high_cells": int((p_live >= thr["high"]).sum()),
                      "watch_cells": int(((p_live >= thr["watch"]) & (p_live < thr["high"])).sum())}
    log(f"   live forecast {int(yr.max())}: {report['live']['high_cells']} HIGH ALERT, "
        f"{report['live']['watch_cells']} WATCH cells of {int(latest.sum())}")

    blind = test | latest
    p_blind = np.where(latest, np.nan, p_cal)
    p_blind[latest] = p_live
    outputs = {"test": test, "latest": latest, "p_live": p_live, "blind": blind,
               "p_blind": p_blind, "tier_blind": tiers_of(np.nan_to_num(p_blind, nan=-1), thr),
               "thresholds": thr}
    return report, outputs


def to_json(obj):
    return json.loads(json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else
                                 list(o) if isinstance(o, (tuple, np.ndarray)) else str(o)))
