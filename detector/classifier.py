"""AI component: a Random Forest that reads Sentinel-1 radar as water / sand / land.

One sentence: we taught the computer to read radar by showing it days when a
camera could also see clearly.

How the training labels are made (no hand-labelling was available):
  * On clear days, Sentinel-2 optical images can see the ground.
  * A pixel gets a label only if two methods agree: Google's Dynamic World
    (a published deep-learning land-cover model) says water / not-water, and
    standard indices from the same image (MNDWI for water, NDVI for
    vegetation) say water, bare sand, or vegetated land.
  * Where a Sentinel-1 radar pass happened within a day of such an image, we
    pair them: radar features in, label out.
  * The Random Forest learns that mapping. It can then label radar images on
    cloudy monsoon days, when there's no optical image at all.

Honesty notes (also shown in the dashboard):
  * The labels come from optical rules plus another model, so they are
    not ground truth; their mistakes carry over.
  * Clear days are mostly dry-season days, so test accuracy may flatter
    monsoon performance. Test dates are listed so this can be judged.
  * Accuracy is measured on whole DATES held out from training, not random
    pixels from the same scenes (which would overstate accuracy).
"""
import datetime as dt
import hashlib
import json

import ee

import config
from detector import water

DW = "GOOGLE/DYNAMICWORLD/V1"
SAMPLE_CACHE = config.ROOT / "data" / "training_samples.json"
CLASSES = ["water", "sand", "land"]  # 0, 1, 2
FEATURES = ["VV", "VH", "VV_minus_VH", "VV_texture", "angle",
            "VV_rel", "VV_ctx", "VV_ctx_rel", "VV_above_dark", "scene_otsu", "scene_p10"]
# What the model actually uses. Viewing angle is sampled but NOT used: in a
# first version the model leaned on it most, learning which orbit geometry
# the training scenes had rather than what water looks like, and it failed
# on images from a different orbit.
# The "_rel" features measure each pixel against its own image: radar
# brightness drifts from date to date (wind, moisture, calibration), and a
# model that only sees absolute dB learns the training dates' cut-off, which
# then fails on new dates. On held-out dates, that version scored below a
# plain fixed threshold.
MODEL_FEATURES = ["VV_rel", "VV_ctx_rel", "VV_above_dark", "VH", "VV_minus_VH", "VV_texture"]
CONTEXT_RADIUS_M = 100
MAX_TRAIN_PER_CLASS_PER_DATE = 120   # x 3 classes x ~70 dates x ~254 bytes = ~6 MB
TEST_CHUNK = 4000             # held-out rows scored per Earth Engine request (~1 MB)
# Index rules for training labels (see _labels). Deliberately strict: unclear
# pixels are left out rather than guessed.
LABEL_RULES = {"water_mndwi_min": 0.1, "sand_ndvi_max": 0.2, "sand_mndwi_max": -0.1,
               "land_ndvi_min": 0.45}


def features(s1_img, region):
    """Radar features the model looks at. `s1_img` has VV, VH, angle (dB).

    `region` is where the image's own statistics (its Otsu water/land
    cut-off and how dark its darkest 10 % is) are measured.
    """
    vv = s1_img.select("VV").focalMedian(config.SPECKLE_RADIUS_M, "circle", "meters")
    vh = s1_img.select("VH").focalMedian(config.SPECKLE_RADIUS_M, "circle", "meters")
    texture = s1_img.select("VV").reduceNeighborhood(
        ee.Reducer.stdDev(), ee.Kernel.square(2)).rename("VV_texture")
    ctx = vv.focalMean(CONTEXT_RADIUS_M, "circle", "meters")
    otsu, p10 = water.scene_stats(vv, region)
    return ee.Image.cat([
        vv, vh, vv.subtract(vh).rename("VV_minus_VH"), texture, s1_img.select("angle"),
        vv.subtract(otsu).rename("VV_rel"), ctx.rename("VV_ctx"),
        ctx.subtract(otsu).rename("VV_ctx_rel"), vv.subtract(p10).rename("VV_above_dark"),
        ee.Image.constant(otsu).rename("scene_otsu"), ee.Image.constant(p10).rename("scene_p10"),
    ]).rename(FEATURES)


