# Jamuna Bank Watch
### Forecast → detect → warn: riverbank erosion in Bangladesh, from free satellite data
*GP FutureMakers 2026 prototype. Pilot site: Kazipur and Chauhali upazilas, Sirajganj.*

**Live dashboard: https://me-sakibbb.github.io/river_sentinel_prototype/**

Every monsoon the Jamuna and Padma take thousands of hectares of homes and
farmland. This prototype tells people **which stretch of bank is likely to go
next**, so they can act before it does. It works alongside the yearly CEGIS /
BWDB erosion predictions; it doesn't replace them.

## What we built

1. **Forecast (AI).** One gradient-boosted model, trained on 37 years of
   dry-season Landsat maps (1990–2026) of the Jamuna and Ganges–Padma, gives
   every 240 m stretch of riverside farmland a risk score before each monsoon
   and puts it on **High alert**, **Watch** or neither.
2. **Detect (AI).** During the monsoon, a Random Forest reads Sentinel-1 radar,
   which sees through cloud, and maps where established land turned into river
   every pass (about 12 days).
3. **Warn (simulated).** A High-alert stretch where radar sees erosion starting
   nearby is escalated, with a Bangla SMS / voice message to people there.
   **Nothing is actually sent.**
4. **Dashboard.** A static map (`web/index.html`) showing this monsoon's risk,
   the latest radar change, the track record and predicted-vs-actual pictures.

## Results (tested on 2021–2025, monsoons the model never saw)

| | |
|---|---|
| Land lost that fell in the riskiest 20 % of bank | **82 %** (rules of thumb: 63–71 %) |
| Ranking score (AUC) | **0.91** (0.898–0.935 every year; rules: 0.83–0.86) |
| Chance a High-alert stretch loses ≥ 1 ha | **45 %**, 10× the 4.4 % average |
| …that stretch or the one next to it | **60 %** |
| River left out of training entirely | AUC 0.89–0.91, almost no loss |
| Radar water map on 22 unseen dates | **90.9 %** correct vs 87.4 % for a plain threshold |

Of six erosion events reported in the news in 2023–2025, the forecast had
flagged the place beforehand in five; the sixth had no measurable farmland
loss. **Two 2026 events at Kazipur were missed.**

## How we kept it honest

- **Fixed split:** train 1995–2016, choose everything on 2017–2020, test once on 2021–2025.
- **No look-ahead:** a test replaces every later map with a different river and
  checks that the model's inputs don't change.
- **Shuffled labels give no skill** (AUC ≈ 0.5), so the score isn't an artefact.
- **False alarms are reported**, not hidden: about 800 a year across four reaches.

## Limits

- Bank protection works (geobags, revetments) aren't in the data, so protected banks may be over-flagged.
- It says *where*, not *which day*.
- Five test years is a small sample.
- Tested on two rivers so far, not yet all of Bangladesh.
- The radar model learned from optical labels on clear days, not field surveys.

## Try it

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest      # leakage and change-detection tests
.venv\Scripts\python evaluate.py    # reproduces every score above, offline
```

Open `web/index.html` in a browser for a local copy of the dashboard. Re-downloading imagery
needs a Google Earth Engine project; see the technical details.

## More

- [`docs/TECHNICAL.md`](docs/TECHNICAL.md): full method, every table, the pipeline and how to run it
- [`MODEL_BLUEPRINT.md`](MODEL_BLUEPRINT.md): how this scales to one national model for every river

Code: MIT (`LICENSE`). Data: Copernicus Sentinel-1/2 (ESA), Landsat (USGS/NASA)
via Google Earth Engine; boundaries © OpenStreetMap contributors.
