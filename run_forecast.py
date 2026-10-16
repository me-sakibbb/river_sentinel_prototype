"""Erosion forecast: download the dry-season maps, run the evaluation protocol
(detector/evaluation.py) and write the dashboard's forecast (web/data/forecast.js).

    python run_forecast.py --project <your-earth-engine-cloud-project>

Earth Engine is only needed to download maps that aren't cached yet (about
15-40 s per reach-year, cached in data/forecast/<reach>/) and the upazila
masks. Everything after that runs locally; `python evaluate.py` reruns the
evaluation alone, offline.
"""
import argparse
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import config
import evaluate
from detector import alert, forecast as fc

CACHE = config.ROOT / "data" / "forecast"


def download(years, workers=3):
    todo = [(name, y) for name in fc.REACHES for y in years
            if not (CACHE / name / f"dry_{y}.npz").exists()]
    if not todo:
        return
    print(f">> Downloading {len(todo)} Landsat dry-season maps ({workers} at a time)")
    grids = {name: fc.reach_grid(name) for name in fc.REACHES}

    def one(job):
        name, y = job
        fc.fetch_year(y, grids[name], CACHE / name)
        print(f"   {name} {y} done")
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, todo))


def site_masks():
    """Which 240 m cells of the pilot reach fall inside each upazila (+1 km)."""
    if evaluate.SITE_MASKS.exists():
        return
    import ee
    from detector import imagery
    from run_analysis import load_site
    grid = fc.reach_grid(fc.PILOT_REACH)
    masks = {}
    for sid, site in config.SITES.items():
        _, region = load_site(site)
        arr = imagery.fetch_array(ee.Image(1).clip(region).unmask(0).toByte().rename("v"), grid)["v"]
        masks[sid] = fc._cells(arr == 1, "mean") > 0.5
    np.savez_compressed(evaluate.SITE_MASKS, **masks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get(config.EE_PROJECT_ENV))
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="recompute the cell x year tables")
    ap.add_argument("--offline", action="store_true", help="don't contact Earth Engine (use cached maps)")
    ap.add_argument("--export-only", action="store_true",
                    help="skip the evaluation; rewrite the dashboard files from the last "
                         "evaluate.py run (e.g. after new radar results)")
    args = ap.parse_args()
    years = list(range(fc.FIRST_YEAR, fc.LAST_YEAR + 1))
    if args.export_only:
        export(*saved_evaluation(years))
        return
    if not args.offline:
        if not args.project:
            ap.error(f"give --project, set {config.EE_PROJECT_ENV}, or use --offline")
        from run_analysis import init_ee
        init_ee(args.project)
        download(years)
        site_masks()
    if args.download_only:
        return
    t, report, out, per_site = evaluate.evaluate(args.rebuild)
    export(t, report, out, per_site)


def saved_evaluation(years):
    """The last evaluate.py run, reloaded: tables (cached), report.json and
    the live-year risks in predictions.npz. Nothing is re-trained."""
    from detector import evaluation as ev
    t = ev.load_tables(CACHE, years)
    report = json.loads((CACHE / "report.json").read_text())
    pr = np.load(CACHE / "predictions.npz")
    latest = t["year"] == report["live"]["year"]
    live = pr["year"] == report["live"]["year"]
    # Same rows, same order (both are the table's rows for the live year).
    assert (pr["reach"][live] == t["reach"][latest]).all() and (pr["row"][live] == t["row"][latest]).all()
    _, per_site = evaluate.site_cells(t)
    out = {"latest": latest, "p_live": pr["p"][live], "thresholds": report["thresholds"]}
    return t, report, out, per_site


# ---------------------------------------------------------------------------
# Dashboard export
# ---------------------------------------------------------------------------

REASONS = [
    ("loss_nearby_1y_ha", lambda v: v >= 1, lambda v: f"bank nearby lost {v:.0f} ha last year"),
    ("years_eroded_last5", lambda v: v >= 3, lambda v: f"erosion nearby in {v:.0f} of the last 5 years"),
    ("main_approach_3y_m", lambda v: v >= 30, lambda v: f"main channel moving closer, ~{v:.0f} m/yr"),
    ("river_approach_3y_m", lambda v: v >= 30, lambda v: f"a channel moving closer, ~{v:.0f} m/yr"),
    ("dist_main_m", lambda v: v <= 300, lambda v: f"main channel only ~{v:.0f} m away"),
    ("dist_river_m", lambda v: v <= 60, lambda v: "right at the water's edge"),
    ("water_upstream_1km", lambda v: v >= 0.5, lambda v: "open water just upstream"),
    ("land_age_years", lambda v: v <= 5, lambda v: f"young land (~{v:.0f} yr since last water)"),
    ("char_share", lambda v: v >= 0.5, lambda v: "on a char (island)"),
]
TIER_COLOURS = {0: (246, 231, 189, 120), 1: (217, 138, 28, 215), 2: (179, 38, 30, 255)}
ESCALATE_WITHIN_M = 500
M_LAT = 111320.0


