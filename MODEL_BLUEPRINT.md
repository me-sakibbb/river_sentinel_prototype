# Blueprint: one AI model to detect and predict river erosion across Bangladesh

This document describes the system we would build to **track and forecast bank erosion on every
erosion-prone river in Bangladesh**, with **no limits on data, computing power or partnerships**.

It is **one national model**. There is one set of trained weights and one pipeline, and it runs on
every river: the braided Jamuna, the meandering Padma and Arial Khan, the tidal lower Meghna, the
flashy Teesta and Dharla, and the smaller rivers in between. It learns how rivers in general erode,
and reads each river's own character from its inputs.

The document covers:
- where accuracy is lost today, and why
- how a single model can handle very different rivers
- how it works, step by step, for **detection** (what the river took) and **prediction** (what it
  will take next)
- how we would **prove** it beats other methods, including on rivers it was never trained on

The Jamuna prototype in this repository is the **pilot**: the first reach where the approach was
built and tested blind. Every external claim links to its source at the end.

> **A note on "beating all other methods."** Nobody can promise that before testing. What we can do
> is define a fair, public benchmark in advance: same rivers, same years, same labels, forecasts
> issued blind. Then we require the model to win on it, river by river and year by year. Section 8
> describes that benchmark. If the model doesn't win somewhere, the benchmark will show where.

---

## 1. Summary

Bangladesh has an estimated **1,200 km of erosion-prone riverbank, more than 500 km of it severe**.
Erosion happens every year in over 50 of its 64 districts. The worst-hit rivers include the Jamuna,
Padma, Meghna, Teesta, Arial Khan and Gorai. Today's forecasts cover a few major rivers, reach by
reach, using a lot of expert time. A national model can score **every bank segment of every river,
every season, the same way**.

The design:

1. **One detector for the whole country.** A multimodal, multi-temporal Earth-observation
   foundation model, fine-tuned once on labelled images from *all* river types. It maps water, sand,
   vegetated land and settlements every few days, through monsoon clouds, on rivers from 30 m to
   15 km wide.
2. **One forecaster for the whole country.** A single spatiotemporal network with one set of
   weights. It predicts the erosion probability and retreat distance for any bank segment in the
   country. It takes three kinds of input:
   - the recent history of the river around that bank
   - **physics signals** computed the same way everywhere, from satellite-measured width, slope
     and discharge
   - a **river-context description** (how braided, how sinuous, how tidal, how big, how flashy),
     which is how it adapts to each river without separate training
3. **Physics built in.** A single neural *emulator* of river
   hydrodynamics, trained on detailed simulations from many rivers, supplies near-bank flow and
   bank-stability estimates anywhere.
4. **Calibrated, honest outputs.** Probabilities that mean what they say, retreat ranges with
   guaranteed coverage, and a separate "channel may switch" layer for the part of erosion that is
   genuinely random.
5. **Tested on unseen rivers.** Besides testing on future years, whole rivers are held out of
   training. That shows the model will work on a river before we have many labels from it.

It issues forecasts at three horizons: **days** (warning that erosion is starting), **the coming
monsoon** (issued each dry season), and **1–5 years** (planning embankments and relocation).

---

## 2. How one model handles every river

**Why it can work.**
- Erosion follows **the same physics everywhere**: water pushes on a bank, and it gives way when the
  push exceeds its strength. A model that learns from all rivers learns that shared process, not one
  river's quirks.
- Pooling every river gives the model **far more erosion examples** than any single river offers,
  which matters most for small rivers with little history.
- One model gives **one comparable score for every bank segment in the country**, so it can answer
  "where should the limited geobags go this year?"

**River context as input.** River type isn't hard-coded. It's an input. Every bank
segment carries a context vector:

