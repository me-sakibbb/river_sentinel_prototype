# Jamuna Bank Watch
### Forecast → detect → warn: riverbank erosion on the Jamuna, from free satellite data
*GP FutureMakers 2026 prototype. Study site: Kazipur and Chauhali upazilas, Sirajganj.*

## What this is, and what it isn't

Bangladesh already has an erosion forecasting system. Since 2004, **CEGIS**, working
with the **Bangladesh Water Development Board (BWDB)**, has published yearly
dry-season, satellite-based *predictions* of where the Jamuna, Ganges and Padma
are likely to erode. We don't try to replace that.

This prototype adds three things alongside it:

1. **Forecast (AI):** before each monsoon, one model trained on 37 years of
   dry-season Landsat maps of the Jamuna and the Ganges–Padma gives every 240 m
   stretch of vegetated bank a calibrated risk, and puts it on **High alert**,
   **Watch** or neither (`run_forecast.py`, `detector/forecast.py`,
   `detector/evaluation.py`).
2. **Detect (AI):** during the monsoon, a radar model measures bank change every
   Sentinel-1 pass, through cloud (`run_analysis.py`, `detector/classifier.py`).
3. **Warn (simulated):** a risky stretch where radar sees erosion starting
   nearby is escalated. Residents in the at-risk cells get a Bangla SMS or voice
   call, and their replies ("erosion has started" / "not yet") become new
   ground-truth labels.

| | CEGIS / BWDB prediction | EWS-RE (Frontiers 2025) | This prototype |
|---|---|---|---|
| Method | Probabilistic, from dry-season images | Delft3D physics model with seasonal flow forecasts | Machine learning on 37 years of dry-season Landsat maps, plus radar every pass |
| Cycle | Once a year | Seasonal (4-month lead time) | Yearly forecast, **updated every radar pass (about 12 days)** |
| Unit | Erosion-prone zones | Model grid | **240 m bank cells**, each with its reasons |
| Tested skill | Not published per cell | 70 % in 2023 field tests | AUC 0.911 on 5 unseen monsoons (0.898-0.935 per year); High-alert precision 45 %, stated with its false alarms (below) |
| Delivery | Report / posters | Research system | Bangla SMS/voice to people in the cell (**simulated**) |
| Mining | Not covered | Not covered | Heuristic flag on pit-like shapes, **for human review** |

It complements local reporting and BWDB field checks. It doesn't replace them, and
it doesn't "solve" erosion.

## How it works (one sentence)

> **We taught the computer to read radar images, which see through monsoon cloud,
> by showing it days when an ordinary camera could also see clearly. It marks
> water, sand and land in two satellite passes, and we map where land turned into
> water right at the river's edge.**

## The erosion forecast (`detector/forecast.py`, `detector/evaluation.py`)

> **We showed the computer 37 years of dry-season satellite maps of the Jamuna
> and the Ganges–Padma, and it learned what a stretch of land looks like the
> year *before* the river takes it.**

- **Maps:** one Landsat water map per dry season (Jan-Mar), 1990-2026, 30 m
  pixels, for four reaches: the Jamuna from the Indian border to Aricha (the
  pilot, Kazipur and Chauhali, is in it), and the Ganges–Padma from Rajshahi
  to Chandpur. A pixel counts as open water if its water index (MNDWI) is
  above 0 **and** it has no vegetation (NDVI below 0). Without the second
  rule, flooded boro rice fields doubled the "water" in some years.
- **What is predicted:** each 240 m cell of *vegetated* land within 1 km of the
  river in year t. Does at least 1 ha of it (fields, homesteads, trees) become
  **part of the river** by the next dry season? Water that isn't connected to
  the river (a flooded field, a pond) doesn't count. Neither do bare sandbars,
  because small differences in dry-season water level make them come and go.
- **What the model sees (only maps up to year t):**
  - distance to the river and to a wide main channel
  - how fast any channel, and the main channel, is approaching (1- and 3-year)
  - width of the nearest channel (narrow side channels erode banks too)
  - erosion nearby last year, in the last 3 years within 1 km, and in how many
    of the last 5 years
  - open water around and upstream of the cell
  - how braided the river is around it
  - how old the land is (young chars erode fastest); char or mainland; vegetation
- **Model:** one set of gradient-boosted trees (scikit-learn
  `HistGradientBoostingClassifier`) for all four reaches. There is no model
  per river.

