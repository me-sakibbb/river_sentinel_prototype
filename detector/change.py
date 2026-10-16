"""Change detection: compare two water maps and describe what changed.

  land -> water, touching the old channel      = bank erosion (candidate)
  land -> water, NOT touching the old channel  = isolated new water
                                                  (flooded field, pond, or pit)
  water -> land                                = accretion (new land / sandbar)

Among isolated new water, a few compact, regular, pit-sized patches get a
"possible extraction - review" flag. That flag is a HEURISTIC FOR A HUMAN TO
CHECK. It is not a finding that mining happened.

This runs locally on numpy arrays downloaded from Earth Engine (a lon/lat
grid of ~10 m pixels). Doing the patch analysis in Earth Engine's interactive
mode timed out.
"""
import math

import numpy as np
from scipy import ndimage as ndi

import config

EIGHT = np.ones((3, 3), bool)  # 8-connected neighbourhood


class Grid:
    """A north-up lon/lat grid with ~10 m square pixels."""

    def __init__(self, bounds, pixel_m=config.ANALYSIS_SCALE_M):
        w, s, e, n = bounds
        self.west, self.north = w, n
        self.dlat = pixel_m / 111320
        self.dlon = pixel_m / (111320 * math.cos(math.radians((s + n) / 2)))
        self.width = math.ceil((e - w) / self.dlon)
        self.height = math.ceil((n - s) / self.dlat)
        self.bounds = [w, n - self.height * self.dlat, w + self.width * self.dlon, n]
        self.px_m = pixel_m
        self.px_area_m2 = pixel_m * pixel_m

    def ee_grid(self):
        return {"dimensions": {"width": self.width, "height": self.height},
                "affineTransform": {"scaleX": self.dlon, "shearX": 0, "translateX": self.west,
                                    "shearY": 0, "scaleY": -self.dlat, "translateY": self.north},
                "crsCode": "EPSG:4326"}

    def lonlat(self, row, col):
        return self.west + (col + 0.5) * self.dlon, self.north - (row + 0.5) * self.dlat


def _drop_specks(mask):
    """Remove patches smaller than the minimum mapping unit."""
    lab, _ = ndi.label(mask, EIGHT)
    sizes = np.bincount(lab.ravel())
    keep = sizes >= config.MIN_PATCH_PIXELS
    keep[0] = False
    return keep[lab]


def _per_label(lab, n, weights):
    return np.bincount(lab.ravel(), weights=weights.ravel().astype(float), minlength=n + 1)[1:]


def _perimeter_px(lab, n):
    """Count pixel edges on each patch's boundary."""
    padded = np.pad(lab, 1)
    core = padded[1:-1, 1:-1]
    total = np.zeros(n + 1)
    for shifted in (padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]):
        boundary = (core > 0) & (shifted != core)
        total += np.bincount(core[boundary], minlength=n + 1)
    return total[1:]