| Context feature | Where it comes from | What it tells the model |
|---|---|---|
| Channel width, braiding index, sinuosity | Computed from the detector's water maps | Braided (Jamuna) vs meandering (Padma, Arial Khan) vs single-thread small rivers |
| Water-surface slope, discharge | SWOT satellite (rivers ≥100 m), gauges, Flood Hub | How much force the flow has |
| Tidal range and tide phase at image time | Tide gauges, tide models | Tidal rivers (lower Meghna, the southern delta) behave differently |
| Flashiness (how fast the river rises and falls) | Gauges, Flood Hub, rainfall | Teesta/Dharla-style flash floods vs slow monsoon rise |
| Bank material class | Soil maps, borehole logs where available | Sandy vs silty/clayey banks |
| Upstream control | Barrages, dams, confluences | Managed flows and backwater effects |

Because these are inputs, a **new river is handled the moment its context is measured**. The model
recognises "a sandy, braided, fast-rising river" from what it learned on others. The river-held-out
tests in §8 check that this really works.

---

## 3. What limits accuracy today

Each problem below is fixed by a named part of the design.

| # | Source of error | What it does | Fixed by |
|---|---|---|---|
| E1 | **Water-level differences between images**, including tides | A higher river or a high tide looks like erosion | Stage- and tide-aware detection (§5) |
| E2 | **Clouds in the monsoon** | Optical satellites go blind exactly when erosion happens | Radar-first detection: Sentinel-1, NISAR, commercial radar (§4, §5) |
| E3 | **River size vs pixel size** | 30 m pixels can't see a 60 m-wide river's bank | Multi-resolution input: 30 m, 10 m, 3 m (§4, §5) |
| E4 | **Noisy or missing labels** | Few examples on small rivers; mistakes in training maps | Nationwide expert annotation, field surveys, active learning (§5) |
| E5 | **No flood information** | A mild monsoon and a record flood get the same forecast | Discharge and stage forcing everywhere via gauges, Flood Hub, SWOT (§4, §6) |
| E6 | **No underwater or bank information** | Scour and weak banks can't be seen from space | Physics emulator + surveys where they exist (§6) |
| E7 | **No human interventions** | Geobags, revetments, dredging and sand mining change where erosion happens | National protection-works and sand-mining inventories (§4) |
| E8 | **Only one time step per year** | Misses when during the monsoon a bank goes | Monsoon radar time series, nowcast horizon (§6) |
| E9 | **Genuine randomness** | New channels and thread switches no model has foreseen | Stochastic switching layer, reported honestly (§6, §8) |
| E10 | **Transfer to new rivers** | A model that works on one river may fail on another | River-context inputs, training on all rivers, leave-one-river-out tests (§2, §8) |

### What the pilot already shows

On the Jamuna pilot reach, the prototype's forecast (`detector/forecast.py`, `run_forecast.py`)
scores **AUC 0.904** on 2021–2025, years it never saw. Its riskiest 20% of bank caught **81% of the
land actually lost**. It beats both simple rules:
- "closest to the river": AUC 0.835, 65% caught
- "eroded last year": AUC 0.856, 67% caught

It is weak where E2, E3 and E9 dominate. In Kazipur, only **3.5%** of the erosion the radar detected
in June 2026 fell in the forecast's riskiest 20% of cells. Most of those patches weren't near any
scored cell, because they were on bare chars or far from the dry-season water.

The pilot also uses **nothing Jamuna-specific in principle**: water maps, distances, recent erosion,
land age, vegetation. That is why its features can become the core of the national model.

---

## 4. The data layer (national, no limits)

