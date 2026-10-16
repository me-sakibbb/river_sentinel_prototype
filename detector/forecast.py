"""Erosion FORECAST: which stretches of riverbank are likely to be lost next.

One sentence: we showed the computer 37 years of dry-season satellite maps of
the Jamuna and the Ganges-Padma (four reaches, one model), and it learned what
a stretch of land looks like in the dry season *before* the river takes it in
the following monsoon.

How:
  * Every dry season (January-March, low water, few clouds), Landsat sees
    the river clearly. A pixel is open water if its water index (MNDWI) is
    above 0 and it has no vegetation (NDVI below 0; see `clean_water`).
    This gives one water map per year, 30 m pixels, 1990-2026. Cells are
    scored from 1995, once five years of history exist.
  * The river corridor is cut into 240 m x 240 m cells. For every cell of
    land near the river in year t, we record what could be known at that
    time (how close the river is, whether the main channel is moving
    towards it, whether the bank nearby eroded in the last years, whether
    it is a young char or the mainland, ...) and whether it was lost to the
    river by the next dry season (year t+1).
  * A gradient-boosting model learns from the older years and is scored
    on later years it never saw. That is the same as issuing a real
    forecast each spring.

Honesty notes:
  * Dry-season maps avoid the monsoon-flood confound, but each yearly label
    is "land at low water in year t, water at low water in t+1": it counts net
    loss over a year, not individual collapses.
  * Bank protection works (geobags, revetments) aren't in the data. Where
    BWDB has protected a bank, the model can over-predict.
  * The digital elevation models available were made after many of the
    training years, so using them would leak the future. We leave them out.
"""
import math
import warnings
from concurrent.futures import ThreadPoolExecutor

import ee
import numpy as np
from scipy import ndimage as ndi

from detector import imagery

# One model, several river reaches (the national design: one set of weights,
# rivers told apart by context features, not by separate models).
#   bounds      lon/lat box downloaded for the reach
#   upstream    unit vector (rows, cols) pointing UP-river, for "open water
#               upstream" features (rows grow southwards)
#   mainland    which box edges are mainland: land touching them isn't a char
# Boxes overlap a little, so features near a seam still see the river beyond
# it. Each cell is scored by ONE reach only: the first one (in this order)
# whose box contains it (see `owned_cells`).
REACHES = {
    # Below the Teesta confluence to above Aricha: Kazipur and Chauhali are here.
    "jamuna": {"river": "Jamuna", "bounds": [89.45, 23.95, 89.98, 25.25],
               "upstream": (-1.0, 0.0), "mainland": "ew"},
    # Brahmaputra-Jamuna from the Indian border (Kurigram) to the Teesta confluence.
    "jamuna_upper": {"river": "Jamuna", "bounds": [89.50, 25.10, 90.00, 25.90],
                     "upstream": (-1.0, 0.0), "mainland": "ew"},
    # Ganges from Rajshahi past Hardinge Bridge to the Jamuna confluence.
    "ganges": {"river": "Ganges-Padma", "bounds": [88.60, 23.70, 89.80, 24.45],
               "upstream": (-0.40, -0.92), "mainland": "ns"},
    # Padma from Goalundo to the Meghna confluence at Chandpur.
    "padma": {"river": "Ganges-Padma", "bounds": [89.65, 23.20, 90.65, 23.95],
              "upstream": (-0.55, -0.84), "mainland": "ns"},
}
# Kazipur and Chauhali (the pilot sites) are in this reach.
PILOT_REACH = "jamuna"
REACH_BOUNDS = REACHES[PILOT_REACH]["bounds"]
PIXEL_M = 30
CELL_PX = 8                  # 8 x 8 px = 240 m cells
FIRST_YEAR, LAST_YEAR = 1990, 2026
MIN_LOSS_HA = 1.0            # a cell "eroded" if at least this much of its land became water
# Only VEGETATED land counts (fields, homesteads, trees): NDVI >= 0.2 in the
# dry season. Bare sandbars go under and re-emerge with small differences in
# dry-season water level; that isn't the loss people suffer. (Same idea as
# Sentinel-1 erosion mapping in NHESS 2023: vegetated before -> water after.)
MIN_NDVI = 0.2
MAX_DIST_TO_WATER_M = 1000   # only land cells this close to the river are scored
NODATA = 255


