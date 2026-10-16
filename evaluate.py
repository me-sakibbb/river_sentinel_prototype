"""Re-run the erosion-forecast evaluation OFFLINE, from the cached dry-season
maps in data/forecast/<reach>/ (no Earth Engine needed).

    python evaluate.py                 # uses cached tables if present
    python evaluate.py --rebuild       # recompute every input from the maps

Prints the validation choices and the test scores, and writes
data/forecast/report.json and data/forecast/predictions.npz. The protocol
(which years train, tune and test) is in detector/evaluation.py.
"""
import argparse
import json

import numpy as np

import config
from detector import evaluation as ev, forecast as fc

CACHE = config.ROOT / "data" / "forecast"
SITE_MASKS = CACHE / "site_masks.npz"


def site_cells(t):
    """Which table rows are in Kazipur or Chauhali (cached upazila masks on
    the pilot reach's cell grid; made by run_forecast.py)."""
    if not SITE_MASKS.exists():
        return None, {}
    masks = dict(np.load(SITE_MASKS))
    pilot = t["reach"] == list(fc.REACHES).index(fc.PILOT_REACH)
    per_site = {}
    for sid, m in masks.items():
        r = np.clip(t["row"], 0, m.shape[0] - 1)
        c = np.clip(t["col"], 0, m.shape[1] - 1)
        per_site[sid] = pilot & m[r, c]
    return np.logical_or.reduce(list(per_site.values())), per_site


def evaluate(rebuild=False, reaches=None):
    years = list(range(fc.FIRST_YEAR, fc.LAST_YEAR + 1))
    t = ev.load_tables(CACHE, years, rebuild=rebuild, reaches=reaches)
    in_sites, per_site = site_cells(t)
    report, out = ev.run(t, in_sites)
    v1 = CACHE / "report_v1.json"
    if v1.exists():
        old = json.loads(v1.read_text())
        report["version1_as_published"] = {"note": "Version 1 (Jamuna reach only, labels counted any "
                                                   "vegetated land that became water, inputs could see "
                                                   "later years through gap filling and the corridor).",
                                           "whole_reach": old["overall"], "kazipur_chauhali": old["sites"]}
    report = ev.to_json(report)
    (CACHE / "report.json").write_text(json.dumps(report, indent=1))
    b = out["blind"]
    np.savez_compressed(CACHE / "predictions.npz", reach=t["reach"][b], year=t["year"][b], row=t["row"][b],
                        col=t["col"][b], p=out["p_blind"][b], tier=out["tier_blind"][b],
                        loss_ha=t["loss_ha"][b])
    return t, report, out, per_site


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="recompute the cell x year tables")
    ap.add_argument("--reach", action="append", help="only these reaches (default: all with data)")
    args = ap.parse_args()
    evaluate(args.rebuild, args.reach)
    print(f"\nWrote {CACHE / 'report.json'}")


if __name__ == "__main__":
    main()
