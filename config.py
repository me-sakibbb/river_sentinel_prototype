"""Project configuration: study sites, analysis windows, thresholds, reference events.

Everything a reviewer might want to question lives here, in one place.
"""
from pathlib import Path

ROOT = Path(__file__).parent
BOUNDARY_DIR = ROOT / "data" / "boundaries"
WEB_DATA_DIR = ROOT / "web" / "data"

# Google Cloud project registered for Earth Engine. Set via the EE_PROJECT
# environment variable or `python run_analysis.py --project <id>`.
EE_PROJECT_ENV = "EE_PROJECT"

# ---------------------------------------------------------------------------
# Study sites
# ---------------------------------------------------------------------------
# The brief's rough box (89.60-89.80 E, 24.00-24.70 N) cuts off part of both
# upazilas: OpenStreetMap's boundaries run east to ~89.83 E (Chauhali) and
# north to ~24.79 N (Kazipur). So we use the upazila polygons themselves.
# We do NOT have verified coordinates for the individual chars (Char Girish,
# Khash Rajbari, Char Salimabad), so results are reported per upazila.
SITES = {
    "kazipur": {
        "name": "Kazipur upazila",
        "boundary": BOUNDARY_DIR / "kazipur.geojson",
    },
    "chauhali": {
        "name": "Chauhali upazila",
        "boundary": BOUNDARY_DIR / "chauhali.geojson",
    },
}

# ---------------------------------------------------------------------------
# Analysis runs: each one compares a "before" window with an "after" window.
# Each window becomes one Sentinel-1 composite (median of the scenes in it,
# all from the same orbit track so viewing geometry is the same).
# ---------------------------------------------------------------------------
RUNS = [
    {
        "id": "jun2026",
        "label": "Mid-June to early July 2026 (validation window)",
        "before": ("2026-06-01", "2026-06-13"),
        "after": ("2026-06-25", "2026-07-08"),
        "note": "Brackets the two weeks before Samakal's 27 June 2026 report "
                "(about 3 km of erosion in two weeks, Chauhali) and the "
                "7 July 2026 reports (Kazipur). The river was rising, so some "
                "land-to-water change here will be seasonal flooding.",
    },
    {
        "id": "sep2026",
        "label": "September 2026 (latest imagery, monitoring mode)",
        "before": ("2026-08-28", "2026-09-10"),
        "after": ("2026-09-14", "2026-09-27"),
        "note": "How the tool would run routinely: the two most recent "
                "radar passes, about two weeks apart. We found no specific "
                "reported event to check this window against.",
    },
]

# ---------------------------------------------------------------------------
# Detection parameters (Sentinel-1 radar water mapping)
# ---------------------------------------------------------------------------
S1_POLARISATION = "VV"
SPECKLE_RADIUS_M = 30            # focal median to smooth radar speckle
# Calm-water check (water.pick_orbit): skip images whose darkest 10% of pixels
# (almost all water) are brighter than this: wind-roughened water looks like land.
# Calm images here measure -19.4 to -25 dB; a windy 30 Aug 2026 image, -17.5.
WINDY_WATER_DB = -18.5
OTSU_BOUNDS_DB = (-24.0, -12.0)  # sanity range for the automatic threshold
FALLBACK_THRESHOLD_DB = -18.0    # used if the automatic threshold looks wrong
MIN_PATCH_PIXELS = 10            # 10 px at 10 m = 0.1 ha minimum mapping unit
# Bank erosion is only reported for patches of at least 1 ha. This is a
# screening tool for large, visible change; smaller flips at the waterline
# are mostly pixel noise (wet sand and water look alike to radar).
MIN_EROSION_PATCH_HA = 1.0
# Only ESTABLISHED land can count as bank erosion: vegetated in the Jan-Mar
# 2026 dry season (Sentinel-2 NDVI >= min_ndvi and not wet, MNDWI < 0) and
# not water in any of the last `dry_years` Landsat dry-season maps. New water
# on sandbars and low, recently wet land next to the channel is reported as
# "flooded low land" instead: a rising river covers it and it comes back.
ESTABLISHED_LAND = {"min_ndvi": 0.2, "dry_years": 5}
# The AI must be confident on both dates for a pixel to count as changed:
# P(water) at most this before, and at least 1 - this after (or vice versa).
CHANGE_CONFIDENCE = 0.3
ANALYSIS_SCALE_M = 10

# AI water/sand/land classifier (detector/classifier.py). Trained on radar
# passes that coincide with clear-sky Dynamic World labels.
CLASSIFIER = {
    "training_period": ("2023-01-01", "2026-09-27"),
    "max_training_dates": 90,
    "test_every_nth_date": 4,        # every 4th date is held out for testing
    "points_per_class_per_date": 400,
    "trees": 100,
}