class TileGrid:
    """A horizontal strip of a Grid, for downloading big areas in pieces."""

    def __init__(self, grid, row0, rows):
        self.g, self.row0, self.height, self.width = grid, row0, rows, grid.width

    def ee_grid(self):
        eg = self.g.ee_grid()
        eg["dimensions"] = {"width": self.width, "height": self.height}
        eg["affineTransform"] = dict(eg["affineTransform"],
                                     translateY=self.g.north - self.row0 * self.g.dlat)
        return eg


def _landsat(coll_id, bands, region, year):
    """One Landsat sensor's dry-season scenes, cloud-masked, as reflectance.
    Filter first: the band maths below drops the date/footprint properties."""
    green, swir, nir, red = bands

    def prep(img):
        qa = img.select("QA_PIXEL")
        # bits: 1 dilated cloud, 3 cloud, 4 cloud shadow
        clear = qa.bitwiseAnd(0b11010).eq(0)
        sr = img.select([green, swir, nir, red]).multiply(0.0000275).add(-0.2)
        return sr.rename(["green", "swir", "nir", "red"]).updateMask(clear)
    return (ee.ImageCollection(coll_id).filterBounds(region)
            .filterDate(f"{year}-01-01", f"{year}-04-01").map(prep))


def dry_season_image(year, region):
    """Median Landsat dry-season (Jan-Mar) composite: bands water (0/1/255), ndvi (x100)."""
    tm = ("SR_B2", "SR_B5", "SR_B4", "SR_B3")    # Landsat 5/7: green, swir1, nir, red
    oli = ("SR_B3", "SR_B6", "SR_B5", "SR_B4")   # Landsat 8/9
    coll = (_landsat("LANDSAT/LT05/C02/T1_L2", tm, region, year)
            .merge(_landsat("LANDSAT/LE07/C02/T1_L2", tm, region, year))
            .merge(_landsat("LANDSAT/LC08/C02/T1_L2", oli, region, year))
            .merge(_landsat("LANDSAT/LC09/C02/T1_L2", oli, region, year)))
    med = coll.median()
    mndwi = med.normalizedDifference(["green", "swir"])
    ndvi = med.normalizedDifference(["nir", "red"])
    water = mndwi.gt(0).unmask(NODATA).toByte().rename("water")
    ndvi_b = ndvi.multiply(100).round().add(100).clamp(0, 200).unmask(NODATA).toByte().rename("ndvi")
    return water.addBands(ndvi_b)


def fetch_year(year, grid, cache_dir, strips=4):
    """Download one year's water + NDVI arrays (cached as .npz)."""
    path = cache_dir / f"dry_{year}.npz"
    if path.exists():
        d = np.load(path)
        return d["water"], d["ndvi"]
    w, s, e, n = grid.bounds
    region = ee.Geometry.Rectangle([w, s, e, n], "EPSG:4326", False)
    img = dry_season_image(year, region)
    step = math.ceil(grid.height / strips)
    tiles = [TileGrid(grid, r, min(step, grid.height - r)) for r in range(0, grid.height, step)]
    with ThreadPoolExecutor(len(tiles)) as pool:
        parts = list(pool.map(lambda t: imagery.fetch_array(img, t), tiles))
    water = np.concatenate([p["water"] for p in parts])
    ndvi = np.concatenate([p["ndvi"] for p in parts])
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, water=water, ndvi=ndvi)
    return water, ndvi