### Evaluation protocol and leakage safeguards

Everything below is fixed in `detector/evaluation.py`. `python evaluate.py`
reproduces it offline from the cached maps, and `python -m pytest` runs the
checks.

| Years | Role |
|---|---|
| 1995-2016 | **Train** |
| 2017-2020 | **Validation.** Every choice is made here: which inputs to keep, model settings, probability calibration, alert thresholds. Each validation year is forecast by a model trained only on earlier years. |
| 2021-2025 | **Test.** Scored once, by a model trained on 1995-2020 with the frozen choices. |
| 2026 | **Live forecast**, from a model trained on every year with a known outcome. |

- **No future inputs.** Version 1 had three leaks, found in review and now fixed:
  - Gaps in the maps were filled from later years.
  - The river corridor (which decides which cells are scored) was drawn from all
    years, 2021-2026 included.
  - The "water upstream" input wrapped around the map edge.

  `tests/test_forecast.py` replaces every map after year t with a different river
  and checks that the inputs for year t don't change at all. That test fails on
  version 1's code.
- **Every new input had to earn its place** on the validation years, by raising
  validation average precision by at least 0.003:
  - version 1 inputs: 0.432
  - \+ side channels: 0.439 (kept)
  - \+ water 2 km upstream: 0.440 (dropped)
  - \+ erosion history: 0.451 (kept)
  - \+ river context: 0.455 (kept)

  On the Jamuna alone, none of the new inputs passed. With four reaches, three
  did.
- **Shuffled-label check:** trained on shuffled labels, the model has no skill
  (AUC 0.45-0.55).
- **One caveat on the test years.** Version 1's results (which covered some of
  2021-2025) were seen before the version 2 inputs were *proposed*. Whether
  each input was *kept* was decided only on the validation years, as above,
  and the test was then scored once. The fully blind check is the 2026
  forecast, scored against the Jan-Mar 2027 map.

### Results on the unseen monsoons 2021-2025

| Test years 2021-2025 | AUC | Lost land in riskiest 10 % of bank | …in riskiest 20 % |
|---|---|---|---|
| **AI forecast, all 4 reaches** (201,895 cell-years) | **0.911** | **64 %** | **83 %** |
| AI with version 1's inputs, same data | 0.906 | 63 % | 82 % |
| Rule: "where it eroded last year" | 0.859 | 53 % | 71 % |
| Rule: "land closest to the river" | 0.830 | 36 % | 63 % |
| **AI forecast, Jamuna pilot reach** | **0.912** | **64 %** | **83 %** |
| **AI forecast, Kazipur + Chauhali** | **0.879** | **57 %** | **75 %** |
| *Version 1 as first published: Jamuna reach / Kazipur + Chauhali* | *0.904 / 0.871* | *61 % / 56 %* | *81 % / 74 %* |

The AI beats both rules in every test year (AUC 0.898-0.935 vs 0.844-0.884 for
the better rule). The gain over version 1's inputs on the same data is small
(AUC +0.005). Most of what changed is that the numbers are now leak-free and
cover two rivers.

**One model for more than one river.**

| Tested on | AUC, river never seen in training | AUC, trained with it |
|---|---|---|
| Ganges–Padma | 0.912 | 0.923 |
| Jamuna | 0.892 | 0.901 |

This supports the national, one-model design (see `MODEL_BLUEPRINT.md`).

### False alarms, stated plainly

Only about 4.4 % of scored cells erode in a given year, so any forecast raises
false alarms. Two alert tiers, with thresholds fixed on the validation years:

| Test years, all reaches | Share of bank flagged | Flagged cells that eroded | …or a neighbouring cell did | Lost land caught | False alarms / year |
|---|---|---|---|---|---|
| **High alert** (risk ≥ 33 %) | 3.6 % | **45 %** | 60 % | 37 % | 805 (of 1,456 alerts) |
| High alert + Watch (risk ≥ 6.4 %) | 21 % | 17 % | 32 % | **84 %** | 7,140 |
| High alert, Kazipur + Chauhali | 5.0 % | 40 % | 52 % | 39 % | 107 (of 176) |

- **The precision target was missed on the test years.** The High-alert
  threshold was set to reach 50 % precision on the validation years, and did
  (50.4 %). On the test years it reached 45 %. We report that rather than
  re-tune on the test years.