# Heuristic "possible extraction" flag. THIS IS NOT A CLASSIFIER. It picks
# new-water patches that are compact and regular, sit apart from the river
# channel, and are pit-sized, so a person can look at them. Natural ponds,
# oxbows and flooded low fields can all trigger it.
MINING_HEURISTIC = {
    "min_area_ha": 0.2,
    "max_area_ha": 10.0,
    "min_compactness": 0.55,  # 4*pi*A / P^2 (1.0 = perfect circle)
    "min_fill_ratio": 0.60,   # patch area / bounding-box area (regular shape)
    "max_dist_to_channel_m": 1500,  # mean distance of the patch's pixels
}

# A site gets a (simulated) alert when bank-attached erosion reaches this size.
ALERT_THRESHOLD_HA = 5.0

# ---------------------------------------------------------------------------
# Reference events, recorded as reported. Qualitative claims stay qualitative:
# no invented hectare numbers.
# ---------------------------------------------------------------------------
REFERENCE_EVENTS = [
    {
        "site": "kazipur",
        "places": ["Char Girish", "Khash Rajbari"],
        "as_reported": "Extensive erosion at Khash Rajbari and Char Girish: "
                       "houses, schools, trees and farmland lost to the river; "
                       "BWDB doing emergency protection work.",
        "reported_quantity": None,
        "reported_date": "2026-07-07",
        "brief_claim": "Active, ongoing Jamuna erosion as of September 2026.",
        "verification": "Location and erosion confirmed in reporting dated "
                        "7 July 2026 (Samakal, 27 June 2026 also names "
                        "Chargirish). We found no source dated September 2026.",
        "sources": [
            {"title": "Jamuna erosion intensifies in Sirajganj - The Business Standard, 7 Jul 2026",
             "url": "https://www.tbsnews.net/bangladesh/jamuna-erosion-intensifies-sirajganj-rising-river-waters-devour-homes-cropland-1481711"},
            {"title": "Jamuna erosion intensifies in Sirajganj - Daily Ittefaq (English), 7 Jul 2026",
             "url": "https://en.ittefaq.com.bd/17259/jamuna-erosion-intensifies-in-sirajganj"},
        ],
    },
    {
        "site": "chauhali",
        "places": ["Char Salimabad"],
        "as_reported": "Samakal, 27 June 2026 (Baghutia union, Chauhali, incl. "
                       "Char Salimabad): 'ongoing erosion across about three "
                       "kilometres has taken at least 30-40 structures - homes, "
                       "mosques, madrasas, schools, shops - into the river in "
                       "the past two weeks.' Earlier (NTV, June 2026): severe "
                       "erosion at Char Salimabad; BWDB placing geobags.",
        "reported_quantity": "about 3 km (extent of the eroding stretch), over about 2 weeks",
        "reported_date": "2026-06-27",
        "brief_claim": "About 3 km of riverbank lost at Char Salimabad over "
                       "about 10-15 days, September 2026.",
        "verification": "The '3 km in about two weeks' figure is sourced "
                        "(Samakal), but the report is dated 27 June 2026, not "
                        "September. It describes the length of the eroding "
                        "stretch, not an area, so we show it only as a length.",
        "sources": [
            {"title": "Samakal - ভাঙন আতঙ্কে নির্ঘুম রাত কাটছে যমুনা তীরবাসীর, 27 Jun 2026",
             "url": "https://samakal.com/whole-country/article/359319/"},
            {"title": "NTV Online - Jamuna rising, erosion at Char Salimabad (June 2026)",
             "url": "https://www.ntvbd.com/bangladesh/news-1751957"},
            {"title": "Dhaka Tribune (Bangla) - severe erosion in Sirajganj chars, 7 Jul 2026",
             "url": "https://bangla.dhakatribune.com/bangladesh/105470"},
        ],
        # OSM has a village "Salimabad" at this point, but tags it as part of
        # Nagarpur (Tangail), just across the channel. It may be the same
        # char; we have not confirmed it.
        "unverified_candidate_point": {"lat": 24.0420, "lon": 89.8236,
                                        "label": "OSM 'Salimabad' (unverified match)"},
    },
]