def analyse(before_water, after_water, inside, grid, new_water=None, new_land=None, established=None):
    """Return (summary stats, per-patch list, class raster for display).

    new_water / new_land can be passed in to restrict change to pixels the
    classifier is confident about; by default any flip counts.

    established: optional mask of ESTABLISHED land (vegetated in the dry
    season and not water in any recent dry season). When given, only new
    water on established land can count as bank erosion. New water on
    sandbars and low, recently wet land next to the channel is reported
    separately as "flooded low land": when the river rises, that land goes
    under and comes back, and it was the main source of false "erosion".
    """
    bw = before_water & inside
    aw = after_water & inside
    new_water = _drop_specks((~bw & aw if new_water is None else new_water) & inside)
    new_land = _drop_specks((bw & ~aw if new_land is None else new_land) & inside)
    old_bank = ndi.binary_dilation(bw, EIGHT)  # next to old water
    low_land = np.zeros_like(new_water)
    if established is not None:
        low = new_water & ~established
        new_water = _drop_specks(new_water & established)
        # Established land counts as at the channel if it is within ~30 m of
        # the old water: a thin strip of bare sand often lines the bank.
        old_bank = ndi.binary_dilation(bw, EIGHT, iterations=3)
        low_lab, n_low = ndi.label(low, EIGHT)
        at_channel = np.r_[False, _per_label(low_lab, n_low, ndi.binary_dilation(bw, EIGHT)) > 0]
        low_land = at_channel[low_lab]
        new_water |= low & ~low_land          # isolated new water stays (pits, ponds)
    # First row of eroded pixels behind the old waterline (or the flooded
    # strip in front of it); x 10 m roughly measures how much shoreline moved.
    edge = new_water & ndi.binary_dilation(bw | low_land, EIGHT)

    lab, n = ndi.label(new_water, EIGHT)
    npx = _per_label(lab, n, np.ones_like(lab))
    touches = _per_label(lab, n, old_bank) > 0
    edge_px = _per_label(lab, n, edge)
    dist_m = ndi.distance_transform_edt(~bw, sampling=grid.px_m)
    mean_dist = _per_label(lab, n, dist_m) / np.maximum(npx, 1)
    perim_m = _perimeter_px(lab, n) * grid.px_m
    boxes = ndi.find_objects(lab)
    centres = ndi.center_of_mass(new_water, lab, range(1, n + 1)) if n else []

    patches = []
    for i in range(n):
        box = boxes[i]
        bbox_px = (box[0].stop - box[0].start) * (box[1].stop - box[1].start)
        area = npx[i] * grid.px_area_m2
        lon, lat = grid.lonlat(*centres[i])
        patches.append(classify_patch({
            "area_m2": area,
            "compactness": 4 * math.pi * area / max(perim_m[i], 1) ** 2,
            "fill_ratio": npx[i] / bbox_px,
            "touches_channel": bool(touches[i]),
            "edge_px": edge_px[i],
            "dist_to_channel_m": mean_dist[i],
        }) | {"lon": round(lon, 6), "lat": round(lat, 6)})

    is_erosion = np.array([False] + [p["kind"] == "bank_erosion" for p in patches])
    attached = np.concatenate([[False], touches])[lab]
    classes = np.zeros(lab.shape, np.uint8)  # 0 none (incl. bank change below 1 ha)
    classes[is_erosion[lab]] = 1             # bank erosion
    classes[new_water & ~attached] = 2       # isolated new water
    classes[new_land] = 3                    # accretion
    classes[low_land & (classes == 0)] = 4   # flooded low land / sandbar (not erosion)

    ha = grid.px_area_m2 / 1e4
    erosion = [p for p in patches if p["kind"] == "bank_erosion"]
    stats = {
        "bank_erosion_ha": round(sum(p["area_ha"] for p in erosion), 2),
        "bank_erosion_patches": len(erosion),
        "eroding_edge_km": round(sum(p["edge_km"] for p in erosion), 2),
        "longest_patch_edge_km": max((p["edge_km"] for p in erosion), default=0),
        "isolated_new_water_ha": round(sum(p["area_ha"] for p in patches
                                           if p["kind"] == "isolated_new_water"), 2),
        "isolated_new_water_patches": sum(p["kind"] == "isolated_new_water" for p in patches),
        "small_bank_change_ha": round(sum(p["area_ha"] for p in patches
                                          if p["kind"] == "small_bank_change"), 2),
        "review_flags": sum(p["review_flag_possible_extraction"] for p in patches),
        "flooded_low_land_ha": round(low_land.sum() * ha, 2),
        "established_land_filter": established is not None,
        "accretion_ha": round(new_land.sum() * ha, 2),
        "water_before_ha": round(bw.sum() * ha, 2),
        "water_after_ha": round(aw.sum() * ha, 2),
    }
    return stats, patches, classes


def classify_patch(p):
    """Label one patch.

    Mining flag = heuristic for human review only, never a determination.
    """
    h = config.MINING_HEURISTIC
    area_ha = p["area_m2"] / 1e4
    attached = p["touches_channel"]
    dist = p["dist_to_channel_m"]
    flagged = (not attached
               and h["min_area_ha"] <= area_ha <= h["max_area_ha"]
               and p["compactness"] >= h["min_compactness"]
               and p["fill_ratio"] >= h["min_fill_ratio"]
               and dist <= h["max_dist_to_channel_m"])
    if not attached:
        kind = "isolated_new_water"
    elif area_ha >= config.MIN_EROSION_PATCH_HA:
        kind = "bank_erosion"
    else:
        kind = "small_bank_change"  # below screening size: not counted as erosion
    return {
        "kind": kind,
        "area_ha": round(area_ha, 2),
        "compactness": round(p["compactness"], 2),
        "fill_ratio": round(p["fill_ratio"], 2),
        "edge_km": round(p["edge_px"] * config.ANALYSIS_SCALE_M / 1000, 2),
        "dist_to_channel_m": round(float(dist)),
        "review_flag_possible_extraction": bool(flagged),
    }