- **Only High alert would trigger the (simulated) SMS.** Watch is for
  officials, to plan field checks.
- **Risks are calibrated** (isotonic regression, fitted on the validation
  years), but run slightly high. Stretches given ~41 % lost land 38 % of the
  time; the top band (~81 %) came in at 68 %.

**Live 2026 forecast:** 994 High-alert and 5,607 Watch stretches across the four
reaches.

**Early check against this monsoon (inconclusive).** The dry-season forecast
scores vegetated land, while radar during a rising river also sees flooded low
land. The real test is the Jan-Mar 2027 dry-season map.

**Short-term warning (prototype, lead time not yet validated).** On every new
radar pass, High-alert or Watch stretches with radar-**confirmed** bank erosion
on established land within 500 m are **escalated**. Next step: add FFWC /
Google Flood Hub river-level forecasts, because banks often collapse as the
water *falls* after a peak.

## Track record: forecasts vs news-reported events (`run_trackrecord.py`)

We took erosion events **reported in the news** (2023-2026, see
`config.NEWS_EVENTS` for sources) and checked what the forecast said *before*
each monsoon, and what the satellites measured afterwards. Every forecast was
issued blind. For 2023-2025 the model had only learned from 1995-2020; for
2026, only from earlier years.

| Reported | Place (union) | Forecast before: area flagged | Measured afterwards | Lost land in flagged cells |
|---|---|---|---|---|
| 12 Jul 2023 | Kazipur (Meghai embankment) | 29 % | 30.1 ha (Landsat) | **100 %** |
| 11 Jul 2023 | Jalalpur (Enayetpur) | 17 % | 17.0 ha (Landsat) | **86 %** |
| 24 May 2025 | Sonatani, Shahjadpur | 38 % | 77.7 ha (Landsat) | **100 %** |
| 13 Jul 2025 | Char Girish, Kazipur | 22 % | 0 ha of farmland | not scored |
| 6 Sep 2025 | Natuar Para, Kazipur | 26 % | 5.7 ha (Landsat) | **95 %** |
| 24 Sep 2025 | Sadia Chandpur, Chauhali | 44 % | 49.1 ha (Landsat) | **100 %** |
| 27 Jun 2026 | Bagutia, Chauhali | 15 % | no loss of established land seen by radar within 3 km | not scored yet |
| 22 Jul 2026 | Char Girish, Kazipur | 7 % | 18.9 ha (radar, established land) | **0 %: missed** |
| 7 Jul 2026 | Khas Rajbari, Kazipur | 1 % | 45.4 ha (radar, established land) | **0 %: missed** |

"Flagged" means on the High-alert or Watch list. "Area flagged" is the
comparison: if the forecast were guessing, the share of lost land in flagged
cells would be about the same as the share of area flagged. **The 2026 Kazipur
misses are real, and version 2 did not fix them.** There the river runs in
narrow side channels in the dry season. The new side-channel inputs helped on
average (they were kept on the validation years), but not at these two places.
We'll recheck against the Jan-Mar 2027 dry-season map.

**Location precision is union-level.** News names villages that aren't on open
maps, so each event is checked within 3 km of its union's centre (OCHA /
Bangladesh Bureau of Statistics union points, CC BY-IGO,
`data/boundaries/unions_hdx.json`). The page also shows four "predicted and
happened" case studies from 2021-2025, with before/after Sentinel-2 images.
They are *chosen* as clear examples, and one clear miss is shown next to them;
the fair overall score is the table in the forecast section.

## The radar detector (`detector/classifier.py`)

A **Random Forest** (100 trees, run in Earth Engine) classifies each 10 m radar
pixel as **water**, **sand** or **land**.

- **Where the labels come from.** We had no hand-labelled data. On clear days,
  Sentinel-2 photos can see the ground, and Google's **Dynamic World** (a
  published deep-learning land-cover model) has already labelled each pixel.
  We pair those labels with Sentinel-1 radar passes taken within a day, and
  keep only pixels where Dynamic World is at least 60 % confident. The model
  learns to produce the same labels from radar alone, so it still works on
  cloudy monsoon days.
- **Flooded crop fields are labelled "land" on purpose.** They haven't eroded,
  and this gives the model a chance to learn not to count them.
