"""Track record: did the forecast see it coming, and did the satellites see it
happen? Checks news-reported erosion events and picks case studies from the
years the forecast never trained on. Writes web/data/trackrecord.js.

    python run_forecast.py --project <id>      # first (writes predictions.npz)
    python run_trackrecord.py --project <id>

Every forecast used here was issued "blind": for 2021-2025 by the model
trained only on 1998-2020, and for 2026 by the model trained on the years
before it. The outcome comes from the next dry-season Landsat map (land that
really became river), or, for 2026 where that map doesn't exist yet, from
our radar detector (an upper bound during the monsoon).
"""
import argparse
import json
import math
import os

import ee
import numpy as np
from PIL import Image, ImageDraw

import config
from detector import forecast as fc, imagery, water
from run_analysis import init_ee

CACHE = config.ROOT / "data" / "forecast"
OUT_DIR = config.WEB_DATA_DIR / "trackrecord"
# "Flagged" = on the forecast's HIGH ALERT or WATCH list that year (the tiers
# and their thresholds are fixed on the validation years; detector/evaluation.py).
WINDOW_M = 4000           # half-size of the picture window around a case
CASES = 4                 # "predicted and happened" examples to show
M_LAT = 111320.0


def percentiles(year, p):
    """Rank of each cell among all cells scored the same year (0 = riskiest)."""
    pct = np.empty(len(p))
    for y in np.unique(year):
        idx = np.nonzero(year == y)[0]
        order = idx[np.argsort(-p[idx], kind="stable")]
        pct[order] = np.arange(len(order)) / len(order)
    return pct


def dist_m(lat1, lon1, lat2, lon2):
    return np.hypot((lat1 - lat2) * M_LAT, (lon1 - lon2) * M_LAT * np.cos(np.radians(24.4)))


def nearest_union(unions, lat, lon):
    u = min(unions, key=lambda u: dist_m(u["lat"], u["lon"], lat, lon))
    return f"{u['union']} union, {u['upazila']}"


def load_water(year):
    d = np.load(CACHE / fc.PILOT_REACH / f"dry_{year}.npz")
    return fc.clean_water(d["water"], d["ndvi"]), d["ndvi"]


def window(grid, lat, lon, half_m=WINDOW_M):
    r0 = int((grid.north - lat) / grid.dlat)
    c0 = int((lon - grid.west) / grid.dlon)
    hr, hc = int(half_m / (grid.dlat * M_LAT)), int(half_m / (grid.dlon * M_LAT * math.cos(math.radians(lat))))
    r1, r2 = max(0, r0 - hr), min(grid.height, r0 + hr)
    c1, c2 = max(0, c0 - hc), min(grid.width, c0 + hc)
    n = grid.north - r1 * grid.dlat
    s = grid.north - r2 * grid.dlat
    w = grid.west + c1 * grid.dlon
    e = grid.west + c2 * grid.dlon
    return (r1, r2, c1, c2), [w, s, e, n]


def outcome_panel(grid, win, year, cells, tiers, path, radar_pts=None, scale=3):
    """Local picture at 30 m: water before (blue), land, the forecast's flagged
    cells (outlined) and the land that was actually lost (red)."""
    (r1, r2, c1, c2) = win
    wb, nb = load_water(year)
    wb, nb = wb[r1:r2, c1:c2], nb[r1:r2, c1:c2]
    img = np.zeros(wb.shape + (3,), np.uint8)
    img[:] = (236, 231, 218)                               # bare land / sand
    img[(nb >= 100 + 100 * fc.MIN_NDVI) & (nb != fc.NODATA)] = (190, 214, 164)  # vegetated land
    img[wb == 1] = (74, 144, 196)                          # river (dry season)
    if year + 1 <= fc.LAST_YEAR and radar_pts is None:
        wa, _ = load_water(year + 1)
        lost = (wb == 0) & (nb >= 100 + 100 * fc.MIN_NDVI) & (nb != fc.NODATA) & (wa[r1:r2, c1:c2] == 1)
        img[lost] = (214, 39, 40)
    im = Image.fromarray(img).resize((img.shape[1] * scale, img.shape[0] * scale), Image.NEAREST)
    d = ImageDraw.Draw(im)
    for (r, c), q in zip(cells, tiers):
        if q < 1:
            continue
        y0, x0 = (r * fc.CELL_PX - r1) * scale, (c * fc.CELL_PX - c1) * scale
        if y0 < 0 or x0 < 0 or y0 >= im.height or x0 >= im.width:
            continue
        col = (122, 0, 32) if q == 2 else (240, 110, 20)
        d.rectangle([x0, y0, x0 + fc.CELL_PX * scale - 1, y0 + fc.CELL_PX * scale - 1], outline=col, width=2)
    for lat, lon, ha in (radar_pts or []):
        y = (grid.north - lat) / grid.dlat - r1
        x = (lon - grid.west) / grid.dlon - c1
        rad = 3 + 2 * math.sqrt(ha)
        d.ellipse([x * scale - rad, y * scale - rad, x * scale + rad, y * scale + rad],
                  outline=(214, 39, 40), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)


