"""Water mapping from Sentinel-1 radar.

One-sentence version: calm water bounces the radar signal away from the
satellite, so it shows up dark; we mark the dark pixels in each image as water.

Radar is used instead of optical imagery because monsoon cloud hides the
ground in most Sentinel-2 scenes from June to October. Radar sees through it.
"""
import datetime as dt

import ee

import config


def _next_day(date_str):
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=1)).isoformat()


def s1_collection(region, window):
    start, end = window
    return (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filterDate(start, _next_day(end))  # end date inclusive
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))


def _scene_table(coll, region):
    """Client-side list of scenes: orbit, pass, date, share of region covered."""
    region_area = region.area(100)

    def describe(img):
        cover = img.geometry().intersection(region, 100).area(100).divide(region_area)
        return ee.Feature(None, {
            "orbit": img.get("relativeOrbitNumber_start"),
            "pass": img.get("orbitProperties_pass"),
            "platform": img.get("platform_number"),
            "date": img.date().format("YYYY-MM-dd"),
            "cover": cover,
        })

    feats = ee.FeatureCollection(coll.map(describe)).getInfo()["features"]
    return [f["properties"] for f in feats]


def pick_orbit(region, before_window, after_window):
    """Choose one relative orbit present in both windows.

    Comparing images from the same orbit track means the same viewing angle
    on both dates, so a change in brightness is a change on the ground and
    not a change in geometry.
    """
    before = _scene_table(s1_collection(region, before_window), region)
    after = _scene_table(s1_collection(region, after_window), region)

    def coverage(scenes, orbit):
        # Frames from one pass tile along-track, so summing per date and
        # taking the best date approximates that window's coverage.
        by_date = {}
        for s in scenes:
            if s["orbit"] == orbit:
                by_date[s["date"]] = by_date.get(s["date"], 0) + s["cover"]
        return min(1.0, max(by_date.values())) if by_date else 0.0

    orbits = {s["orbit"] for s in before} & {s["orbit"] for s in after}
    if not orbits:
        raise RuntimeError(
            f"No Sentinel-1 orbit track has scenes in both windows "
            f"{before_window} and {after_window}. Try widening the windows in config.RUNS.")
    cover = {o: min(coverage(before, o), coverage(after, o)) for o in orbits}
    full = [o for o in orbits if cover[o] >= 0.95 * max(cover.values())]

    # Calm-water check: wind roughens water so it looks brighter to radar and
    # can be mistaken for land. Measure how dark the river's permanent channel
    # looks in each candidate image; reject windy ones.
    calm = {o: calm_water_db(region, before_window, after_window, o) for o in full}
    limit = config.WINDY_WATER_DB
    ok = [o for o in full if max(calm[o]) <= limit]
    pool = ok or full
    best = min(pool, key=lambda o: (abs(calm[o][0] - calm[o][1]), -cover[o]) if ok
               else (max(calm[o]), -cover[o]))

    def dates(scenes):
        return sorted({s["date"] for s in scenes if s["orbit"] == best})

    return {
        "orbit": best,
        "pass": next(s["pass"] for s in before if s["orbit"] == best),
        "before_dates": dates(before),
        "after_dates": dates(after),
        "before_cover": round(coverage(before, best), 3),
        "after_cover": round(coverage(after, best), 3),
        "calm_water_db": {"before": calm[best][0], "after": calm[best][1], "limit": limit},
        "rejected_windy": {str(o): calm[o] for o in full if o not in ok},
        "quality_warning": None if ok else (
            "Every available image pair has rough (windy) water; results for "
            "this window are unreliable."),
    }


def calm_water_db(region, before_window, after_window, orbit):
    """How dark the water looks, before and after: the 10th-percentile VV (dB).

    In a river scene the darkest 10% of pixels are almost all water. Calm
    water sits around -20 dB or darker; wind raises it towards -17 dB, at which
    point open water starts to be mistaken for land. (We tried the JRC
    'permanent water' layer as the reference instead, but the braided Jamuna
    has moved since that record, so much of it is now sandbar.)
    """
    vals = ee.Dictionary({
        w: s1_composite(region, win, orbit).select("VV")
             .reduceRegion(ee.Reducer.percentile([10]), region, 30, maxPixels=1e9).get("VV")
        for w, win in (("before", before_window), ("after", after_window))
    }).getInfo()
    return [round(vals["before"], 1), round(vals["after"], 1)]


