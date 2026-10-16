"""Predicted vs actual: side-by-side pictures for the dashboard's #compare view.

For every place the track record scored as a hit (news events and case
studies from the test years), draws two transparent overlays on the same
bounds as the track-record satellite pictures:

    high/watch the forecast's High alert and Watch squares, issued in March
    actual     established land that was river by the next dry season

    python run_trackrecord.py --project <id>   # first (writes trackrecord.json)
    python run_showcase.py [--project <id>]

Overlays come from the cached Landsat maps only. With --project it also
fetches sharper (1200 px) Sentinel-2 pictures to put underneath; without it
the dashboard falls back to the 420 px track-record pictures.
"""
import argparse
import json
import os

import numpy as np
from PIL import Image, ImageDraw

import config
from detector import forecast as fc
from run_trackrecord import CACHE, WINDOW_M, dist_m, load_water, window

OUT_DIR = config.WEB_DATA_DIR / "showcase"
SCALE = 4
HIGH, WATCH, LOST = (226, 38, 28), (255, 170, 30), (255, 36, 36)


def overlays(grid, win, year, cells, tiers, key):
    (r1, r2, c1, c2) = win
    h, w = (r2 - r1) * SCALE, (c2 - c1) * SCALE
    # One picture per tier, so the dashboard can show High alert on its own.
    pics = {}
    for name, want, col in (("watch", 1, WATCH), ("high", 2, HIGH)):
        pics[name] = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(pics[name])
        for (r, c), q in zip(cells, tiers):
            if q != want:
                continue
            y0, x0 = (r * fc.CELL_PX - r1) * SCALE, (c * fc.CELL_PX - c1) * SCALE
            if y0 < 0 or x0 < 0 or y0 >= h or x0 >= w:
                continue
            d.rectangle([x0, y0, x0 + fc.CELL_PX * SCALE - 1, y0 + fc.CELL_PX * SCALE - 1],
                        fill=col + (90,), outline=col + (255,), width=2)
    wb, nb = load_water(year)
    wa, _ = load_water(year + 1)
    wb, nb, wa = wb[r1:r2, c1:c2], nb[r1:r2, c1:c2], wa[r1:r2, c1:c2]
    lost = (wb == 0) & (nb >= 100 + 100 * fc.MIN_NDVI) & (nb != fc.NODATA) & (wa == 1)
    act = np.zeros(lost.shape + (4,), np.uint8)
    act[lost] = LOST + (235,)
    act = Image.fromarray(act, "RGBA").resize((w, h), Image.NEAREST)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, im in pics.items():
        im.save(OUT_DIR / f"{key}_{name}.png", optimize=True)
    (OUT_DIR / f"{key}_predicted.png").unlink(missing_ok=True)      # older single-picture output
    act.save(OUT_DIR / f"{key}_actual.png", optimize=True)
    return {"high": f"data/showcase/{key}_high.png", "watch": f"data/showcase/{key}_watch.png",
            "actual": f"data/showcase/{key}_actual.png",
            "lost_ha_window": round(float(lost.sum()) * fc.PIXEL_M ** 2 / 1e4, 1)}


def tier_stats(sel, tier, loss, min_loss):
    """Inside the circle: per tier, how many squares, how many really lost land
    (min_loss ha or more, the forecast's own target) and their share of all loss."""
    lost = float(np.nansum(loss[sel]))
    out = {"cells": int(sel.sum()), "lost_ha": round(lost, 1)}
    for name, q in (("high", 2), ("watch", 1), ("none", 0)):
        m = sel & (tier == q)
        out[name] = {"n": int(m.sum()), "eroded": int((loss[m] >= min_loss).sum()),
                     "loss_share": round(float(np.nansum(loss[m])) / lost, 3) if lost else None}
    return out