# ---------------------------------------------------------------------------
# Local analysis (numpy/scipy)
#
# LEAKAGE RULE: every input for year t is computed from dry-season maps of
# years <= t only. tests/test_forecast.py checks this by scrambling all later
# years and asserting the year-t inputs don't change.
# ---------------------------------------------------------------------------
EIGHT = np.ones((3, 3), bool)                 # 8-connected neighbourhood
DISK5 = np.hypot(*np.mgrid[-5:6, -5:6]) <= 5  # opening with it keeps channels >= ~300 m wide
MIN_RIVER_PX = 50                             # water bodies >= ~4.5 ha count as river
CORRIDOR_BUFFER_M = 2000
HISTORY_YEARS = 5                             # years of history needed before a year is scored


def reach_grid(name):
    from detector.change import Grid
    return Grid(REACHES[name]["bounds"], PIXEL_M)


def clean_water(water, ndvi):
    """Open water only: water index says water AND there's no vegetation.

    In Jan-Mar, irrigated boro rice fields are flooded and show up as
    "water" on the water index alone. In some years that doubled the mapped
    water. Open river water has NDVI below 0; flooded paddy doesn't.
    """
    out = np.where((water == 1) & (ndvi < 100), 1, 0).astype(np.uint8)
    out[(water == NODATA) | (ndvi == NODATA)] = NODATA
    return out


def fill_forward(stack):
    """Cloud / sensor gaps: carry the last KNOWN state forward.

    Never backward: filling year t from year t+1 would put the future into
    year t's inputs. Pixels not observed yet in any year count as land (0);
    the `observed_share` input tells the model how much of a cell was seen.
    """
    filled = stack.copy()
    for i in range(1, len(filled)):
        gap = filled[i] == NODATA
        filled[i][gap] = filled[i - 1][gap]
    filled[filled == NODATA] = 0
    return filled


def corridor_of(ever_water, buffer_m=CORRIDOR_BUFFER_M):
    """The river's braid belt as known so far: the biggest connected area
    that has been water in any year up to now, widened by `buffer_m`.
    Built from past years only (a corridor from all years would tell the
    model where the river goes later)."""
    lab, n = ndi.label(ever_water, EIGHT)
    if n == 0:
        return np.zeros(ever_water.shape, bool)
    biggest = np.argmax(np.bincount(lab.ravel())[1:]) + 1
    return ndi.distance_transform_edt(lab != biggest) <= buffer_m / PIXEL_M


def river_bodies(water):
    """Drop ponds: only water bodies of at least MIN_RIVER_PX pixels."""
    lab, _ = ndi.label(water, EIGHT)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return (sizes >= MIN_RIVER_PX)[lab]