# ---------------------------------------------------------------------------
# News-reported erosion events, used by run_trackrecord.py to check the
# forecast (issued before each monsoon) and the detectors against reality.
#
# LOCATION PRECISION: reports name villages and unions, and OpenStreetMap has
# almost none of these villages. Each event is placed at the centre of the
# named UNION (OCHA / Bangladesh Bureau of Statistics admin-4 points, CC BY-IGO,
# data.humdata.org/dataset/cod-ab-bgd) and checked within EVENT_RADIUS_M of it.
# That is union-level precision, not village-level. Say so wherever it's shown.
# ---------------------------------------------------------------------------
EVENT_RADIUS_M = 3000
NEWS_EVENTS = [
    {"id": "kazipur-meghai-2023", "date": "2023-07-12", "upazila": "Kazipur",
     "union": "Kazipur", "lat": 24.6506, "lon": 89.6632,
     "what": "150 m of the BWDB embankment at Meghai collapsed within hours; "
             "upazila food godown, police station and schools at risk.",
     "note": "A protected embankment. The forecast doesn't know about protection works.",
     "sources": [{"title": "The Daily Star, 12 Jul 2023",
                  "url": "https://www.thedailystar.net/environment/climate-crisis/natural-disaster/news/jamuna-devours-150-metres-embankment-sirajganjs-kazipur-3367331"}]},
    {"id": "enayetpur-jalalpur-2023", "date": "2023-07-11", "upazila": "Shahjadpur / Enayetpur",
     "union": "Jalalpur", "lat": 24.1928, "lon": 89.6976,
     "what": "10 homesteads lost at Enayetpur in two days; over 100 houses in 15 days "
             "at Jamalpur, Brahmangram and Arkandi; erosion at Jalalpur and Hatpachil.",
     "sources": [{"title": "Dhaka Tribune, 11 Jul 2023", "url": "https://www.dhakatribune.com/320203"}]},
    {"id": "sonatani-2025", "date": "2025-05-24", "upazila": "Shahjadpur",
     "union": "Sonatani", "lat": 24.1194, "lon": 89.7155,
     "what": "Sonatani the worst-hit union; houses lost in recent weeks at Sayedpur, "
             "Koijuri, Haatpachil, Dhitpur and Kurshi.",
     "sources": [{"title": "The Daily Star, 24 May 2025",
                  "url": "https://www.thedailystar.net/environment/climate-crisis/natural-disaster/news/jamunas-erosion-takes-serious-turn-shahzadpur"}]},
    {"id": "chargirish-bhetua-2025", "date": "2025-07-13", "upazila": "Kazipur",
     "union": "Char Girish", "lat": 24.6966, "lon": 89.7543,
     "what": "About 100 houses lost at Bhetua village, Chargirish union.",
     "sources": [{"title": "BSS, 13 Jul 2025", "url": "https://www.bssnews.net/district/291771"}]},
    {"id": "kazipur-chars-2025", "date": "2025-09-06", "upazila": "Kazipur",
     "union": "Natuar Para", "lat": 24.6794, "lon": 89.6988,
     "what": "Over 100 homesteads in two weeks and about 200 houses in a month in "
             "Chorgirish, Natuarpara and Mansurnagar unions.",
     "note": "Three unions named; checked around Natuar Para, between the other two.",
     "sources": [{"title": "The Daily Star, 6 Sep 2025",
                  "url": "https://www.thedailystar.net/news/bangladesh/news/jamuna-river-erosion-leaves-thousands-homeless-sirajganjs-kazipur-3979746"}]},
    {"id": "chauhali-chandpur-2025", "date": "2025-09-24", "upazila": "Chauhali",
     "union": "Sadia Chandpur", "lat": 24.2481, "lon": 89.7438,
     "what": "Nearly 50 houses, cropland and trees lost at Chandpur as the river receded.",
     "sources": [{"title": "Dhaka Tribune, 24 Sep 2025",
                  "url": "https://www.dhakatribune.com/bangladesh/392296/jamuna-riverbank-erosion-devours-50-homes-in"}]},
    {"id": "baghutia-2026", "date": "2026-06-27", "upazila": "Chauhali",
     "union": "Bagutia", "lat": 24.0654, "lon": 89.7947,
     "what": "About 3 km of ongoing erosion took 30-40 structures in two weeks "
             "(Baghutia union, incl. Char Salimabad).",
     "sources": [{"title": "Samakal, 27 Jun 2026", "url": "https://samakal.com/whole-country/article/359319/"}]},
    {"id": "chargirish-2026", "date": "2026-07-22", "upazila": "Kazipur",
     "union": "Char Girish", "lat": 24.6966, "lon": 89.7543,
     "what": "Over 300 houses lost over the past month in at least 16 villages, from "
             "Chargirish Bazar to Bhetua.",
     "sources": [{"title": "The Daily Star, 22 Jul 2026",
                  "url": "https://www.thedailystar.net/news/bangladesh/news/jamuna-erosion-swallows-hundreds-homes-sirajganj-4229711"},
                 {"title": "The Business Standard, 7 Jul 2026",
                  "url": "https://www.tbsnews.net/bangladesh/jamuna-erosion-intensifies-sirajganj-rising-river-waters-devour-homes-cropland-1481711"}]},
    {"id": "khasrajbari-2026", "date": "2026-07-07", "upazila": "Kazipur",
     "union": "Khas Rajbari", "lat": 24.7427, "lon": 89.6837,
     "what": "Extensive erosion at Khash Rajbari: houses, schools, trees and farmland lost; "
             "BWDB emergency work.",
     "sources": [{"title": "The Business Standard, 7 Jul 2026",
                  "url": "https://www.tbsnews.net/bangladesh/jamuna-erosion-intensifies-sirajganj-rising-river-waters-devour-homes-cropland-1481711"}]},
]