def sharp_pictures(bounds, year, key):
    from detector import imagery
    import ee
    w, s, e, n = bounds
    region = ee.Geometry.Rectangle([w, s, e, n], "EPSG:4326", False)
    def dry(y):
        return imagery.s2_dry_season_reference(region, f"{y}-01-01", f"{y}-03-31").unmask(
            imagery.s2_dry_season_reference(region, f"{y - 1}-11-01", f"{y}-04-30"))
    out = {}
    for k, y in (("before", year), ("after", year + 1)):
        path = OUT_DIR / f"{key}_{k}.png"
        if not path.exists():
            imagery.download_png(imagery.vis_s2(dry(y)), bounds, path, dimensions=1200)
        out[k] = f"data/showcase/{key}_{k}.png"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=os.environ.get(config.EE_PROJECT_ENV))
    args = ap.parse_args()
    if args.project:
        from run_analysis import init_ee
        init_ee(args.project)

    track = json.loads((config.WEB_DATA_DIR / "trackrecord.json").read_text(encoding="utf8"))
    grid = fc.reach_grid(fc.PILOT_REACH)
    pr = dict(np.load(CACHE / "predictions.npz"))
    pilot = pr["reach"] == list(fc.REACHES).index(fc.PILOT_REACH)
    year, row, col, tier, loss = (pr[k][pilot] for k in ("year", "row", "col", "tier", "loss_ha"))
    min_loss = json.loads((CACHE / "report.json").read_text(encoding="utf8"))["protocol"]["min_loss_ha"]
    lat = np.array([grid.north - (r * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlat for r in row])
    lon = np.array([grid.west + (c * fc.CELL_PX + fc.CELL_PX / 2) * grid.dlon for c in col])

    places = [("event", e["id"], e, WINDOW_M) for e in track["events"] if e.get("verdict") == "hit"]
    places += [("case", f"case_{c['year']}_{i}", c, 3000) for i, c in enumerate(track["cases"]) if c["kind"] == "hit"]
    items = []
    for kind, key, src, half in places:
        y = src.get("forecast_year", src.get("year"))
        win, bounds = window(grid, src["lat"], src["lon"], half)
        if not np.allclose(bounds, src["bounds"]):
            print(f"   {key}: skipped, its window differs from the track-record pictures")
            continue
        sel = year == y
        item = {"id": key, "kind": kind, "year": y, "lat": src["lat"], "lon": src["lon"], "bounds": bounds,
                "high_cells": int((tier[sel] == 2).sum()), "images": overlays(grid, win, y, list(zip(row[sel], col[sel])), tier[sel], key)}
        item["circle"] = tier_stats(sel & (dist_m(lat, lon, src["lat"], src["lon"]) <= track["radius_m"]), tier, loss, min_loss)
        item["images"].update({"before": src["images"]["before"], "after": src["images"]["after"]})
        if args.project:
            item["images"].update(sharp_pictures(bounds, y, key))
        if kind == "event":
            item.update(place=f"{src['union']} union, {src['upazila']}", date=src["date"], what=src["what"],
                        sources=src.get("sources", []), lost_ha=src["outcome"]["lost_ha"],
                        caught_share=src["outcome"]["caught_share"], flagged_share=src["flagged_share"],
                        flagged_cells=src["flagged_cells"], high_in_radius=src["high_cells"], cells=src["cells"])
        else:
            item.update(place=src["place"], lost_ha=src["lost_ha"], caught_share=src["caught_share"], risk=src["risk"])
        items.append(item)
        print(f"   {key}: {item['place']}, {y}: {item['images']['lost_ha_window']} ha lost in the picture")

    # The headline place: the hit with the most land lost among news-reported events.
    ev = [i for i in items if i["kind"] == "event"]
    featured = max(ev, key=lambda i: i["lost_ha"])["id"] if ev else (items[0]["id"] if items else None)
    payload = {"featured": featured, "radius_m": track["radius_m"], "min_loss_ha": min_loss, "places": items}
    text = json.dumps(payload, default=float)
    (config.WEB_DATA_DIR / "showcase.js").write_text(f"window.SHOWCASE = {text};\n", encoding="utf8")
    print(f"   featured: {featured}; wrote web/data/showcase.js")


if __name__ == "__main__":
    main()