- **Inputs:** VV and VH backscatter, their difference and local texture, plus
  brightness **relative to each image's own water/land cut-off** (Otsu),
  relative to its darkest 10 %, and averaged over 100 m around the pixel.
  Radar brightness drifts from date to date (wind, moisture). The first
  version saw only absolute brightness, so it learned the training dates'
  cut-off and **scored below the plain threshold on new dates (88.7 % vs
  91.4 %)**. With the relative inputs it scored 90.6 % vs 88.0 % on 10 held-out
  dates. Version 2 learns from **90 date pairs from 2023 to 2026** (68 train,
  22 held out; 120 pixels per class per date, which keeps each Earth Engine
  request under its 10 MB limit). On the **22 unseen dates** it scores
  **90.9 % vs 87.4 %** for the plain threshold (water / sand / land: 86.3 %).
  The radar viewing angle was dropped: in a first version the model
  leaned on it most, learning which orbit the training scenes came from rather
  than what water looks like, and it failed on images from a different orbit.
- **Only confident change counts.** A pixel counts as "land turned to water"
  only if the model gives it at most 30 % chance of water before and at least
  70 % after. Uncertain pixels are left out. Bank-erosion patches must be at
  least 1 ha.
- **Honest testing.** Every 4th date is held out entirely and never used for
  training. The model is scored on those unseen dates, next to the plain
  dark-pixel threshold. Both scores, the test dates and what the model relies
  on most are shown in the dashboard. We don't quote any accuracy that wasn't
  measured this way.
- **Fallback.** `--method threshold` runs the original non-AI method (step 2
  below). The dashboard also shows the threshold method's raw land-to-water
  area as a cross-check.

Pipeline details, all in `detector/`:
1. **Imagery:** Sentinel-1 GRD (VV polarisation, IW mode) from Google Earth
   Engine. For each time window, we take the median of the scenes from **one
   orbit track**, so both dates have the same viewing geometry. Radar speckle is
   smoothed with a 30 m median filter. We use radar rather than optical imagery
   because almost every Sentinel-2 image from June to October is cloudy.
   Sentinel-2 is still shown when a clear-ish image exists, plus a dry-season
   true-colour reference.
   **Calm-water check:** wind roughens water so radar sees it as brighter,
   close to land. For each candidate orbit, we measure how dark the darkest 10 %
   of pixels (almost all water) are, and skip image pairs where that is above
   -18.5 dB. In testing, this rejected a windy 30 Aug 2026 image (-17.5 dB) that
   had produced thousands of hectares of false "erosion".
2. **Water vs land:** by the AI classifier above (default). The non-AI baseline
   gives each image its own dark/bright cut-off from **Otsu's
   method**. It falls back to -18 dB if the result is outside -24..-12 dB. The
   threshold used is shown in the dashboard.
3. **Change:** land→water and water→land patches smaller than 0.1 ha are dropped.
   - land→water on **established land** at the old channel = **bank erosion**.
     Established land was vegetated in the Jan-Mar 2026 Sentinel-2 image
     (NDVI ≥ 0.2, not wet) and was not water in any of the last 5 Landsat dry
     seasons. A strip of bare sand up to ~30 m wide between it and the water
     is allowed.
   - land→water on **sandbars or low, recently wet land** at the channel =
     **flooded low land**. It is shown separately and **not counted as
     erosion**. A rising river covers this land and it comes back. In version 1
     it was the biggest source of false "erosion" (Kazipur, June 2026: 463 ha).
   - land→water **not touching the channel** = **isolated new water** (usually
     flooding; *not* counted as erosion)
   - water→land = **accretion** (new sandbar/char)
4. **Length measure:** the number of eroded pixels in the first row behind the
   old waterline, × 10 m, gives an approximate "km of shoreline that moved". The
   reported event is described as a length (about 3 km), so we compare length to
   length instead of turning it into hectares.
5. **Possible-extraction flag (heuristic):** isolated new-water patches that are
   0.2-10 ha, compact (4πA/P² ≥ 0.55), regular (≥ 60 % of their bounding box)
   and within 1.5 km of the channel. **This marks things for a person to look
   at. It is not a finding that mining took place.**
6. **Alert (simulated):** if bank erosion in a site reaches 5 ha, the script
   writes and prints an alert addressed to roles (BWDB O&M division, union
   parishad, UNO). **Nothing is sent.** There's no SMS or telecom integration.