def _dates(coll):
    ms = coll.aggregate_array("system:time_start").getInfo()
    return sorted({dt.datetime.fromtimestamp(m / 1000, dt.timezone.utc).date() for m in ms})


def find_pairs(region, start, end, max_pairs, max_gap_days=1):
    """Dates where Dynamic World (clear optical) and Sentinel-1 radar nearly coincide."""
    dw_dates = _dates(ee.ImageCollection(DW).filterBounds(region).filterDate(start, end))
    s1_dates = _dates(water.s1_collection(region, (start, end)))
    # Never learn from dates inside an analysis window: the model must not
    # have seen the periods it is later used to measure.
    blocked = [(dt.date.fromisoformat(r[w][0]) - dt.timedelta(days=max_gap_days),
                dt.date.fromisoformat(r[w][1]) + dt.timedelta(days=max_gap_days))
               for r in config.RUNS for w in ("before", "after")]
    dw_dates = [d for d in dw_dates if not any(a <= d <= b for a, b in blocked)]
    pairs = []
    for d in dw_dates:
        near = [s for s in s1_dates if abs((s - d).days) <= max_gap_days]
        if near:
            pairs.append((d, min(near, key=lambda s: abs((s - d).days))))
    if len(pairs) > max_pairs:  # spread evenly across the whole period
        step = len(pairs) / max_pairs
        pairs = [pairs[int(i * step)] for i in range(max_pairs)]
    return pairs


def _labels(region, date):
    """Training labels for one clear day: kept only where two methods agree.

    1. Dynamic World's label (a deep-learning model), used only for
       water vs not-water. Its finer land classes are unreliable here: it
       calls most bright Jamuna sandbars "snow and ice".
    2. Standard spectral indices from the same Sentinel-2 image:
       MNDWI (water index) and NDVI (vegetation index).
    Pixels where the two disagree, or that are cloudy, get no label.
    """
    t = LABEL_RULES
    end = (date + dt.timedelta(days=1)).isoformat()
    dw = ee.ImageCollection(DW).filterBounds(region).filterDate(date.isoformat(), end).mosaic()
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filterDate(
        date.isoformat(), end).mosaic()
    scl = s2.select("SCL")
    clear = scl.neq(3).And(scl.lt(8).Or(scl.gt(10))).And(scl.neq(1))
    ndvi = s2.normalizedDifference(["B8", "B4"])
    mndwi = s2.normalizedDifference(["B3", "B11"])
    dw_water = dw.select("label").eq(0)

    water = dw_water.And(mndwi.gt(t["water_mndwi_min"]))
    sand = dw_water.Not().And(ndvi.lt(t["sand_ndvi_max"])).And(mndwi.lt(t["sand_mndwi_max"]))
    land = dw_water.Not().And(ndvi.gt(t["land_ndvi_min"]))
    cls = (ee.Image(-1).where(land, 2).where(sand, 1).where(water, 0)
           .rename("class").toInt())
    return cls.updateMask(cls.gte(0)).updateMask(clear).updateMask(dw.select("label").mask())


def _radar(region, date):
    return (water.s1_collection(region, (date.isoformat(), date.isoformat()))
            .select(["VV", "VH", "angle"]).mosaic())


def _sample(region, pair, idx):
    dw_date, s1_date = pair
    img = features(_radar(region, s1_date), region).addBands(_labels(region, dw_date))
    return img.stratifiedSample(
        numPoints=config.CLASSIFIER["points_per_class_per_date"], classBand="class",
        region=region, scale=config.ANALYSIS_SCALE_M, seed=idx, tileScale=4,
        geometries=False).map(lambda f: f.set("pair", idx))