def shift_from(arr, dr, dc, fill=np.nan):
    """out[r, c] = arr[r + dr, c + dc]. Outside the grid -> `fill`.

    (np.roll would wrap around: the top rows would read the bottom of the map.)
    """
    out = np.full(arr.shape, fill, dtype=np.float32)
    h, w = arr.shape
    r0, r1 = max(0, -dr), min(h, h - dr)
    c0, c1 = max(0, -dc), min(w, w - dc)
    if r0 < r1 and c0 < c1:
        out[r0:r1, c0:c1] = arr[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def _cells(arr, how="mean", mask=None):
    """Block-reduce a pixel raster to 240 m cells."""
    h, w = arr.shape
    H, W = h // CELL_PX, w // CELL_PX
    a = arr[:H * CELL_PX, :W * CELL_PX].reshape(H, CELL_PX, W, CELL_PX).astype(np.float32)
    if mask is not None:
        m = mask[:H * CELL_PX, :W * CELL_PX].reshape(H, CELL_PX, W, CELL_PX)
        a = np.where(m, a, np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if how == "mean":
            return np.nanmean(a, axis=(1, 3))
        if how == "min":
            return np.nanmin(a, axis=(1, 3))
        if how == "sum":
            return np.nansum(a, axis=(1, 3))
    raise ValueError(how)


def cell_centres(arr):
    """The pixel at the centre of each 240 m cell."""
    H, W = arr.shape[0] // CELL_PX, arr.shape[1] // CELL_PX
    c = CELL_PX // 2
    return arr[c::CELL_PX, c::CELL_PX][:H, :W].astype(np.float32)


def owned_cells(name, shape):
    """Cells this reach scores: inside its box and not inside the box of a
    reach listed before it (so no cell is counted twice)."""
    grid = reach_grid(name)
    H, W = shape[0] // CELL_PX, shape[1] // CELL_PX
    lat = grid.north - (np.arange(H) * CELL_PX + CELL_PX / 2) * grid.dlat
    lon = grid.west + (np.arange(W) * CELL_PX + CELL_PX / 2) * grid.dlon
    LON, LAT = np.meshgrid(lon, lat)
    own = np.ones((H, W), bool)
    for other, cfg in REACHES.items():
        if other == name:
            break
        w, s, e, n = cfg["bounds"]
        own &= ~((LON >= w) & (LON <= e) & (LAT >= s) & (LAT <= n))
    return own


def _year_rasters(water, corridor, reach):
    """Per-year pixel rasters that the cell inputs are built from."""
    w = (water == 1) & corridor
    river = river_bodies(w)
    main = ndi.binary_opening(river, DISK5)
    shape = w.shape
    if river.any():
        dist_river, (ir, ic) = ndi.distance_transform_edt(~river, return_indices=True)
        # Width of the channel nearest to each pixel: twice the distance from
        # the channel's centre line to its banks, looked up at the nearest water.
        half = ndi.maximum_filter(ndi.distance_transform_edt(river), size=9)
        near_width = (half[ir, ic] * 2 * PIXEL_M).astype(np.float32)
        del ir, ic, half
    else:
        dist_river, near_width = np.full(shape, 9e3), np.zeros(shape, np.float32)
    dist_main = ndi.distance_transform_edt(~main) * PIXEL_M if main.any() else np.full(shape, 9e3)
    riv = river.astype(np.float32)
    frac_500 = ndi.uniform_filter(riv, 33)
    frac_2k = ndi.uniform_filter(riv, 133)
    # Open water up-river of each pixel (the direction the flow comes from).
    ur, uc = reach["upstream"]
    up_1km = shift_from(frac_500, int(round(ur * 33)), int(round(uc * 33)))
    up_2km = shift_from(frac_500, int(round(ur * 67)), int(round(uc * 67)))
    # Braiding: how much river bank there is around (many channels = many banks).
    bank = river & ~ndi.binary_erosion(river, EIGHT)
    bank_2km = ndi.uniform_filter(bank.astype(np.float32), 133)
    # Char vs mainland: land that doesn't reach the box edges the mainland is on.
    land = ~w
    lab, _ = ndi.label(land)
    edges = (lab[:, 0], lab[:, -1]) if reach["mainland"] == "ew" else (lab[0, :], lab[-1, :])
    edge = set(np.unique(edges[0])) | set(np.unique(edges[1]))
    edge.discard(0)
    char = land & ~np.isin(lab, list(edge))
    return {"river": river, "dist_river": (dist_river * PIXEL_M).astype(np.float32),
            "dist_main": dist_main.astype(np.float32), "near_width": near_width,
            "frac_500": frac_500, "frac_2k": frac_2k, "up_1km": up_1km, "up_2km": up_2km,
            "bank_2km": bank_2km, "char": char}


# The model's possible inputs. BASE are the first version's 14 inputs; the
# groups after it were added for version 2 and are each kept only if they
# improve the VALIDATION years (see detector/evaluation.py), never the test years.
BASE_FEATURES = [
    "dist_river_m",        # how close the river is (nearest land pixel in the cell)
    "dist_main_m",         # how close a wide (>= ~300 m) channel is
    "main_approach_1y_m",  # how far that main channel moved towards the cell last year
    "main_approach_3y_m",  # ... per year, over the last 3 years
    "loss_nearby_1y_ha",   # land lost to the river last year, this cell + neighbours
    "loss_nearby_3y_ha",   # ... over the last 3 years
    "loss_self_1y_ha",     # land this cell itself lost last year
    "water_within_500m",   # share of water within ~500 m
    "water_within_2km",    # share of water within ~2 km
    "water_upstream_1km",  # share of water ~1 km up-river of the cell
    "land_age_years",      # years since the cell's land was last water (young chars erode fastest)
    "char_share",          # share of the cell's land on a char (island), not the mainland
    "land_share",          # share of the cell that is land
    "ndvi",                # vegetation cover (established, farmed land vs bare sand)
]
FEATURE_GROUPS = {
    # Narrow side channels also eat banks (the 2026 Kazipur misses).
    "side_channels": ["nearest_channel_width_m", "river_approach_1y_m", "river_approach_3y_m"],
    "upstream_2km": ["water_upstream_2km"],
    "erosion_history": ["years_eroded_last5", "loss_1km_3y_ha"],
    "river_context": ["bank_density_2km", "observed_share"],
}
FEATURES = BASE_FEATURES + [f for g in FEATURE_GROUPS.values() for f in g]
FEATURE_LABELS = {
    "dist_river_m": "distance to the river",
    "dist_main_m": "distance to a main channel",
    "main_approach_1y_m": "main channel approaching (last year)",
    "main_approach_3y_m": "main channel approaching (3-year trend)",
    "loss_nearby_1y_ha": "erosion nearby last year",
    "loss_nearby_3y_ha": "erosion nearby, last 3 years",
    "loss_self_1y_ha": "erosion in this cell last year",
    "water_within_500m": "water all around (500 m)",
    "water_within_2km": "water all around (2 km)",
    "water_upstream_1km": "open water upstream (1 km)",
    "land_age_years": "age of the land",
    "char_share": "char (island) rather than mainland",
    "land_share": "amount of land in the cell",
    "ndvi": "vegetation",
    "nearest_channel_width_m": "width of the nearest channel",
    "river_approach_1y_m": "any channel approaching (last year)",
    "river_approach_3y_m": "any channel approaching (3-year trend)",
    "water_upstream_2km": "open water upstream (2 km)",
    "years_eroded_last5": "years with erosion nearby, last 5",
    "loss_1km_3y_ha": "erosion within 1 km, last 3 years",
    "bank_density_2km": "braiding (river banks within 2 km)",
    "observed_share": "share of the cell seen (cloud-free)",
}


def build_table(years, water_raw, ndvi, reach_name):
    """Cell x year table for one reach: inputs known at year t, label = loss by t+1.

    `water_raw` is the cleaned water stack with NODATA gaps (clean_water);
    `ndvi` the matching NDVI stack. Returns dict of arrays: year, row, col,
    X (N x len(FEATURES)), loss_ha (NaN for the last year: future unknown).
    """
    reach = REACHES[reach_name]
    ha_px = PIXEL_M * PIXEL_M / 1e4
    shape = water_raw.shape[1:]
    owned = owned_cells(reach_name, shape)
    filled = fill_forward(water_raw)

    def vegetated(i):
        return (ndvi[i] != NODATA) & (ndvi[i] >= 100 + 100 * MIN_NDVI)

    ever = np.zeros(shape, bool)
    land_age = np.zeros(shape, np.float32)
    dm_hist, dr_hist = {}, {}
    # losses[y]: ha of vegetated land (observed in y-1) that became part of the
    # RIVER (observed in y). Water bodies not connected to the river (flooded
    # fields, ponds) don't count: that isn't erosion.
    losses = {}
    ring, box1km = np.ones((3, 3)), np.ones((9, 9))
    out = {k: [] for k in ("year", "row", "col", "X")}
    for i, y in enumerate(years):
        ever |= filled[i] == 1
        corridor = corridor_of(ever)
        if i > 0:
            river_now = river_bodies((water_raw[i] == 1) & corridor)
            lost = (water_raw[i - 1] == 0) & vegetated(i - 1) & river_now
            losses[y] = _cells(lost, "sum") * ha_px
            del river_now, lost
        land_age = np.where(filled[i] == 0, land_age + 1, 0).astype(np.float32)
        r = _year_rasters(filled[i], corridor, reach)
        dm_hist[y] = _cells(r["dist_main"], "min")
        dr_hist[y] = cell_centres(r["dist_river"])
        if i < HISTORY_YEARS:
            continue
        land = (filled[i] == 0) & corridor
        dist_river = _cells(r["dist_river"], "min", land)
        veg_share = _cells(land & vegetated(i), "mean")
        cand = (veg_share >= 0.25) & (dist_river <= MAX_DIST_TO_WATER_M) & \
               (_cells(corridor, "mean") > 0.5) & owned
        l1 = losses[y]
        l3 = losses[y] + losses[years[i - 1]] + losses[years[i - 2]]
        eroded_years = sum((ndi.convolve(losses[years[i - k]], ring, mode="constant") >= MIN_LOSS_HA)
                           .astype(np.float32) for k in range(5))
        f = {
            "dist_river_m": dist_river,
            "dist_main_m": _cells(r["dist_main"], "min", land),
            "main_approach_1y_m": dm_hist[years[i - 1]] - dm_hist[y],
            "main_approach_3y_m": (dm_hist[years[i - 3]] - dm_hist[y]) / 3,
            "loss_nearby_1y_ha": ndi.convolve(l1, ring, mode="constant"),
            "loss_nearby_3y_ha": ndi.convolve(l3, ring, mode="constant"),
            "loss_self_1y_ha": l1,
            "water_within_500m": _cells(r["frac_500"], "mean"),
            "water_within_2km": _cells(r["frac_2k"], "mean"),
            "water_upstream_1km": _cells(r["up_1km"], "mean"),
            "land_age_years": np.minimum(_cells(land_age, "mean", land), 30),
            "char_share": _cells(r["char"], "mean", land),
            "land_share": _cells(land, "mean"),
            "ndvi": _cells(ndvi[i].astype(np.float32) - 100, "mean", land & (ndvi[i] != NODATA)),
            "nearest_channel_width_m": _cells(r["near_width"], "mean", land),
            "river_approach_1y_m": dr_hist[years[i - 1]] - dr_hist[y],
            "river_approach_3y_m": (dr_hist[years[i - 3]] - dr_hist[y]) / 3,
            "water_upstream_2km": _cells(r["up_2km"], "mean"),
            "years_eroded_last5": eroded_years,
            "loss_1km_3y_ha": ndi.convolve(l3, box1km, mode="constant"),
            "bank_density_2km": _cells(r["bank_2km"], "mean"),
            "observed_share": _cells(water_raw[i] != NODATA, "mean"),
        }
        del r
        rows, cols = np.nonzero(cand)
        out["year"].append(np.full(len(rows), y, np.int16))
        out["row"].append(rows.astype(np.int32))
        out["col"].append(cols.astype(np.int32))
        out["X"].append(np.stack([f[k][rows, cols] for k in FEATURES], axis=1).astype(np.float32))
        print(f"   {reach_name} {y}: {len(rows)} bank cells", flush=True)
    table = {k: np.concatenate(v) for k, v in out.items()}
    # Label = loss during (t -> t+1), known only once the t+1 map exists.
    loss = np.full(len(table["year"]), np.nan, np.float32)
    for y in np.unique(table["year"]):
        nxt = losses.get(int(y) + 1)
        if nxt is not None:
            sel = table["year"] == y
            loss[sel] = nxt[table["row"][sel], table["col"][sel]]
    table["loss_ha"] = loss
    table["features"] = np.array(FEATURES)
    return table


def capture(score, loss_ha, top):
    """Share of all land actually lost that fell in the `top` fraction of cells
    ranked by `score` (e.g. top=0.1: 'the riskiest 10 % of bank caught X %')."""
    order = np.argsort(-score, kind="stable")
    k = max(1, int(round(top * len(score))))
    total = loss_ha.sum()
    return float(loss_ha[order[:k]].sum() / total) if total > 0 else float("nan")