| Group | Sources | Why it matters |
|---|---|---|
| **River network** | A national river centre-line and bank-segment network (built from the detector's maps, aligned with SWORD, the SWOT river database) | Every prediction has a fixed address: river → reach → 100 m bank segment |
| **Optical history** | Landsat 5/7/8/9 (1984→), Sentinel-2 (2015→), NASA HLS (harmonized 30 m), JRC Global Surface Water | 40 years of how every large river behaves (E4, E9) |
| **Optical high-res** | PlanetScope (~3 m, daily), sub-metre tasking | Small rivers, exact bank edges, settlement loss, checking labels (E3, E4) |
| **Radar** | Sentinel-1A/C (C-band; 1C launched Dec 2024), **NISAR L-band** (public data from July 2026), commercial X-band (ICEYE, Capella) in floods | Sees through monsoon cloud (E2, E8) |
| **River height, slope, discharge** | **SWOT** (water-surface elevation, slope, width and discharge for reaches ≥100 m wide); FFWC/BWDB gauges; **Google Flood Hub** (AI forecasts up to 7 days, incl. virtual gauges); GloFAS; ECMWF seasonal outlooks | Flood forcing for every river, including ungauged ones (E5) |
| **Tides and surges** | BIWTA/BWDB tide gauges, global tide models, cyclone surge forecasts | Tidal rivers of the south (E1) |
| **Under water and bank** | BWDB/CEGIS cross-sections and bathymetry where they exist; multibeam at hotspots; borehole logs; jet-erosion tests; drone LiDAR | Training and checking the physics emulator (E6) |
| **Human** | BWDB protection-works inventory with dates (all divisions); dredging and sand-mining permits; barrages and regulators; Google Open Buildings; WorldPop | Where rivers are held back, and who is at risk (E7) |
| **Global rivers** | REAL global bank erosion/accretion dataset (rivers >150 m); GSW for large rivers worldwide | Pre-training on far more erosion examples than Bangladesh alone has (E4, E10) |
| **Ground truth** | Erosion pins, RTK-GNSS bank surveys, drones before and after each monsoon, union-parishad and upazila loss reports, news-reported events | The final judge of accuracy, in every division (E4) |

Data engineering rules:
- **One national analysis grid** (10 m), plus the bank-segment network. Every river is processed by
  the same code.
- Every feature carries an **"available-at" timestamp**. Training only uses information that was
  available when the forecast would really have been issued. This is the same no-leakage rule the
  prototype already enforces, for example by leaving out DEMs made after the training years.

---

## 5. Stage 1: Detection, step by step (one detector, all rivers)

Goal: for every river in the country, know **exactly** which land became river, **when**, and **how
sure** we are.

**Step 1. Build a multi-sensor time series everywhere.**
For every 10 m pixel on the national river network, stack all Sentinel-1, NISAR, Sentinel-2 and HLS
observations. On narrow rivers, add PlanetScope at 3 m. The result is an observation every ~3–6
days, all year, on every river.

**Step 2. Encode it with a foundation model.**
Start from a multi-temporal Earth-observation foundation model such as
[Prithvi-EO-2.0](https://arxiv.org/abs/2412.02732) (pre-trained on 4.2 million global HLS time
series, already fine-tuned for floods on Sen1Floods11), or a multimodal one like TerraMind that takes
radar and optical together. These models already "know" water, land and cloud across the world, so
they transfer across rivers with few extra labels. **Multi-resolution input** lets the same model
read a 15 km-wide Jamuna braid belt and a 60 m-wide tributary.

**Step 3. Fine-tune once, on all river types.**
- About **10,000–20,000 expert-annotated chips**, deliberately balanced across river types:
  braided, meandering, tidal, flashy and small. They cover every season and wet and dry years.
- **Active learning:** a person labels the chips where the model is least sure, which naturally
  pulls in the rivers it knows least.
- The prototype's radar Random Forest (`detector/classifier.py`, 90.6% vs a threshold's 88.0% on
  held-out Jamuna dates) becomes a **baseline** and a source of weak labels.

**Step 4. Add a change head.**
A second head looks at the sequence, not single dates, and outputs **erosion events**:
vegetated/settled land that became water or sand and stayed that way. Each event has a date, area
and confidence. It filters out:
- fields that flood seasonally
- sandbars that go under and come back
- on tidal rivers, land that is just under water at high tide

**Step 5. Correct for water level and tide.**
Each map records the river stage (gauge, SWOT or Flood Hub) and, on tidal rivers, the tide phase at
the moment of the image. Erosion is only confirmed by comparing like with like: similar stage,
similar tide, or after the water has fallen (fixes E1).

