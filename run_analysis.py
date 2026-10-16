"""Run the erosion screening for every site x run in config and write the
results the dashboard reads (web/data/).

    python run_analysis.py --project <your-earth-engine-cloud-project>

Earth Engine needs a one-time interactive browser login first:

    earthengine authenticate
"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import ee
import numpy as np

import config
from detector import alert, assets, change, classifier, imagery, water

MAX_PATCHES_SHOWN = 150  # largest bank-erosion patches shown as clickable points
SITE_BUFFER_M = 1000          # banks move; catch change just outside the line


def init_ee(project):
    try:
        ee.Initialize(project=project)
        ee.data.setDeadline(300_000)  # ms; a hung request fails instead of waiting forever
    except Exception as exc:
        msg = str(exc)
        print("\nCould not start Earth Engine:\n  " + msg.splitlines()[0])
        if "credentials" in msg.lower() or "authenticate" in msg.lower():
            print("\nYou need to log in once, in a browser:\n"
                  "  .venv\\Scripts\\earthengine authenticate\n"
                  "then run this script again.")
        elif "project" in msg.lower():
            print("\nPass a Google Cloud project that is registered for Earth "
                  "Engine:\n  python run_analysis.py --project <project-id>\n"
                  "(register one at https://code.earthengine.google.com/register)")
        sys.exit(1)


def load_site(site):
    fc = json.loads(site["boundary"].read_text(encoding="utf8"))
    upazila = ee.Geometry(fc["features"][0]["geometry"])
    return fc, upazila.buffer(SITE_BUFFER_M, 50)


def bounds_of(geom):
    ring = geom.bounds(1).coordinates().get(0).getInfo()
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    # Rounded (~10 cm): EE returns last-digit float jitter between calls, which
    # would otherwise change the pixel grid and the saved-map cache key.
    return [round(v, 6) for v in (min(lons), min(lats), max(lons), max(lats))]


def prepare(site, run):
    """Site geometry, pixel grid and chosen radar scenes for one site x run."""
    boundary_fc, region = load_site(site)
    grid = change.Grid(bounds_of(region))
    scenes = water.pick_orbit(region, run["before"], run["after"])
    return {"boundary_fc": boundary_fc, "region": region, "grid": grid, "scenes": scenes}


def recently_water(grid, n_years):
    """Pixels (on the 10 m site grid) that were open water in any of the last
    `n_years` Landsat dry-season maps (cached by run_forecast.py). Pixels
    outside those maps count as recently water, i.e. not established land."""
    from detector import forecast as fc
    rg = fc.reach_grid(fc.PILOT_REACH)
    cache = config.ROOT / "data" / "forecast" / fc.PILOT_REACH
    wet = np.zeros((rg.height, rg.width), bool)
    for y in range(fc.LAST_YEAR - n_years + 1, fc.LAST_YEAR + 1):
        d = np.load(cache / f"dry_{y}.npz")
        wet |= fc.clean_water(d["water"], d["ndvi"]) == 1
    lat = grid.north - (np.arange(grid.height) + 0.5) * grid.dlat
    lon = grid.west + (np.arange(grid.width) + 0.5) * grid.dlon
    r = ((rg.north - lat) / rg.dlat).astype(int)
    c = ((lon - rg.west) / rg.dlon).astype(int)
    ok = (r[:, None] >= 0) & (r[:, None] < rg.height) & (c[None, :] >= 0) & (c[None, :] < rg.width)
    out = wet[np.clip(r, 0, rg.height - 1)[:, None], np.clip(c, 0, rg.width - 1)[None, :]]
    return out | ~ok


def ai_probabilities(rf, region, run, scenes):
    """Two-band image: packed AI class probabilities, before and after."""
    return ee.Image.cat([
        classifier.probabilities_packed(
            rf, water.s1_composite(region, run[w], scenes["orbit"], smooth=False), region
        ).rename(f"prob_{w}")
        for w in ("before", "after")]).clip(region)


def analyse(site_id, site, run, out_dir, rf=None, prep=None, prob_asset=None):
    print(f"\n>> {site['name']} | {run['label']}")
    prep = prep or prepare(site, run)
    boundary_fc, region, grid, scenes = (prep[k] for k in ("boundary_fc", "region", "grid", "scenes"))
    bounds = grid.bounds
    img_dir = out_dir / run["id"] / site_id
    rel = f"data/{run['id']}/{site_id}"

    print(f"   Sentinel-1 orbit {scenes['orbit']} ({scenes['pass'].lower()}): "
          f"before {scenes['before_dates']}, after {scenes['after_dates']}")

    s1_before = water.s1_composite(region, run["before"], scenes["orbit"])
    s1_after = water.s1_composite(region, run["after"], scenes["orbit"])
    # The threshold is always computed: it is the AI's baseline, and the
    # fallback method.
    t_before, src_b = water.water_threshold(s1_before, region)
    t_after, src_a = water.water_threshold(s1_after, region)
    print(f"   water threshold: before {t_before} dB ({src_b}), after {t_after} dB ({src_a})")

    # Rasters to download, each in its own request: a whole site classified
    # at 10 m takes 1-2 minutes, and together they'd exceed EE's time limit.
    def byte(img):
        return img.unmask(0).clip(region).unmask(0).toByte().rename("v")
    est = config.ESTABLISHED_LAND
    s2dry = imagery.s2_dry_season_reference(region)
    to_fetch = {
        "inside": byte(ee.Image(1)),
        # vegetated, not-wet land in the Jan-Mar 2026 dry season
        "veg_dry": byte(s2dry.normalizedDifference(["B8", "B4"]).gte(est["min_ndvi"])
                        .And(s2dry.normalizedDifference(["B3", "B11"]).lt(0))),
        # bit 1 = water before, bit 2 = water after (plain threshold)
        "thr": byte(water.water_mask(s1_before, t_before)
                    .add(water.water_mask(s1_after, t_after).multiply(2))),
    }
    if rf is not None:
        # Saved AI map if there is one (fast), otherwise compute it now (1-2 min).
        probs = ee.Image(prob_asset) if prob_asset else ai_probabilities(rf, region, run, scenes)
        for w in ("before", "after"):
            to_fetch[f"prob_{w}"] = byte(probs.select(f"prob_{w}"))
    print(f"   downloading {len(to_fetch)} rasters ({grid.width} x {grid.height} px) ...")
    with ThreadPoolExecutor(len(to_fetch)) as pool:
        futures = {k: pool.submit(imagery.fetch_array, img, grid) for k, img in to_fetch.items()}
        arrays = {k: f.result()["v"] for k, f in futures.items()}

    inside = arrays["inside"] == 1
    thr_b, thr_a = (arrays["thr"] & 1) > 0, (arrays["thr"] & 2) > 0
    confident = {}
    if rf is not None:
        pb = classifier.unpack_probabilities(arrays["prob_before"])
        pa = classifier.unpack_probabilities(arrays["prob_after"])
        cls_b, cls_a = np.argmax(pb, axis=0), np.argmax(pa, axis=0)  # 0 water, 1 sand, 2 land
        w_before, w_after, method = cls_b == 0, cls_a == 0, "ai"
        # Only count change the AI is confident about on both dates.
        c = config.CHANGE_CONFIDENCE
        confident = {"new_water": (pb[0] <= c) & (pa[0] >= 1 - c),
                     "new_land": (pb[0] >= 1 - c) & (pa[0] <= c)}
    else:
        w_before, w_after, method = thr_b, thr_a, "threshold"

    established = (arrays["veg_dry"] == 1) & ~recently_water(grid, est["dry_years"])
    stats, patches, classes = change.analyse(w_before, w_after, inside, grid, established=established,
                                             **confident)
    # Cross-check: raw land->water area by the plain threshold method.
    stats["threshold_land_to_water_ha"] = round(
        float((~thr_b & thr_a & inside).sum()) * grid.px_area_m2 / 1e4, 2)
    print(f"   bank erosion {stats['bank_erosion_ha']} ha | accretion {stats['accretion_ha']} ha "
          f"| isolated new water {stats['isolated_new_water_ha']} ha "
          f"| eroding shoreline ~{stats['eroding_edge_km']} km")
    if stats["review_flags"]:
        print(f"   {stats['review_flags']} patch(es) flagged 'possible extraction' - "
              f"HEURISTIC, needs human review, not a confirmed finding")

    # Clickable points for the map: the biggest erosion patches plus every review flag.
    erosion = sorted((p for p in patches if p["kind"] == "bank_erosion"),
                     key=lambda p: -p["area_ha"])[:MAX_PATCHES_SHOWN]
    flagged = [p for p in patches if p["review_flag_possible_extraction"]]
    features = [{"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                 "properties": {k: v for k, v in p.items() if k not in ("lon", "lat")}}
                for p in erosion + flagged]

    # Picture layers.
    print("   rendering images ...")
    layers_out = {}

    def record(key, label):
        layers_out[key] = {"url": f"{rel}/{key}.png", "label": label}

    def save_ee(key, vis_img, label):
        imagery.download_png(vis_img, bounds, img_dir / f"{key}.png")
        record(key, label)

    def save_local(key, arr, colours, label):
        imagery.save_classes_png(arr, colours, img_dir / f"{key}.png", mask=inside)
        record(key, label)

    save_local("change", classes, imagery.CHANGE_COLOURS, "Computed change")
    blue = {1: (43, 131, 255)}
    save_local("water_before", w_before.astype("uint8"), blue, "Water, before")
    save_local("water_after", w_after.astype("uint8"), blue, "Water, after")
    if method == "ai":
        save_local("classes_before", cls_b, imagery.CLASS_COLOURS, "AI class map, before")
        save_local("classes_after", cls_a, imagery.CLASS_COLOURS, "AI class map, after")
    save_ee("s1_before", imagery.vis_s1(s1_before),
            f"Radar, before ({', '.join(scenes['before_dates'])})")
    save_ee("s1_after", imagery.vis_s1(s1_after),
            f"Radar, after ({', '.join(scenes['after_dates'])})")
    save_ee("s2_dry", imagery.vis_s2(imagery.s2_dry_season_reference(region)),
            "Optical, dry season Jan-Mar 2026 (Sentinel-2)")
    s2_meta = {}
    for when in ("before", "after"):
        s2 = imagery.s2_clearest(region, run[when], side=when)
        if s2:
            save_ee(f"s2_{when}", imagery.vis_s2(s2["image"]),
                    f"Optical, {when} ({s2['date']}, {s2['cloud_pct']}% cloud)")
            s2_meta[when] = {"date": s2["date"], "cloud_pct": s2["cloud_pct"]}
        else:
            s2_meta[when] = None
            print(f"   no Sentinel-2 image under {imagery.MAX_S2_CLOUD_PCT}% cloud near the "
                  f"'{when}' window (normal in monsoon)")

    result = {
        "site": site_id, "site_name": site["name"], "run": run["id"], "method": method,
        "bounds": bounds, "boundary": boundary_fc,
        "scenes": scenes, "s2": s2_meta,
        "thresholds_db": {"before": t_before, "before_method": src_b,
                          "after": t_after, "after_method": src_a},
        "stats": stats, "patches": {"type": "FeatureCollection", "features": features},
        "layers": layers_out,
    }
    result["alert"] = alert.build_alert(site["name"], run, stats, scenes)
    if result["alert"]:
        alert.print_alert(result["alert"])
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get(config.EE_PROJECT_ENV),
                    help="Google Cloud project registered for Earth Engine")
    ap.add_argument("--method", choices=["ai", "threshold"], default="ai",
                    help="water mapping: AI classifier (default) or plain threshold")
    ap.add_argument("--no-storage", action="store_true",
                    help="don't save/re-use AI maps in Earth Engine asset storage")
    ap.add_argument("--run", action="append", help="only these run ids (default: all)")
    ap.add_argument("--site", action="append", help="only these site ids (default: all)")
    args = ap.parse_args()
    if not args.project:
        ap.error(f"give --project or set {config.EE_PROJECT_ENV}")

    init_ee(args.project)
    out = config.WEB_DATA_DIR
    out.mkdir(parents=True, exist_ok=True)

    results_path = out / "results.json"
    previous = json.loads(results_path.read_text(encoding="utf8")) if results_path.exists() else {}
    results = previous.get("results", {})

    rf, model_report = None, None
    if args.method == "ai":
        print("\n>> Training the AI water/sand/land classifier")
        regions = [load_site(s)[1] for s in config.SITES.values()]
        union = regions[0]
        for g in regions[1:]:
            union = union.union(g, 50)
        try:
            rf, model_report = classifier.train(union)
        except Exception as exc:
            # Fail loudly rather than quietly produce non-AI results.
            sys.exit(f"   !! AI training failed: {exc}\n"
                     f"   Fix it, or run with --method threshold for the non-AI baseline.")

    runs = [r for r in config.RUNS if not args.run or r["id"] in args.run]
    sites = {k: v for k, v in config.SITES.items() if not args.site or k in args.site}

    jobs = {(run["id"], site_id): (run, site_id, site) for run in runs for site_id, site in sites.items()}
    preps, prob_assets = {}, {}
    if rf is not None and not args.no_storage:
        if assets.available(args.project):
            print("\n>> Checking saved AI maps in Earth Engine storage")
            export_jobs = []
            for key, (run, site_id, site) in jobs.items():
                prep = preps[key] = prepare(site, run)
                sc = prep["scenes"]
                # Rebuild the saved map if the model, the scenes or the grid change.
                cache_key = hashlib.sha1(json.dumps([
                    model_report["model_key"], sc["orbit"], sc["before_dates"], sc["after_dates"],
                    prep["grid"].bounds, config.SPECKLE_RADIUS_M]).encode()).hexdigest()[:16]
                aid = assets.asset_id(args.project, run["id"], site_id)
                export_jobs.append({"aid": aid, "grid": prep["grid"], "key": cache_key,
                                    "name": f"{run['id']}_{site_id}",
                                    "image": ai_probabilities(rf, prep["region"], run, sc)})
                prob_assets[key] = aid
            try:
                assets.ensure(export_jobs)
            except Exception as exc:
                print(f"   !! saving AI maps failed ({exc}); computing them directly instead")
                prob_assets = {}
        else:
            print("\n   (no Earth Engine asset storage on this project; computing AI maps directly)")

    for key, (run, site_id, site) in jobs.items():
        try:
            results[f"{run['id']}/{site_id}"] = analyse(
                site_id, site, run, out, rf, preps.get(key), prob_assets.get(key))
        except Exception as exc:
            print(f"   !! failed: {exc}")
            results[f"{run['id']}/{site_id}"] = {"site": site_id, "run": run["id"],
                                                 "site_name": site["name"], "error": str(exc)}

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "runs": config.RUNS,
        "sites": {k: v["name"] for k, v in config.SITES.items()},
        "reference_events": config.REFERENCE_EVENTS,
        "alert_threshold_ha": config.ALERT_THRESHOLD_HA,
        "mining_heuristic": config.MINING_HEURISTIC,
        "model": model_report,
        "results": results,
    }
    text = json.dumps(payload, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    results_path.write_text(text, encoding="utf8")
    # Also as a script, so the dashboard works when opened straight from disk.
    (out / "results.js").write_text(f"window.RESULTS = {text};\n", encoding="utf8")
    print(f"\nWrote {results_path} - open web/index.html to view.")


if __name__ == "__main__":
    main()