All thresholds are in `config.py`.

## Study area: why not the brief's bounding box

The brief gave a rough box of 89.60-89.80 E, 24.00-24.70 N. OpenStreetMap's upazila
boundaries show that box cuts off parts of both sites: Chauhali runs east to
about 89.83 E, and Kazipur runs north to about 24.79 N. So we analyse each
**upazila polygon plus a 1 km buffer** (`data/boundaries/`, OSM relations
14326604 and 14326610).

We **could not verify exact coordinates** for Char Girish, Khash Rajbari or Char
Salimabad. OSM has no entries for the first two. It has one "Salimabad" village
(24.042 N, 89.824 E), but tags it as part of Nagarpur, Tangail, across the
channel. It may be the same char, but we haven't confirmed that. It's shown on the
map as an *unverified candidate*. Results are therefore reported **per upazila**,
not per char.

## Reference events, and a correction to the brief

We checked the brief's reference events against published reporting. Both places
are real and did erode. **But the dates we found are June-July 2026, not September.**

| Brief says | What the sources say |
|---|---|
| Kazipur, Sept 2026: active erosion at Char Girish and Khash Rajbari | Extensive erosion at both, reported **7 July 2026** ([TBS](https://www.tbsnews.net/bangladesh/jamuna-erosion-intensifies-sirajganj-rising-river-waters-devour-homes-cropland-1481711), [Ittefaq](https://en.ittefaq.com.bd/17259/jamuna-erosion-intensifies-in-sirajganj)). No area figure. |
| Chauhali, Sept 2026: about 3 km of bank lost at Char Salimabad in about 10-15 days | Samakal, **27 June 2026**: *"about three kilometres"* of ongoing erosion took at least 30-40 structures *"in the past two weeks"* (Baghutia union, incl. Char Salimabad) ([Samakal](https://samakal.com/whole-country/article/359319/)). Also NTV, June 2026. |

So the main validation window is **1 June → 8 July 2026** (`jun2026`). A second run,
`sep2026`, uses the latest imagery the way the tool would run routinely. There's no
specific reported event to check it against.

## Running it

**Reproduce the forecast scores without Earth Engine** (needs the per-reach
`data/forecast/<reach>/table.npz` files):

```powershell
.venv\Scripts\python -m pytest          # leakage, gap-filling and change-detection tests
.venv\Scripts\python evaluate.py        # the whole train / validate / test protocol
```

**Full pipeline (needs Earth Engine):**

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# One-time, interactive: opens a browser to log in to Google / Earth Engine.
.venv\Scripts\earthengine authenticate

# You need a Google Cloud project registered for Earth Engine
# (free for non-commercial use: https://code.earthengine.google.com/register)
.venv\Scripts\python run_analysis.py --project YOUR-PROJECT-ID
```

This writes `web/data/` (PNGs plus `results.js`). The radar classifier's
training pixels are cached in `data/training_samples.json` (the exact
labelled pixels behind the reported scores), so re-runs skip the slow
sampling step. Then run the forecast. The
first run downloads 148 Landsat dry-season maps (4 reaches × 1990-2026) into
`data/forecast/<reach>/`, about 1-2 hours. Building the tables and running the
full protocol takes about another hour:

```powershell
.venv\Scripts\python run_forecast.py --project YOUR-PROJECT-ID
# after new radar results, refresh the dashboard without re-training:
.venv\Scripts\python run_forecast.py --export-only
```

It writes `web/data/forecast.js`, which the dashboard shows as its first view.
Finally, `run_trackrecord.py --project YOUR-PROJECT-ID` checks the forecast
against the news events and writes `web/data/trackrecord.js` plus images, and
`run_showcase.py --project YOUR-PROJECT-ID` draws the predicted-vs-actual
pictures for the dashboard's compare view. Open `web/index.html` directly in
a browser; it doesn't need a server. Use `--run jun2026` or `--site chauhali` to
re-run just one part.

## Known limitations

- **10 m pixels:** this screens for large, visible change. Small or early-stage
  extraction pits may not be detected.
- **The extraction flag is a heuristic proxy,** not a validated classifier.
  Oxbow ponds, borrow pits for roads and flooded low fields can all trigger it.
  Every flag needs a person, and ideally a field visit, to judge it.
- **Monsoon flooding is the biggest confound.** When the river is rising, low char
  land next to the channel floods, and radar sees that as water. Version 2 only
  counts erosion on *established* land and reports flooded low land separately.
  The price is that loss of very young chars (water within the last 5 dry
  seasons) isn't counted as erosion, even when it is real.
- **False alarms are real and counted.** In the test years, 55 % of High-alert
  stretches didn't lose a full hectare in their own 240 m cell (40 % if a
  neighbouring cell counts). Use the alerts to prioritise checks, not as an
  evacuation order on their own.
- **The AI learned from optical labels, not ground truth** (Dynamic World plus
  water/vegetation indices on clear days). Their mistakes carry over, and the
  test scores are measured against the same kind of labels. Clear-sky training and test days are mostly outside the monsoon, so
  monsoon accuracy may be lower than the reported score.
- **Validation is thin:** a few already-reported events, not a statistically
  representative benchmark. It shows the method *can* work. It doesn't show how
  accurate it is.
- **Char-level locations are unverified,** so comparisons are per upazila. The
  only OSM "Salimabad" point lies *outside* the Chauhali boundary we analyse, on
  the Tangail side. If that's the reported char, some of the reported erosion
  falls outside our study area, which may explain part of the shortfall
  against the reported ~3 km.
- **Wind:** rough water looks like land to radar. Windy images are skipped
  automatically (see the calm-water check), but if every image in a window is
  windy, results for that window are flagged as unreliable.
- **Forecast limits:** bank protection works (geobags, revetments) aren't in the
  data, so protected banks can be over-flagged. Yearly dry-season maps say
  *where* land is likely to go this monsoon, not the day. Five test years is a
  small sample, and the river keeps changing. Two rivers are not yet "all of
  Bangladesh": meandering, tidal and flashy northern rivers are still untested
  (`MODEL_BLUEPRINT.md`).
- **Version 2 counts far less monsoon "erosion", on purpose.** The
  established-land rule moves flooded sandbars and low land into their own
  class:

  | Window | Version 1 | Version 2 bank erosion | Flooded low land (not counted) |
  |---|---|---|---|
  | Kazipur, Jun-Jul 2026 | 463 ha | 236 ha | 319 ha |
  | Chauhali, Jun-Jul 2026 | 105 ha | 22 ha | 234 ha |
  | Kazipur, Sep 2026 | 26 ha | 0 ha | 25 ha |
  | Chauhali, Sep 2026 | 48 ha | 1 ha | 93 ha |

  Some of the flooded low land may still be lost for good. The next dry-season
  map will tell.
- **The alert system is simulated end to end.**
- The dashboard stretches lon/lat image overlays onto a Web-Mercator map, so edges
  can be off by a few tens of metres.

## Layout

```
config.py            sites, time windows, thresholds, reference events (with sources)
run_analysis.py      runs every site x window, writes web/data/
detector/water.py    Sentinel-1 composite, orbit choice, Otsu water threshold
detector/classifier.py  AI: Random Forest water/sand/land classifier, training and held-out test
detector/forecast.py AI: river reaches, 37-year Landsat history -> per-cell erosion-risk inputs (leak-free)
detector/evaluation.py  AI: train / validate / test protocol, calibration, alert tiers, false alarms
evaluate.py          reruns the forecast evaluation offline from cached maps
run_forecast.py      downloads maps, runs the evaluation, writes web/data/forecast.js
run_trackrecord.py   forecasts vs news-reported events, case-study pictures
run_showcase.py      predicted-vs-actual overlays for the dashboard's compare view
tests/               pytest: no-future-leakage, gap filling, change classification
detector/change.py   change classes, patch shapes, extraction heuristic, totals
detector/imagery.py  image rendering (radar, optical, masks, change overlay)
detector/alert.py    SIMULATED alert text (incl. Bangla pre-monsoon SMS/voice warning)
data/boundaries/     upazila polygons (OpenStreetMap, ODbL)
web/                 static dashboard (Leaflet)
```

## License and data

Code: MIT (see `LICENSE`). Data: Copernicus Sentinel-1/2 (ESA) and Landsat
5/7/8/9 (USGS/NASA) via Google Earth Engine; boundaries © OpenStreetMap
contributors (ODbL); union points OCHA / Bangladesh Bureau of Statistics
(CC BY-IGO).