def s1_composite(region, window, orbit, smooth=True):
    """Median of all scenes from one orbit in the window, in dB.

    smooth=True applies the speckle filter (threshold method and pictures);
    the AI classifier does its own smoothing, so it takes smooth=False.
    """
    img = (s1_collection(region, window)
           .filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
           .select(["VV", "VH", "angle"])
           .median())
    if smooth:
        img = img.focalMedian(config.SPECKLE_RADIUS_M, "circle", "meters")
    return img.clip(region)


def _otsu(histogram):
    """Otsu's method: the threshold that best splits a two-humped histogram."""
    h = ee.Dictionary(histogram)
    counts = ee.Array(h.get("histogram"))
    means = ee.Array(h.get("bucketMeans"))
    size = means.length().get([0])
    total = counts.reduce(ee.Reducer.sum(), [0]).get([0])
    total_sum = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])
    mean = total_sum.divide(total)

    def between_class_variance(i):
        a_counts = counts.slice(0, 0, i)
        a_count = a_counts.reduce(ee.Reducer.sum(), [0]).get([0])
        a_mean = (means.slice(0, 0, i).multiply(a_counts)
                  .reduce(ee.Reducer.sum(), [0]).get([0]).divide(a_count))
        b_count = total.subtract(a_count)
        b_mean = total_sum.subtract(a_count.multiply(a_mean)).divide(b_count)
        return (a_count.multiply(a_mean.subtract(mean).pow(2))
                .add(b_count.multiply(b_mean.subtract(mean).pow(2))))

    # Split after bucket i, for i = 1..size-1 (both classes non-empty); the
    # candidate threshold for split i is the mean of bucket i-1.
    bcv = ee.List.sequence(1, size.subtract(1)).map(between_class_variance)
    return means.slice(0, 0, size.subtract(1)).sort(bcv).get([-1])


def water_threshold(composite, region):
    """Pick the dark/bright cut-off for this image automatically (Otsu),
    falling back to a standard value if the result is implausible."""
    band = config.S1_POLARISATION
    hist = composite.select(band).reduceRegion(
        reducer=ee.Reducer.histogram(255, 0.1),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True).get(band)
    try:
        t = float(_otsu(hist).getInfo())
    except Exception as exc:  # e.g. empty histogram
        print(f"    ! Otsu threshold failed ({exc}); using fallback")
        return config.FALLBACK_THRESHOLD_DB, "fallback"
    lo, hi = config.OTSU_BOUNDS_DB
    if not lo <= t <= hi:
        print(f"    ! Otsu threshold {t:.1f} dB outside {lo}..{hi}; using fallback")
        return config.FALLBACK_THRESHOLD_DB, "fallback"
    return round(t, 2), "otsu"


def scene_stats(vv_smoothed, region):
    """Server-side (Otsu cut-off, 10th-percentile VV) of one image, in dB.

    Same recipe as water_threshold() and calm_water_db(), but kept inside
    Earth Engine so the AI classifier can use them as features without a
    round trip per image. An implausible Otsu value falls back to the
    standard threshold, as in water_threshold().
    """
    stats = vv_smoothed.rename("VV").reduceRegion(
        reducer=ee.Reducer.histogram(255, 0.1).combine(ee.Reducer.percentile([10]), "", True),
        geometry=region, scale=30, maxPixels=1e9, bestEffort=True)
    t = ee.Number(_otsu(stats.get("VV_histogram")))
    lo, hi = config.OTSU_BOUNDS_DB
    t = ee.Number(ee.Algorithms.If(t.gte(lo).And(t.lte(hi)), t, config.FALLBACK_THRESHOLD_DB))
    return t, ee.Number(stats.get("VV_p10"))


def water_mask(composite, threshold_db):
    """1 = water, 0 = land."""
    return composite.select(config.S1_POLARISATION).lt(threshold_db).rename("water").unmask(0)