def satellite_pictures(bounds, year, key, after_radar=None):
    """True-colour Sentinel-2 dry-season pictures before (Jan-Mar of `year`)
    and after (Jan-Mar of year+1), or a radar picture if that isn't possible yet."""
    w, s, e, n = bounds
    region = ee.Geometry.Rectangle([w, s, e, n], "EPSG:4326", False)
    def dry(y):
        # Jan-Mar, with gaps (too few cloud-free scenes) filled from Nov-Apr.
        return imagery.s2_dry_season_reference(region, f"{y}-01-01", f"{y}-03-31").unmask(
            imagery.s2_dry_season_reference(region, f"{y - 1}-11-01", f"{y}-04-30"))
    jobs = {"before": imagery.vis_s2(dry(year))}
    if after_radar:
        jobs["after"] = imagery.vis_s1(water.s1_composite(region, after_radar["window"], after_radar["orbit"]))
    else:
        jobs["after"] = imagery.vis_s2(dry(year + 1))
    out = {}
    for k, vis in jobs.items():
        path = OUT_DIR / f"{key}_{k}.png"
        if not path.exists():
            imagery.download_png(vis, bounds, path, dimensions=420)
        out[k] = f"data/trackrecord/{key}_{k}.png"
    return out


def radar_patches(site, lat, lon, radius):
    """Bank-erosion patches our radar detector found near a point in 2026."""
    res = json.loads((config.WEB_DATA_DIR / "results.json").read_text(encoding="utf8"))["results"]
    out = {}
    for run in config.RUNS:
        r = res.get(f"{run['id']}/{site}")
        if not r or r.get("error"):
            continue
        pts = []
        for f in r["patches"]["features"]:
            if f["properties"]["kind"] != "bank_erosion":
                continue
            plon, plat = f["geometry"]["coordinates"]
            if dist_m(plat, plon, lat, lon) <= radius:
                pts.append((plat, plon, f["properties"]["area_ha"]))
        out[run["id"]] = {"label": run["label"], "dates": [r["scenes"]["before_dates"][-1], r["scenes"]["after_dates"][-1]],
                          "orbit": r["scenes"]["orbit"], "window": run["after"], "points": pts,
                          "ha": round(sum(p[2] for p in pts), 1)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=os.environ.get(config.EE_PROJECT_ENV))
    args = ap.parse_args()
    if not args.project:
        ap.error(f"give --project or set {config.EE_PROJECT_ENV}")
    init_ee(args.project)

    grid = fc.reach_grid(fc.PILOT_REACH)
    pr = dict(np.load(CACHE / "predictions.npz"))
    pilot = pr["reach"] == list(fc.REACHES).index(fc.PILOT_REACH)
    year, row, col, p, loss, tier = (pr[k][pilot] for k in ("year", "row", "col", "p", "loss_ha", "tier"))
    pct = percentiles(year, p)
    flag = tier >= 1
    lat = np.array([grid.north - (r * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlat for r in row])
    lon = np.array([grid.west + (c * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlon for c in col])
    unions = json.loads((config.ROOT / "data" / "boundaries" / "unions_hdx.json").read_text(encoding="utf8"))["unions"]
    site_of = {"Kazipur": "kazipur", "Chauhali": "chauhali"}

    # ---- News events ----------------------------------------------------------
    print(">> News-reported events")
    events = []
    for ev in config.NEWS_EVENTS:
        y = int(ev["date"][:4])
        sel = (year == y) & (dist_m(lat, lon, ev["lat"], ev["lon"]) <= config.EVENT_RADIUS_M)
        n = int(sel.sum())
        item = {**ev, "forecast_year": y, "cells": n}
        if n:
            flagged = flag[sel]
            item.update({"flagged_share": round(float(flagged.mean()), 3),
                         "flagged_cells": int(flagged.sum()),
                         "high_cells": int((tier[sel] == 2).sum()),
                         "riskiest_cell_top_pct": round(100 * float(pct[sel].min()), 1)})
        if n and not np.isnan(loss[sel]).all():
            lost = float(np.nansum(loss[sel]))
            caught = float(np.nansum(loss[sel][flag[sel]]))
            item["outcome"] = {"source": f"Landsat dry season {y} -> {y + 1}",
                               "lost_ha": round(lost, 1),
                               "caught_share": round(caught / lost, 3) if lost > 0 else None}
            if lost < 2:
                item["verdict"] = "no_big_loss"
            else:
                share = caught / lost
                item["verdict"] = "hit" if share >= 0.5 else "partial" if share >= 0.2 else "miss"
        site = site_of.get(ev["upazila"])
        radar = radar_patches(site, ev["lat"], ev["lon"], config.EVENT_RADIUS_M) if site and y == 2026 else {}
        if radar:
            item["radar"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("points", "orbit", "window")}
                             for k, v in radar.items()}
            pts = [q for v in radar.values() for q in v["points"]]
            total = sum(q[2] for q in pts)
            near_flagged = 0.0
            for plat, plon, ha in pts:
                d = dist_m(lat[year == y], lon[year == y], plat, plon)
                if d.size and d.min() <= 400 and flag[year == y][np.argmin(d)]:
                    near_flagged += ha
            item["outcome"] = {"source": "radar detector, monsoon 2026 (upper bound; can include flooding)",
                               "lost_ha": round(total, 1),
                               "caught_share": round(near_flagged / total, 3) if total else None}
            item["verdict"] = ("early_no_detection" if total < 1 else
                               "early_hit" if near_flagged / total >= 0.5 else "early_partial"
                               if near_flagged / total >= 0.2 else "early_miss")
        win, bounds = window(grid, ev["lat"], ev["lon"])
        cells_here = list(zip(row[year == y], col[year == y]))
        after_radar = None
        pts = None
        if radar:
            latest = radar.get(config.RUNS[-1]["id"]) or next(iter(radar.values()))
            after_radar = {"window": latest["window"], "orbit": latest["orbit"]}
            pts = [q for v in radar.values() for q in v["points"]]
        outcome_panel(grid, win, y, cells_here, tier[year == y], OUT_DIR / f"{ev['id']}_outcome.png", radar_pts=pts)
        item["images"] = satellite_pictures(bounds, y, ev["id"], after_radar)
        item["images"]["outcome"] = f"data/trackrecord/{ev['id']}_outcome.png"
        item["bounds"] = bounds
        events.append(item)
        o = item.get("outcome", {})
        print(f"   {ev['date']} {ev['union']:14s}: {item.get('flagged_cells', 0)}/{n} cells flagged; "
              f"{o.get('source', 'no outcome')}: {o.get('lost_ha')} ha lost, "
              f"{o.get('caught_share')} in flagged cells -> {item.get('verdict')}")

    # ---- Case studies: predicted and happened --------------------------------
    print(">> Case studies from years the forecast never trained on")
    known = ~np.isnan(loss)
    cand = []
    for i in np.nonzero(known & (tier == 2) & (loss >= 2))[0]:
        near = known & (year == year[i]) & (dist_m(lat, lon, lat[i], lon[i]) <= 2500)
        lost = float(np.nansum(loss[near]))
        caught = float(np.nansum(loss[near & flag]))
        cand.append((caught, lost, i))
    cand.sort(reverse=True)
    picked = []
    for caught, lost, i in cand:
        if len(picked) == CASES:
            break
        if all(dist_m(lat[i], lon[i], lat[j], lon[j]) > 10000 for _, _, j in picked) and \
                sum(year[j] == year[i] for _, _, j in picked) < 2:
            picked.append((caught, lost, i))
    # ...and one honest miss: the biggest loss the forecast didn't flag.
    miss = None
    for i in np.argsort(-np.where(known & ~flag, loss, 0)):
        near = known & (year == year[i]) & (dist_m(lat, lon, lat[i], lon[i]) <= 2500)
        lost = float(np.nansum(loss[near]))
        caught = float(np.nansum(loss[near & flag]))
        if lost > 0 and caught / lost < 0.2:
            miss = (caught, lost, i)
            break

    cases = []
    for kind, (caught, lost, i) in [("hit", c) for c in picked] + ([("miss", miss)] if miss else []):
        y = int(year[i])
        key = f"case_{kind}_{y}_{i}"
        win, bounds = window(grid, lat[i], lon[i], 3000)
        outcome_panel(grid, win, y, list(zip(row[year == y], col[year == y])), tier[year == y],
                      OUT_DIR / f"{key}_outcome.png")
        imgs = satellite_pictures(bounds, y, key)
        imgs["outcome"] = f"data/trackrecord/{key}_outcome.png"
        cases.append({"kind": kind, "year": y, "lat": round(float(lat[i]), 5), "lon": round(float(lon[i]), 5),
                      "place": nearest_union(unions, lat[i], lon[i]),
                      "lost_ha": round(lost, 1), "caught_share": round(caught / lost, 3) if lost else None,
                      "risk": round(float(p[i]), 3), "top_pct": round(100 * float(pct[i]), 1),
                      "images": imgs, "bounds": bounds})
        print(f"   {kind}: {cases[-1]['place']}, monsoon {y}: {lost:.0f} ha lost, "
              f"{caught / lost:.0%} of it in cells flagged beforehand")

    payload = {"events": events, "cases": cases, "radius_m": config.EVENT_RADIUS_M,
               "union_source": "OCHA / BBS union points (CC BY-IGO), data.humdata.org/dataset/cod-ab-bgd"}
    text = json.dumps(payload, default=float)
    (config.WEB_DATA_DIR / "trackrecord.json").write_text(text, encoding="utf8")
    (config.WEB_DATA_DIR / "trackrecord.js").write_text(f"window.TRACK = {text};\n", encoding="utf8")
    print("   wrote web/data/trackrecord.js")


if __name__ == "__main__":
    main()