def _metrics(truth, predicted, n):
    """Accuracy numbers from a confusion matrix (rows = truth, columns = predicted)."""
    m = [[0] * n for _ in range(n)]
    for a, b in zip(truth, predicted):
        m[a][b] += 1
    total = sum(map(sum, m)) or 1
    agree = sum(m[i][i] for i in range(n)) / total
    chance = sum(sum(m[i]) * sum(r[i] for r in m) for i in range(n)) / total ** 2
    return {
        "overall_accuracy": round(agree, 3),
        "kappa": round((agree - chance) / (1 - chance), 3) if chance < 1 else None,
        # share of each true class that was found / share of each predicted class that was right
        "producers_accuracy": [round(m[i][i] / max(sum(m[i]), 1), 3) for i in range(n)],
        "consumers_accuracy": [round(m[i][i] / max(sum(r[i] for r in m), 1), 3) for i in range(n)],
        "matrix": m,
    }


def train(region):
    """Train the Random Forest and test it on held-out dates.

    Returns (classifier, report). `report` is plain JSON for the dashboard.
    """
    c = config.CLASSIFIER
    pairs = find_pairs(region, *c["training_period"], max_pairs=c["max_training_dates"])
    if len(pairs) < 4:
        raise RuntimeError(f"Only {len(pairs)} clear-optical/radar date pairs found; need at least 4.")
    test_idx = {i for i in range(len(pairs)) if i % c["test_every_nth_date"] == c["test_every_nth_date"] - 1}
    print(f"   {len(pairs)} date pairs ({len(pairs) - len(test_idx)} train, {len(test_idx)} test)")

    # Download each date's samples in its own request: asking Earth Engine for
    # all dates at once hits its "too many concurrent aggregations" limit.
    # The samples are small (a few thousand rows of 6 numbers per date).
    # Samples are cached on disk per date + settings, so re-runs skip the download.
    key = json.dumps([c["points_per_class_per_date"], LABEL_RULES, FEATURES,
                      config.SPECKLE_RADIUS_M], sort_keys=True)
    cache = json.loads(SAMPLE_CACHE.read_text()) if SAMPLE_CACHE.exists() else {}
    if cache.get("key") != key:
        cache = {"key": key, "dates": {}}
    rows, used = {"train": [], "test": []}, set()
    for i, p in enumerate(pairs):
        tag = f"{p[0]}|{p[1]}|{i}"
        if tag not in cache["dates"]:
            try:
                feats = _sample(region, p, i).getInfo()["features"]
            except Exception as exc:
                print(f"     skip {p[0]}: {exc}")
                continue
            cache["dates"][tag] = [f["properties"] for f in feats]
            SAMPLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SAMPLE_CACHE.write_text(json.dumps(cache))
        if not cache["dates"][tag]:
            print(f"     skip {p[0]}: no usable labels (cloud)")
            continue
        split = "test" if i in test_idx else "train"
        rows[split] += cache["dates"][tag]
        used.add(i)
        print(f"     {p[0]}: {len(cache['dates'][tag])} samples ({split})")
    pairs_used = {i: p for i, p in enumerate(pairs) if i in used}
    if not rows["test"] or len(rows["train"]) < 500:
        raise RuntimeError("Not enough training/test samples after sampling.")

    # Earth Engine takes at most 10 MB per request, and the training rows
    # travel with every request that uses the model. So send only the columns
    # the model and the baseline need, rounded, and at most
    # MAX_TRAIN_PER_CLASS_PER_DATE training rows per class per date: more
    # DATES (different water levels, wind, seasons) matter more than more
    # pixels from the same date. (Tested locally: 250 instead of 400 per class
    # changed held-out accuracy by less than 0.5 %.)
    keep = MODEL_FEATURES + ["VV", "class"]

    def slim(props, cap=None):
        seen, out = {}, []
        for r in props:
            if any(r.get(k) is None for k in keep):
                continue
            key = (r["pair"], r["class"])
            seen[key] = seen.get(key, 0) + 1
            if cap is None or seen[key] <= cap:
                out.append({k: round(r[k], 3) if isinstance(r[k], float) else r[k] for k in keep})
        return out

    def to_fc(props):
        return ee.FeatureCollection([ee.Feature(None, r) for r in props])
    train_rows = slim(rows["train"], MAX_TRAIN_PER_CLASS_PER_DATE)
    test_rows = slim(rows["test"])
    train_fc = to_fc(train_rows)

    rf = (ee.Classifier.smileRandomForest(numberOfTrees=c["trees"], minLeafPopulation=5, seed=42)
          .train(train_fc, "class", MODEL_FEATURES))

    # Score the held-out dates in chunks (each request also carries the
    # training rows), then count the confusion matrices locally.
    predicted = []
    for i in range(0, len(test_rows), TEST_CHUNK):
        chunk = to_fc(test_rows[i:i + TEST_CHUNK]).classify(rf)
        predicted += chunk.aggregate_array("classification").getInfo()
    truth = [r["class"] for r in test_rows]
    # Baseline for comparison: the plain dark-pixel threshold, water vs not-water.
    t = config.FALLBACK_THRESHOLD_DB
    is_water = [int(k == 0) for k in truth]
    report = {
        "rf_3class": _metrics(truth, predicted, 3),
        "rf_water_vs_not": _metrics(is_water, [int(k == 0) for k in predicted], 2),
        "threshold_water_vs_not": _metrics(is_water, [int(r["VV"] < t) for r in test_rows], 2),
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "importance": ee.Dictionary(rf.explain()).get("importance").getInfo(),
    }
    total = sum(report["importance"].values()) or 1
    report["importance"] = {k: round(v / total, 3) for k, v in
                            sorted(report["importance"].items(), key=lambda kv: -kv[1])}
    model_key = hashlib.sha1(json.dumps(
        [key, c["trees"], sorted(str(p) for p in pairs_used.values()), sorted(test_idx)]
    ).encode()).hexdigest()[:12]
    report.update({
        "model_key": model_key,
        "classes": CLASSES,
        "model": f"Random Forest, {c['trees']} trees (Earth Engine smileRandomForest)",
        "features": MODEL_FEATURES,
        "label_source": "clear-day Sentinel-2 pixels where Google Dynamic World "
                        "(water / not-water) and standard water & vegetation indices "
                        "(MNDWI, NDVI) agree",
        "train_dates": [f"{p[0]} (radar {p[1]})" for i, p in pairs_used.items() if i not in test_idx],
        "test_dates": [f"{p[0]} (radar {p[1]})" for i, p in pairs_used.items() if i in test_idx],
        "baseline_threshold_db": t,
    })
    print(f"   test accuracy on unseen dates: RF water-vs-not "
          f"{report['rf_water_vs_not']['overall_accuracy']:.1%}, plain threshold "
          f"{report['threshold_water_vs_not']['overall_accuracy']:.1%}; RF 3-class "
          f"{report['rf_3class']['overall_accuracy']:.1%}")
    return rf, report


def classify(rf, s1_composite_raw, region):
    """Class map (0 water, 1 sand, 2 land) for an unsmoothed VV/VH/angle composite."""
    return features(s1_composite_raw, region).classify(rf).rename("class")


def probabilities_packed(rf, s1_composite_raw, region):
    """Per-pixel class probabilities, packed into one byte for download.

    Low 4 bits = P(water) x 15, high 4 bits = P(sand) x 15;
    P(land) = 1 - the two. Unpack with unpack_probabilities().
    """
    probs = (features(s1_composite_raw, region)
             .classify(rf.setOutputMode("MULTIPROBABILITY"))
             .arrayFlatten([CLASSES]))
    q = probs.multiply(15).round()
    return q.select("water").add(q.select("sand").multiply(16)).rename("p")


def unpack_probabilities(packed):
    """numpy byte array -> (p_water, p_sand, p_land) float arrays."""
    pw = (packed & 15) / 15.0
    ps = (packed >> 4) / 15.0
    return pw, ps, (1 - pw - ps).clip(0, 1)