def _cell_lonlat(grid, rows, cols):
    lat = grid.north - (rows * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlat
    lon = grid.west + (cols * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlon
    return lon, lat


def _bank_erosion(run_id, sid):
    path = config.WEB_DATA_DIR / "results.json"
    if not path.exists():
        return None, []
    r = json.loads(path.read_text(encoding="utf8"))["results"].get(f"{run_id}/{sid}") or {}
    pts = [(f["geometry"]["coordinates"], f["properties"]) for f in (r.get("patches") or {}).get("features", [])
           if f["properties"].get("kind") == "bank_erosion"]
    return r, pts


def export(t, report, out, per_site):
    from PIL import Image
    latest, p_live = out["latest"], out["p_live"]
    thr = out["thresholds"]
    tier_live = np.where(p_live >= thr["high"], 2, np.where(p_live >= thr["watch"], 1, 0))
    reach_ids = list(fc.REACHES)
    out_dir = config.WEB_DATA_DIR / "forecast"
    out_dir.mkdir(parents=True, exist_ok=True)

    # One transparent risk map per reach: High alert / Watch / other scored land.
    reaches = []
    for ri, name in enumerate(reach_ids):
        sel = t["reach"][latest] == ri
        if not sel.any():
            continue
        grid = fc.reach_grid(name)
        H, W = grid.height // fc.CELL_PX, grid.width // fc.CELL_PX
        rgba = np.zeros((H, W, 4), np.uint8)
        rows, cols, tiers = t["row"][latest][sel], t["col"][latest][sel], tier_live[sel]
        for k in (0, 1, 2):
            rgba[rows[tiers == k], cols[tiers == k]] = TIER_COLOURS[k]
        Image.fromarray(rgba, "RGBA").save(out_dir / f"risk_{name}.png", optimize=True)
        w, s, e, n = grid.bounds
        reaches.append({"id": name, "river": fc.REACHES[name]["river"], "png": f"data/forecast/risk_{name}.png",
                        "bounds": [w, n - H * fc.CELL_PX * grid.dlat, w + W * fc.CELL_PX * grid.dlon, n]})
    old = out_dir / "risk.png"
    if old.exists():
        old.unlink()

    # Per upazila: counts, the riskiest flagged stretches with reasons,
    # simulated warnings (High alert only) and radar escalation.
    grid = fc.reach_grid(fc.PILOT_REACH)
    fi = {k: i for i, k in enumerate(fc.FEATURES)}
    X = t["X"][latest]
    lon_all, lat_all = _cell_lonlat(grid, t["row"][latest], t["col"][latest])
    latest_run = config.RUNS[-1]
    watch, counts, alerts, escalated = {}, {}, {}, {}
    issued = f"dry season (Jan-Mar) {report['live']['year']}"
    for sid, mask in per_site.items():
        inside = mask[latest]
        counts[sid] = {name: int(((tier_live == k) & inside).sum())
                       for k, name in ((2, "high"), (1, "watch"), (0, "scored"))}
        idx = np.nonzero(inside & (tier_live >= 1))[0]
        idx = idx[np.argsort(-p_live[idx])][:15]
        items = []
        for i in idx:
            hits = [k for k, ok, _ in REASONS if ok(X[i, fi[k]])]
            if "main_approach_3y_m" in hits and "river_approach_3y_m" in hits:
                hits.remove("river_approach_3y_m")   # same channel, said once
            why = [txt(X[i, fi[k]]) for k, _, txt in REASONS if k in hits][:3]
            items.append({"lat": round(float(lat_all[i]), 5), "lon": round(float(lon_all[i]), 5),
                          "risk": round(float(p_live[i]), 3), "tier": "high" if tier_live[i] == 2 else "watch",
                          "why": why})
        watch[sid] = items
        alerts[sid] = alert.build_forecast_alert(sid, config.SITES[sid]["name"],
                                                 [c for c in items if c["tier"] == "high"], issued)
        # Escalation: a flagged stretch with CONFIRMED bank erosion (established
        # land) detected within 500 m in the latest radar pass.
        res, pts = _bank_erosion(latest_run["id"], sid)
        m_lon = M_LAT * math.cos(math.radians(24.4))
        cells = []
        for i in np.nonzero(inside & (tier_live >= 1))[0]:
            near = [(p, math.hypot((lon - lon_all[i]) * m_lon, (lat - lat_all[i]) * M_LAT)) for (lon, lat), p in pts]
            near = [(p, d) for p, d in near if d <= ESCALATE_WITHIN_M]
            if near:
                cells.append({"lat": round(float(lat_all[i]), 5), "lon": round(float(lon_all[i]), 5),
                              "risk": round(float(p_live[i]), 3), "tier": "high" if tier_live[i] == 2 else "watch",
                              "detected_ha": round(sum(p["area_ha"] for p, _ in near), 1),
                              "nearest_m": int(min(d for _, d in near))})
        cells.sort(key=lambda c: -c["risk"])
        escalated[sid] = {"run": latest_run["id"], "label": latest_run["label"],
                          "after_dates": ((res or {}).get("scenes") or {}).get("after_dates"),
                          "total": len(cells), "cells": cells[:20]}
        print(f"   {sid}: {counts[sid]['high']} HIGH ALERT, {counts[sid]['watch']} WATCH cells; "
              f"{len(cells)} escalated by the latest radar pass")

    payload = {"report": report, "issued_from": f"Jan–Mar {report['live']['year']}", "reaches": reaches,
               "watch": watch, "tier_counts": counts, "alerts": alerts, "escalated": escalated,
               "sites": {k: v["name"] for k, v in config.SITES.items()}}
    text = json.dumps(payload, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    (config.WEB_DATA_DIR / "forecast.json").write_text(text, encoding="utf8")
    (config.WEB_DATA_DIR / "forecast.js").write_text(f"window.FORECAST = {text};\n", encoding="utf8")
    np.savez_compressed(CACHE / "forecast_latest.npz", reach=t["reach"][latest], row=t["row"][latest],
                        col=t["col"][latest], risk=p_live, tier=tier_live, X=X, year=report["live"]["year"])
    print("   wrote web/data/forecast.js")


if __name__ == "__main__":
    main()