**Step 6. Check hotspots at high resolution.**
Where large erosion is detected near settlements, a fine-tuned **Segment Anything** model on 3 m or
sub-metre imagery traces the exact new bank edge and counts lost buildings.
[A 2025 Bangladesh study](https://arxiv.org/html/2510.17198v1) did this with mIoU 86.3%. It measured
11.77 km² of erosion against 11.75 km² in the ground truth.

**Outputs, for the whole country:**
- a 10 m land-cover time series
- a national catalogue of erosion events (river, segment, date, area, confidence, buildings lost)
- annual bank lines for every river

**How detection accuracy is measured:**
- IoU and F1 **per river type**
- erosion-area error against drone and RTK surveys
- date error in days
- Baselines: the Sentinel-1 GEE method of [NHESS 2023](https://nhess.copernicus.org/articles/23/751/2023/)
  (>87% accuracy on the Jamuna), the SAM approach above, and the prototype's radar Random Forest.

---

## 6. Stage 2: Prediction, step by step (one forecaster, all rivers)

Goal: for **every 30 m pixel and every 100 m bank segment in the country**, estimate:
- the probability it is lost to the river, and
- the expected retreat distance in metres with an uncertainty range

for three horizons: the next days, the coming monsoon, and 1–5 years.

The forecaster is **one network with one set of weights**. It has three kinds of internal
"evidence". They are all computed the same way on every river and all trained together.

### Evidence A: Physics, made national with an emulator

Full hydrodynamic models (Delft3D-FM, TELEMAC, HEC-RAS) are accurate but need a separate, calibrated
setup per river. That isn't practical for a national system. So:

1. **Generate training simulations** on a **diverse sample of reaches**: braided, meandering, tidal
   and flashy, across Bangladesh and similar rivers elsewhere, wherever bathymetry exists. Run each
   over many floods.
2. **Train one physics emulator** on all of them: a neural network that takes a reach's shape
   (width, bars, curvature) and the incoming discharge, and outputs near-bank velocity and shear
   stress. [ESurf 2026](https://esurf.copernicus.org/articles/14/313/2026/) showed that a CNN-LSTM
   trained on HEC-RAS outputs can give fast morphodynamic predictions. Here the same idea is trained
   across *many* reaches, so it generalises instead of memorising one river.
3. **Apply it everywhere.** On any river the emulator estimates, using SWOT slope and width and
   Flood Hub or gauge discharge:
   - fluvial erosion from excess shear stress: `ε = k_d (τ − τc)`, where τ is the shear stress
     (`τ = ρ g R S` as a first estimate) and τc and k_d come from the bank-material class
   - bank-stability **factor of safety** (resisting ÷ driving forces), highest-risk as floods fall
4. These physics fields go **into the forecaster as inputs**. They are not a separate model's
   answer.

### Evidence B: River-history patterns

1. **Input:** 20–30 years of annual and monsoon-season maps from Stage 1 around each bank segment.
   All rivers are cut into same-size windows, at the resolution each river needs.
2. **The backbone:** the Stage-1 foundation encoder applied to each time step, then a **temporal
   transformer** that learns how channels, bars and banks move.
3. **Why this improves on the closest existing work.**
   [JamUNet](https://www.researchgate.net/publication/390122469_Deep_learning_for_planform_predictions_of_braided_rivers)
   (TU Delft, 2024) predicted Jamuna planforms from water maps only. It was about **5–6% better
   than assuming no change**, **underpredicted erosion**, and missed channel formation and shifting.
   The national model addresses each of these:
   - it knows the coming flood and the physics fields
   - it uses radar and monsoon data
   - it is trained with a loss that rewards **finding eroded land**
   - it learns from many rivers, not one

### Evidence C: Local signals

The pilot's 14 features (`FEATURES` in `detector/forecast.py`) are all river-agnostic:
- distance to river and main channel
- main channel approaching over 1 and 3 years
- erosion nearby over 1 and 3 years
- water all around and open water upstream
- land age, island vs mainland, vegetation

They are computed for every segment in the country. Alongside them go:
- protection works and their age
- sand-mining pressure
- forecast peak stage and flood duration
- the **river-context vector** from §2

Downstream effects travel along the river, so a **graph layer** over the national river network
passes information between connected segments: upstream to downstream, and between neighbouring
channel threads.
[Research on the Brahmaputra-Jamuna](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2024JF008196)
shows that channel threads migrate coherently, so what happens upstream carries information.

### Putting it together: one network

```
  river history (maps) ──► foundation encoder ──► temporal transformer ─┐
  physics emulator fields (τ, FS, velocity) ────────────────────────────┤
  local signals + protection + sand mining ─────────────────────────────┼─► fusion layers ─► graph layer ─► heads
  forecast flood (stage, duration, fall rate) ──────────────────────────┤      (along the national river network)
  river-context vector (width, braiding, tide, flashiness, soil) ───────┘
                                                                              heads:
                                                                              • P(land lost) per pixel / segment
                                                                              • retreat distance quantiles (m)
                                                                              • channel-switching risk
```

- The **river-context vector** modulates the fusion layers, for example via feature-wise
  conditioning. The model can then weight evidence differently on a tidal river than on a braided
  one, still with one set of weights.
- **Calibration:** isotonic calibration per horizon, checked separately per river type, so "30%
  risk" means about 30% everywhere.
- **Prediction intervals:** conformal prediction on retreat distances, with guaranteed coverage.
- **Channel-switching layer:** some erosion is genuinely random.
  [Li et al. 2025 (WRR)](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2024WR039336) argue
  for hybrid deterministic + stochastic bank-erosion modelling. The model outputs a separate
  "switching risk" instead of pretending to predict these events exactly.
- A **gradient-boosting model** on the same inputs is kept as a fast, explainable **check model**.
  If the two disagree strongly, the segment is flagged for a person to review. It is a safety net,
  not a second forecast.

### Horizons

| Horizon | Issued | What updates it | Use |
|---|---|---|---|
| **Days (nowcast)** | Every radar pass in the monsoon, nationally | New erosion detected nearby + Flood Hub / FFWC / SWOT 1–7 day stage | Warn households to move belongings and livestock *now* |
| **Season** | Each dry season (Jan–Mar; timing adjusted for tidal and flashy rivers), updated monthly | Latest maps + seasonal flood outlook | National priority list for emergency geobags and preparedness |
| **1–5 years** | Yearly | Long-term trends + flood-scenario ensembles | Embankments, schools, relocation planning |

The nowcast is the prototype's `escalate()` idea (forecast-risky cell + fresh radar detection
within 500 m) grown into a trained, validated part of the model with a measured **lead time**.

### Training recipe

1. **Pre-train** on large rivers worldwide (REAL, GSW) so the model sees tens of thousands of
   erosion examples beyond Bangladesh.
2. **Train on all Bangladesh rivers together**: one model, every river in the same batches, sampled
   so small rivers and rare river types aren't drowned out by the Jamuna and Padma.
3. **Walk forward in time:** train up to year *t*, forecast *t+1*, move forward one year, repeat.
   Never split at random.
4. **Hold out whole rivers** (§8.2) to prove the model transfers.
5. **Handle imbalance:** erosion is a small share of bank (about 5% of cells in the pilot's test
   years). Use focal loss and weight each example by area lost.
6. **Physics-informed losses:** penalise predictions that break basic rules. Examples: erosion
   where shear stress is far below τc, or sediment that appears from nowhere.
7. **Check for leakage:** retrain on shuffled labels and confirm the model has no skill, as the
   prototype already does.

---

## 7. How it runs: the national yearly cycle

```
 ┌──────────── DRY SEASON (Jan–Mar) ── whole country ────────────┐
 │ 1. Ingest optical + radar + SWOT + gauges for every river     │
 │ 2. DETECT last monsoon's erosion on every river (Stage 1)     │
 │ 3. Add new labels → retrain the ONE model                     │
 │ 4. ISSUE NATIONAL SEASONAL FORECAST                           │
 │    risk map for every bank segment + national priority list  │
 │ 5. District / upazila teams check their top-ranked segments   │
 └──────────────────────────────┬────────────────────────────────┘
                                ▼
 ┌──────────── MONSOON (Jun–Oct) ────────────────────────────────┐
 │ 6. Every radar pass: detect new erosion nationwide            │
 │ 7. Every day: Flood Hub / FFWC / SWOT stage for every river   │
 │ 8. NOWCAST: high-risk segment + fresh erosion nearby +        │
 │    rising/falling stage → ESCALATE                            │
 │ 9. Alert: SMS / cell broadcast / union parishad, in Bangla    │
 └──────────────────────────────┬────────────────────────────────┘
                                ▼
 ┌──────────── AFTER THE MONSOON (Nov–Dec) ──────────────────────┐
 │ 10. Drone + RTK surveys at a sample of alerted sites          │
 │     in every division                                         │
 │ 11. Score every forecast, per river and nationally            │
 │ 12. Errors → new labels → back to step 3                      │
 └───────────────────────────────────────────────────────────────┘
```

### A real forecast today: the pilot's output

These are the current pilot's actual outputs (`web/data/forecast.json`), issued from the
**January–March 2026 dry season** for the Jamuna reach. The reach has **10,525 scored 240 m cells**.
The highest-risk cell in each study upazila:

| Upazila | Location | Risk score | Rank in the reach | Why (the model's top reasons) |
|---|---|---|---|---|
| **Chauhali** | 24.13847 N, 89.78093 E | **0.80** | **4 of 10,525** | bank nearby lost 16 ha last year · main channel moving closer, ~277 m/yr · main channel only ~30 m away |
| **Kazipur** | 24.61925 N, 89.69793 E | **0.57** | 121 of 10,525 | bank nearby lost 4 ha last year · main channel moving closer, ~136 m/yr · main channel only ~30 m away |

The risk score is the model's raw output. It ranks cells well but is **not yet calibrated**, so
"0.80" should not be read as an 80% chance. Calibration is part of the national design above.

How accurate this model has been, forecasting years it never saw (trained on 1998–2020):

| Forecast year | AUC (pilot / "eroded last year" / "closest to river") | Lost land caught in riskiest 20% of cells (pilot / "eroded last year" / "closest to river") |
|---|---|---|
| 2021 | **0.923** / 0.897 / 0.876 | **88%** / 76% / 77% |
| 2022 | **0.903** / 0.820 / 0.819 | **80%** / 56% / 59% |
| 2023 | **0.877** / 0.870 / 0.837 | **73%** / 72% / 63% |
| 2024 | **0.900** / 0.834 / 0.814 | **79%** / 63% / 57% |
| 2025 | **0.940** / 0.893 / 0.863 | **90%** / 76% / 77% |
| **All 2021–2025** | **0.904** / 0.856 / 0.835 | **81%** / 67% / 65% |
| Kazipur + Chauhali only | **0.871** / 0.850 / 0.830 | **74%** / 67% / 63% |

The pilot wins every year on both measures. In 2023 the margin over "eroded last year" is small
(0.877 vs 0.870; 73% vs 72%). On the stricter riskiest-10% measure, that rule actually edged it
that year (54% vs 53%).

The first real check of the 2026 forecast is mixed. Of the bank erosion the radar detected in June
2026, **45%** in Chauhali fell in the forecast's riskiest 20% of cells, but only **3.5%** in
Kazipur. That Kazipur gap is what §3 and the national design set out to close.

A national model would produce the same kind of output for every bank segment on every river.
A national ranking lets government and NGOs send limited resources where the most people are at
risk, on whichever river that happens to be.

---

## 8. How we prove it beats other methods

### 8.1 The competitors

| Baseline | What it is |
|---|---|
| No change | Next year's river = this year's river |
| Closest to river | Rank land by distance to water (already in `run_forecast.py`) |
| Eroded last year | Rank land by last year's nearby erosion (already in `run_forecast.py`) |
| DSAS extrapolation | Project each transect's linear-regression retreat rate forward |
| CEGIS-style forecast | The operational Bangladesh forecast: satellite-based empirical methods plus expert judgement, on the rivers it covers |
| JamUNet-style U-Net | Deep-learning planform prediction from water maps only |
| Physics only | The emulator's erosion estimate alone |
| Pilot prototype | The current gradient-boosting forecast, extended to all rivers |

### 8.2 The rules

- **Walk-forward, blind:** for each year from 2016 to 2025, every method sees only data up to that
  year's forecast date. 2026 onwards is scored live.
- **Leave-one-river-out:** retrain with one whole river removed, then forecast it. Repeat for
  representative rivers of each type, for example Teesta (flashy), Arial Khan (meandering) and lower
  Meghna (tidal). This is the real test of "one model for all rivers".
- **Same segments, same labels** for every method. Labels come from the independent Stage-1 detector
  plus field surveys, not from any competitor's own maps.

### 8.3 The scorecard

Every metric below is reported **nationally, per river type and per river**.

| Question | Metric |
|---|---|
| Does it rank risky land correctly? | PR-AUC (better than ROC-AUC when erosion is rare), ROC-AUC |
| Does a short watch-list catch the loss? | **Capture@10% / @20%**: share of lost land inside the riskiest 10% / 20% of bank (the prototype's `capture()`) |
| Does it get the totals right? | Predicted vs actual hectares lost. CEGIS reported, for example, 3,225 ha predicted vs 3,450 ha actual in 2017, and 2,800 vs 1,965 in a later year |
| Does it put the bank line in the right place? | Bankline position error in metres per segment |
| Are the probabilities honest? | Brier score and reliability, per river type |
| Are the intervals honest? | Coverage of the 90% retreat interval |
| Does it protect people? | Villages and settlements hit vs warned; false-alarm rate |
| Does the warning come in time? | Median **lead time** in days between alert and loss |
| Does it transfer? | Score on held-out rivers vs the same rivers when included in training |

### 8.4 What "winning" means

- It must win **per river type and per year**, not just on the national average. The Jamuna and
  Padma are large enough to hide failures elsewhere. Significance comes from a block bootstrap over
  years and rivers.
- **Ablations:** remove each evidence type and data source in turn and re-score. Anything that
  doesn't help is dropped.
- **Honest ceiling:** new channel formation is reported **separately**. JamUNet found this
  unpredicted, and we don't expect to predict it exactly either. The switching-risk layer is scored
  on flagging the right *areas*.

### 8.5 Why it should win

Every competitor uses only part of the evidence, or only part of the country:
- **Rules and DSAS** see only the past, not this year's flood.
- **Physics models** know the flood but need per-river setup and good bathymetry, which most rivers
  don't have.
- **Pattern models** (the prototype, JamUNet) know history but not the flood or bank strength, and
  are trained on one river.
- **CEGIS** combines satellite history with expert judgement, but covers selected major rivers and
  can't score every segment of every river each radar pass.

The national model uses all of this evidence, on every river. It learns from far more erosion
examples than any one river offers. That is the reason to expect it to win. The benchmark in
§8.1–8.4 is how we find out.

---

## 9. From the pilot to the national model

| Now (this repo, Jamuna pilot) | Becomes | Gap closed |
|---|---|---|
| `detector/classifier.py`: radar Random Forest (90.6% on held-out dates) | Detection baseline + weak labels for the national foundation-model detector | E2, E4 |
| `detector/forecast.py`: dry-season MNDWI maps, 240 m cells, 14 river-agnostic features | "Local signals" evidence for every river; 30 m grid and bank segments | E3, E10 |
| `run_forecast.py`: gradient boosting, blind test 2021–2025, two rule baselines, shuffled-label check | Benchmark harness (§8) and the check model | — |
| `escalate()` in `run_forecast.py` | The model's trained nowcast head, with measured lead time | E8 |
| `run_trackrecord.py`: news-reported events, blind forecasts | Village hit-rate and lead-time metric, nationwide | — |

**Expansion path:**
1. **Pilot (done):** Jamuna, Kazipur and Chauhali. Blind-tested.
2. **Generalise the code:** replace the fixed `REACH_BOUNDS` with a national river mask and
   bank-segment network. Add the river-context features. Add flood forcing from gauges and Flood
   Hub (fixes E5, the biggest missing driver).
3. **Train one model on the big five:** Jamuna, Padma, Ganges, Meghna and Teesta. Hold out Arial
   Khan and Gorai to test transfer before using the model there.
4. **All erosion-prone rivers:** add the BWDB protection-works inventory, radar-based labels, and
   high-resolution input for narrow rivers.

---

## 10. Risks and responsibilities

- **Uneven accuracy across rivers.** A national average can hide a river where the model is poor.
  Always publish per-river scores, and mark rivers where transfer is untested as "provisional".
- **False alarms vs misses.** A missed village is worse than a false alarm, but too many false
  alarms and people stop listening. Alert thresholds are chosen with district and upazila officials.
  Calibrated probabilities make the trade-off explicit.
- **Data partnerships.** Gauge, bathymetry and protection-works data belong to BWDB, CEGIS, FFWC
  and BIWTA. The system should strengthen their work, not compete with it.
- **Explaining the forecast.** Every alert comes with plain-language reasons (the prototype's
  `REASONS` list, extended with feature attributions), in Bangla.
- **Not over-claiming.** Accuracy quoted to judges, funders or communities is only ever the blind
  walk-forward and held-out-river score, with its uncertainty.

---

## 11. Sources

**Scale of the problem in Bangladesh**
- Riverbank erosion (1,200 km prone, >500 km severe), Banglapedia: https://en.banglapedia.org/index.php/Riverbank_Erosion
- Erosion taking toll on millions (rivers and districts), The Daily Star: https://www.thedailystar.net/news/bangladesh/news/erosion-taking-toll-millions-3391891
- Arial Khan erosion and channel shifting from Landsat time series (2024): https://www.sciencedirect.com/science/article/pii/S2590061724000711
- CEGIS erosion predictions and their accuracy (Prothom Alo): https://en.prothomalo.com/environment/climate-change/expert-study-predicts-river-erosion-in-northwest-seeks-precaution
- River erosion in Bangladesh and the 2026 risk (The Daily Star): https://campaign.thedailystar.net/river-erosion/

**Detection**
- Assessing riverbank erosion in Bangladesh using Sentinel-1 radar in Google Earth Engine, *NHESS*
  2023: https://nhess.copernicus.org/articles/23/751/2023/
- From Pixels to People: satellite-based mapping of riverbank erosion and lost villages in
  Bangladesh (SAM fine-tuning), arXiv 2510.17198, 2025: https://arxiv.org/html/2510.17198v1
- Prithvi-EO-2.0, arXiv 2412.02732: https://arxiv.org/abs/2412.02732 ·
  Sen1Floods11 fine-tune: https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11
- NISAR L-band public data: https://www.earthdata.nasa.gov/news/nisar-l-band-data-released-expanding-record-surface-changes

**Prediction**
- Deep learning for planform predictions of braided rivers (JamUNet):
  https://www.researchgate.net/publication/390122469_Deep_learning_for_planform_predictions_of_braided_rivers
- An integrated deep learning framework enables rapid spatiotemporal morphodynamic predictions
  (CNN-LSTM surrogate of HEC-RAS), *ESurf* 2026: https://esurf.copernicus.org/articles/14/313/2026/
- Coherent motion of channel threads in the braided Brahmaputra-Jamuna River, *JGR Earth Surface*
  2025: https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2024JF008196
- Unveiling the stochasticity of bank erosion (hybrid deterministic + stochastic), *WRR* 2025:
  https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2024WR039336
- Large-scale riverbank erosion risk assessment with multi-source data and XAI (XGBoost), *Ecological
  Indicators* 2024: https://www.sciencedirect.com/science/article/pii/S1470160X2401032X
- Riverbank erosion susceptibility in the Mekong Delta with ML, *Physical Geography* 2025:
  https://www.tandfonline.com/doi/abs/10.1080/02723646.2025.2555849

**Global data and hydrology**
- Global observations of riverbank erosion and accretion from Landsat (REAL), *JGR Earth Surface*
  2023: https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2022JF006774
- SWOT river products (elevation, slope, width, discharge for reaches ≥100 m):
  https://www.earthdata.nasa.gov/data/catalog/pocloud-swot-l2-hr-riversp-reach-d-d ·
  SWORD river database: https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2021WR030054
- Google Flood Hub AI hydrology model: https://research.google/blog/a-flood-forecasting-ai-model-trained-and-evaluated-globally/ ·
  open-sourced framework: https://research.google/blog/the-next-chapter-in-flood-resilience-open-sourcing-googles-hydrology-framework/

**This project**
- Pilot results: `data/forecast/report.json` (forecast), `docs/TECHNICAL.md` (radar detector)
