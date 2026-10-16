"""Picture layers for the dashboard: radar images, optical images, masks."""
import datetime as dt

import ee
import numpy as np
import requests

S2 = "COPERNICUS/S2_SR_HARMONIZED"
MAX_S2_CLOUD_PCT = 40


def _shift(date_str, days):
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=days)).isoformat()


def s2_clearest(region, window, pad_days=7, side="before"):
    """Least-cloudy Sentinel-2 date near the window, or None.

    The search extends only away from the other window (earlier for
    'before', later for 'after'), so before and after never share an image.

    During the monsoon there is often no usable optical image, which is why
    detection uses radar. When a clear-ish image exists, it's shown so people
    can see the scene in true colour.
    """
    coll = (ee.ImageCollection(S2).filterBounds(region)
            .filterDate(_shift(window[0], -pad_days if side == "before" else 0),
                        _shift(window[1], (pad_days if side == "after" else 0) + 1))
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_S2_CLOUD_PCT)))
    if coll.size().getInfo() == 0:
        return None
    best = coll.sort("CLOUDY_PIXEL_PERCENTAGE").first()
    date = best.date().format("YYYY-MM-dd").getInfo()
    day = coll.filterDate(date, _shift(date, 1))
    cloud = day.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE").getInfo()
    return {"image": day.mosaic().clip(region), "date": date, "cloud_pct": round(cloud, 1)}


def s2_dry_season_reference(region, start="2026-01-01", end="2026-03-31"):
    """Cloud-masked dry-season median: what the land looks like in the clear."""
    def mask(img):
        scl = img.select("SCL")
        return img.updateMask(scl.neq(3).And(scl.lt(8).Or(scl.gt(10))))
    return (ee.ImageCollection(S2).filterBounds(region).filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
            .map(mask).median().clip(region))


def vis_s2(img):
    return img.visualize(bands=["B4", "B3", "B2"], min=0, max=2500, gamma=1.2)


def vis_s1(img):
    # Grey-scale VV: water is black, land is grey, built-up areas bright.
    return img.select("VV").visualize(min=-25, max=0)


# erosion, isolated new water, accretion, flooded low land (not erosion)
CHANGE_COLOURS = {1: (227, 52, 47), 2: (245, 166, 35), 3: (47, 179, 68), 4: (150, 190, 215)}
CLASS_COLOURS = {0: (43, 108, 176), 1: (232, 216, 168), 2: (79, 138, 60)}   # water, sand, land


def save_classes_png(arr, colours, path, mask=None):
    """Render a small-integer raster to a transparent PNG (locally)."""
    from PIL import Image
    rgba = np.zeros(arr.shape + (4,), np.uint8)
    for value, rgb in colours.items():
        sel = arr == value
        if mask is not None:
            sel &= mask
        rgba[sel] = (*rgb, 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def fetch_array(image, grid, attempts=3):
    """Download an EE image as a numpy structured array on `grid`.

    Retries: a dropped connection (e.g. the computer sleeping) otherwise
    shows up as a request that never returns.
    """
    for attempt in range(1, attempts + 1):
        try:
            return ee.data.computePixels({"expression": image, "fileFormat": "NUMPY_NDARRAY",
                                          "grid": grid.ee_grid()})
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"   download failed ({str(exc)[:80]}); retrying ({attempt}/{attempts - 1})")


def download_png(vis_img, bounds, path, dimensions=1600):
    """Render an EE image to a PNG aligned to the lon/lat bounding box."""
    w, s, e, n = bounds
    region = ee.Geometry.Rectangle([w, s, e, n], "EPSG:4326", False)
    url = vis_img.getThumbURL({"region": region, "dimensions": dimensions,
                               "format": "png", "crs": "EPSG:4326"})
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
