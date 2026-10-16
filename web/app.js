/* Jamuna Bank Watch dashboard.
   Reads window.FORECAST (run_forecast.py), window.RESULTS (run_analysis.py)
   and window.TRACK (run_trackrecord.py). Static: opens straight from disk.
   Views: #risk (forecast), #radar (monsoon radar), #record (track record), #about. */
(function () {
  const R = window.RESULTS, F = window.FORECAST, T = window.TRACK, S = window.SHOWCASE;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en", {
    minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (x, d = 0) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fdate = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : (s || ""); };
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

  const REP = F && F.report;
  const SITES = (R && R.sites) || (F && F.sites) || {};
  const RUNS = (R && R.runs) || [];
  const BANGLA = { kazipur: "কাজীপুর", chauhali: "চৌহালী" };
  const VIEWS = ["risk", "radar", "record", "compare", "about"];
  const OLD = { overview: "risk", forecast: "risk", monsoon: "radar", proof: "record", method: "about" };
  const viewFromHash = () => { const h = location.hash.slice(1); return VIEWS.includes(h) ? h : (OLD[h] || "risk"); };
  const state = {
    view: viewFromHash(),
    site: SITES[store.get("site")] ? store.get("site") : ("chauhali" in SITES ? "chauhali" : Object.keys(SITES)[0]),
    run: RUNS.length ? RUNS[RUNS.length - 1].id : null,
    src: "s1", fade: 100, showAll: false, picked: null,
    place: S && S.places.some((p) => p.id === store.get("place")) ? store.get("place") : S && S.featured,
    boxes: false, watch: null,   // watch: null = the place's own default (see showWatch)
  };
  const siteName = (sid) => (SITES[sid] || sid || "").replace(" upazila", "");
  const TIER = { high: "High alert", watch: "Watch" };
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const testSpan = () => REP.protocol.test_years.join("–");

  // ---- map --------------------------------------------------------------
  const map = L.map("map", { zoomControl: true }).setView([24.4, 89.72], 10);
  const basemaps = {
    "Satellite": L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 18, attribution: "Esri, Maxar, Earthstar Geographics" }),
    "Streets": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 18, attribution: "© OpenStreetMap contributors" }),
  };
  basemaps["Satellite"].addTo(map);
  let control = L.control.layers(basemaps, {}, { collapsed: true, position: "topright" }).addTo(map);
  const legendCtl = L.control({ position: "bottomleft" });
  legendCtl.onAdd = () => { const d = L.DomUtil.create("div", "legend"); d.id = "legend"; return d; };
  legendCtl.addTo(map);
  let cur = { layers: [], before: null, after: null, markers: [] };

  function clearMap() {
    cur.layers.forEach((l) => map.removeLayer(l));
    map.removeControl(control);
    control = L.control.layers(basemaps, {}, { collapsed: true, position: "topright" }).addTo(map);
    cur = { layers: [], before: null, after: null, markers: [] };
  }
  function add(layer, name, on = true) {
    cur.layers.push(layer);
    if (on) layer.addTo(map);
    if (name) control.addOverlay(layer, name);
    return layer;
  }
  const result = (runId, sid) => R && R.results[`${runId}/${sid}`];
  function siteGeo(sid) {
    const r = R && Object.values(R.results).find((x) => x.site === sid && x.bounds);
    return r ? { bounds: r.bounds, boundary: r.boundary } : null;
  }
  const boundaryStyle = { color: "#ffffff", weight: 2, dashArray: "6 4", fill: false };
  function fitSite(sid) {
    const g = siteGeo(sid);
    if (g) { const [w, s, e, n] = g.bounds; map.fitBounds([[s, w], [n, e]]); }
  }
  function imgOverlay(res, key, opts = {}) {
    const lyr = res.layers && res.layers[key];
    if (!lyr) return null;
    const [w, s, e, n] = res.bounds;
    return L.imageOverlay(lyr.url, [[s, w], [n, e]], { opacity: 1, ...opts });
  }
  function legend(title, items) {
    const open = window.innerWidth > 900 ? " open" : "";
    $("legend").innerHTML = `<details${open}><summary>${esc(title)}</summary>` + items.map(([style, label, cls = ""]) =>
      `<div><i class="${cls}" style="${style}"></i>${label}</div>`).join("") + "</details>";
  }

  // ---- risk map -------------------------------------------------------------
  function drawRiskMap(sid) {
    clearMap();
    (F.reaches || []).forEach((r) => {
      const [w, s, e, n] = r.bounds;
      add(L.imageOverlay(r.png, [[s, w], [n, e]], { opacity: 0.9, interactive: false }), `Risk: ${esc(r.river)} (${esc(r.id)})`);
    });
    const g = siteGeo(sid);
    if (g) add(L.geoJSON(g.boundary, { style: boundaryStyle, interactive: false }), "Upazila boundary");
    const grp = L.layerGroup();
    (F.watch[sid] || []).forEach((c, i) => {
      const m = L.circleMarker([c.lat, c.lon], { radius: 9, color: c.tier === "high" ? css("--high") : css("--watch"),
        weight: 2.5, fillOpacity: 0 }).bindPopup(cellPopup(c, i));
      m.on("click", () => pick(i, false));
      cur.markers[i] = m;
      m.addTo(grp);
    });
    add(grp, "Riskiest stretches");
    const ex = L.layerGroup();
    ((F.escalated || {})[sid]?.cells || []).forEach((c) =>
      L.circleMarker([c.lat, c.lon], { radius: 6, color: "#ffffff", weight: 2, fillColor: css("--high"), fillOpacity: 1 })
        .bindPopup(`<b>Erosion confirmed next to this stretch</b><br>${TIER[c.tier] || "Flagged"} stretch, risk ${pct(c.risk)}.
          Radar saw ${fmt(c.detected_ha, 1)} ha of bank erosion ${c.nearest_m} m away.`).addTo(ex));
    add(ex, "Erosion confirmed by radar");
    legend("Risk this monsoon", [
      [`background:${css("--high")}`, "High alert"],
      [`background:${css("--watch")}`, "Watch"],
      ["background:#f6e7bd", "Lower risk bank land"],
      [`border-color:${css("--high")}`, "Riskiest stretches (click)", "ring"],
      [`background:${css("--high")};box-shadow:0 0 0 2px #fff inset`, "Erosion confirmed by radar", "dot"],
      ["border-color:#9aa4aa", "Upazila boundary", "dash"],
    ]);
    fitSite(sid);
  }
  function cellPopup(c, i) {
    return `<b>${TIER[c.tier] || "Scored"} · stretch ${i + 1} in ${esc(siteName(state.site))}</b><br>
      Chance of losing ${fmt(REP.protocol.min_loss_ha)} ha or more this monsoon: <b>${pct(c.risk)}</b><br>
      ${c.why.length ? c.why.map((w) => esc(cap(w))).join("<br>") : "Several weaker signals together"}<br>
      <span style="color:${css("--muted")}">240 m square at ${c.lat.toFixed(4)}° N, ${c.lon.toFixed(4)}° E</span>`;
  }
  function pick(i, fly) {
    state.picked = i;
    document.querySelectorAll(".stretch").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.i) === i)));
    const m = cur.markers[i];
    if (!m) return;
    if (fly) { map.setView(m.getLatLng(), 14); m.openPopup(); scrollToMapOnPhone(); }
  }

  // ---- radar map ------------------------------------------------------------
  function drawRadarMap(res) {
    clearMap();
    const src = state.src;
    const b = src === "none" ? null : imgOverlay(res, `${src}_before`);
    const a = src === "none" ? null : imgOverlay(res, `${src}_after`);
    if (b) cur.before = add(b, "Before image");
    if (a) cur.after = add(a, "After image");
    applyFade();
    [["s2_dry", "Optical, dry season 2026", 1], ["classes_before", "AI land/water map, before", 0.8],
     ["classes_after", "AI land/water map, after", 0.8]]
      .forEach(([k, name, op]) => { const o = imgOverlay(res, k, { opacity: op }); if (o) add(o, name, false); });
    const ch = imgOverlay(res, "change", { opacity: 0.95 });
    if (ch) add(ch, "Detected change", true);
    add(L.geoJSON(res.boundary, { style: boundaryStyle, interactive: false }), "Upazila boundary");
    const feats = res.patches?.features || [];
    const ll = (f) => [f.geometry.coordinates[1], f.geometry.coordinates[0]];
    const er = L.layerGroup();
    feats.filter((f) => f.properties.kind === "bank_erosion").forEach((f) =>
      L.circleMarker(ll(f), { radius: 4 + Math.min(8, Math.sqrt(f.properties.area_ha)),
        color: css("--high"), weight: 1.5, fillColor: css("--high"), fillOpacity: 0.2 })
        .bindPopup(`<b>Bank erosion</b><br>Established land that became water: ${fmt(f.properties.area_ha, 1)} ha.<br>
          Shoreline moved back along about ${fmt(f.properties.edge_km, 2)} km.`).addTo(er));
    add(er, "Largest erosion patches");
    const flagged = feats.filter((f) => f.properties.review_flag_possible_extraction);
    const fl = L.layerGroup();
    flagged.forEach((f) => L.circleMarker(ll(f), { radius: 11, color: "#b04be0", weight: 2, dashArray: "4 3", fill: false })
      .bindPopup(`<b>Check by hand: possible extraction pit</b><br>Shape rule only (compactness
        ${f.properties.compactness}, fill ${f.properties.fill_ratio}). Ponds and flooded fields can look the same,
        so this is not a finding.`).addTo(fl));
    add(fl, `Possible extraction pits (${flagged.length})`, false);
    legend("What changed", [
      [`background:${css("--high")}`, "Bank erosion of established land"],
      ["background:rgb(150,190,215)", "Flooded sandbar or low land (not erosion)"],
      ["background:rgb(120,144,156)", "Isolated new water (not erosion)"],
      [`background:${css("--gain")}`, "New land"],
    ]);
    const [w, s, e, n] = res.bounds;
    map.fitBounds([[s, w], [n, e]]);
  }
  function applyFade() {
    const v = state.fade / 100;
    if (cur.after) cur.after.setOpacity(v);
    if (cur.before) cur.before.setOpacity(cur.after ? 1 : 1 - v);
  }

  // ---- track-record map ---------------------------------------------------------
  const VERDICT = {
    hit: ["hit", "Predicted"], partial: ["partial", "Partly predicted"], miss: ["miss", "Missed"],
    no_big_loss: ["neutral", "No large loss"],
    early_hit: ["hit", "Predicted so far"], early_partial: ["partial", "Partly, so far"],
    early_miss: ["miss", "Missed so far"], early_no_detection: ["neutral", "No loss seen yet"],
  };
  const vcol = (c) => ({ hit: css("--gain"), partial: css("--watch"), miss: css("--high") }[c] || css("--none"));
  const chip = (v) => { const [c, t] = VERDICT[v] || ["neutral", "Not scored"]; return `<span class="chip ${c}">${esc(t)}</span>`; };
  function drawRecordMap() {
    clearMap();
    const pts = [];
    (T?.events || []).forEach((e) => {
      const [c] = VERDICT[e.verdict] || ["neutral"];
      pts.push([e.lat, e.lon]);
      add(L.circle([e.lat, e.lon], { radius: T.radius_m, color: vcol(c), weight: 2, fillOpacity: 0.1 })
        .bindPopup(`<b>${esc(fdate(e.date))} · ${esc(e.union)} union</b><br>${esc(e.what)}<br>${chip(e.verdict)}`));
    });
    legend("News-reported erosion", [
      [`border-color:${css("--gain")}`, "Forecast flagged it", "ring"],
      [`border-color:${css("--watch")}`, "Partly flagged", "ring"],
      [`border-color:${css("--high")}`, "Missed", "ring"],
      [`border-color:${css("--none")}`, "Not scored yet", "ring"],
      ["display:none", `<span style="color:${css("--muted")}">Circles: ${fmt((T?.radius_m || 3000) / 1000)} km around the union centre</span>`],
    ]);
    if (pts.length) map.fitBounds(pts, { padding: [40, 40] });
  }

  // ---- charts (inline SVG) ---------------------------------------------------------
  // Horizontal bars, one measure. The AI bar carries the accent; rules are grey.
  function barChart(rows, { max = 1, valueFmt = (v) => pct(v) } = {}) {
    const W = 420, rowH = 32, left = 150, right = 44, H = rows.length * rowH + 4;
    const x = (v) => left + (W - left - right) * (v / max);
    const bars = rows.map((r, i) => {
      const y = i * rowH + 7, h = 16, w = Math.max(2, x(r.value) - left);
      return `<text x="${left - 10}" y="${y + 12}" text-anchor="end" ${r.me ? 'style="fill:var(--ink);font-weight:600"' : ""}>${esc(r.label)}</text>
        <rect x="${left}" y="${y}" width="${w}" height="${h}" rx="2" fill="${r.color}" fill-opacity="${r.opacity ?? 1}"/>
        <text class="v" x="${left + w + 6}" y="${y + 12}">${valueFmt(r.value)}</text>
        <rect class="hit" x="0" y="${y - 7}" width="${W}" height="${rowH}" data-tip="${esc(r.tip || `${r.label}: ${valueFmt(r.value)}`)}"/>`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Bar chart">
      <line class="axis" x1="${left}" x2="${left}" y1="0" y2="${H}"/>${bars}</svg>`;
  }
  // Lines over the test years, one y axis, labelled at the line ends.
  function lineChart(series, { ymin, ymax, ticks, yFmt }) {
    const W = 420, H = 190, l = 36, r = 104, t = 10, b = 26;
    const xs = series[0].points.map((p) => p[0]);
    const x = (v) => l + (W - l - r) * (xs.length > 1 ? (v - xs[0]) / (xs[xs.length - 1] - xs[0]) : 0.5);
    const y = (v) => t + (H - t - b) * (1 - (v - ymin) / (ymax - ymin));
    const grid = ticks.map((v) => `<line class="grid" x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`).join("");
    const xl = xs.map((v) => `<text x="${x(v)}" y="${H - 6}" text-anchor="middle">${v}</text>`).join("");
    // Spread end labels so they never collide.
    const ends = series.map((s) => ({ s, y: y(s.points[s.points.length - 1][1]) })).sort((a, b2) => a.y - b2.y);
    ends.forEach((e, i) => { if (i && e.y - ends[i - 1].y < 14) e.y = ends[i - 1].y + 14; });
    const lines = series.map((s) => {
      const d = s.points.map((p, i) => `${i ? "L" : "M"}${x(p[0])},${y(p[1])}`).join(" ");
      const last = s.points[s.points.length - 1], ly = ends.find((e) => e.s === s).y;
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.me ? 2.5 : 1.75}" stroke-linejoin="round" stroke-linecap="round"/>
        ${s.points.map((p) => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="${s.me ? 3.5 : 2.5}" fill="${s.color}"/>
          <circle class="hit" cx="${x(p[0])}" cy="${y(p[1])}" r="10" data-tip="${esc(`${s.name}, ${p[0]}: ${yFmt(p[1])}`)}"/>`).join("")}
        <text x="${x(last[0]) + 9}" y="${ly + 4}" ${s.me ? 'style="fill:var(--ink);font-weight:600"' : ""}>${esc(s.short)}</text>`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Line chart">${grid}${xl}${lines}</svg>`;
  }
  // Calibration: predicted vs observed, with the diagonal where they match.
  function calibChart(rows) {
    const W = 420, H = 230, l = 50, r = 16, t = 10, b = 40;
    const max = Math.max(0.6, ...rows.map((q) => Math.max(q.predicted, q.observed)));
    const m = Math.min(1, Math.ceil(max * 10) / 10);
    const x = (v) => l + (W - l - r) * v / m, y = (v) => t + (H - t - b) * (1 - v / m);
    const ticks = [0, m / 4, m / 2, 3 * m / 4, m];
    const g = ticks.map((v) => `<line class="grid" x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${pct(v)}</text>
      <text x="${x(v)}" y="${H - 22}" text-anchor="middle">${pct(v)}</text>`).join("");
    const dots = rows.map((q) => {
      const rad = 3.5 + Math.min(5, Math.sqrt(q.cells) / 30);
      return `<circle cx="${x(q.predicted)}" cy="${y(q.observed)}" r="${rad}" fill="var(--accent)" fill-opacity=".85"/>
        <circle class="hit" cx="${x(q.predicted)}" cy="${y(q.observed)}" r="${rad + 7}"
          data-tip="${esc(`Forecast said ${pct(q.predicted)}; ${pct(q.observed)} eroded (${fmt(q.cells)} stretches)`)}"/>`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Calibration chart">${g}
      <line x1="${x(0)}" y1="${y(0)}" x2="${x(m)}" y2="${y(m)}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3"/>
      <text x="${x(m) - 4}" y="${y(m) + 16}" text-anchor="end">forecast = reality</text>
      ${dots}<text x="${(l + W - r) / 2}" y="${H - 4}" text-anchor="middle">risk the forecast gave</text>
      <text transform="translate(11 ${(t + H - b) / 2}) rotate(-90)" text-anchor="middle">share that eroded</text></svg>`;
  }
  const tipEl = document.createElement("div");
  tipEl.className = "tip"; tipEl.hidden = true; document.body.appendChild(tipEl);
  document.addEventListener("mousemove", (e) => {
    const h = e.target.closest && e.target.closest("[data-tip]");
    if (!h) { tipEl.hidden = true; return; }
    tipEl.textContent = h.dataset.tip; tipEl.hidden = false;
    tipEl.style.left = e.clientX + "px"; tipEl.style.top = e.clientY + "px";
  });

  // ---- small builders -----------------------------------------------------------
  const det = (title, body, count) =>
    `<details><summary>${title}${count != null ? `<span class="count">${count}</span>` : ""}</summary><div class="inner">${body}</div></details>`;
  const figures = (items) => `<dl class="figures">${items.map(([v, unit, k]) =>
    `<div><dt>${k}</dt><dd>${v}${unit ? `<small>${unit}</small>` : ""}</dd></div>`).join("")}</dl>`;
  const sources = (list) => list.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`).join("<br>");
  const simAlert = (inner) => `<div class="sim"><div class="sim-flag">Simulated. Nothing was sent.</div>${inner}</div>`;

  // ---- view: risk this monsoon ------------------------------------------------------
  function riskPanel() {
    const sid = state.site, name = esc(siteName(sid));
    const counts = F.tier_counts[sid] || {}, cells = F.watch[sid] || [];
    const all = REP.tiers.all_rivers, ex = (F.escalated || {})[sid], al = F.alerts && F.alerts[sid];
    const hi = counts.high || 0, wa = counts.watch || 0, lo = counts.scored || 0, tot = hi + wa + lo || 1;
    const nEx = ex ? (ex.total ?? ex.cells.length) : 0;
    const exDate = ex && fdate((ex.after_dates || []).slice(-1)[0]);
    const shown = state.showAll ? cells : cells.slice(0, 8);
    const list = shown.map((c, i) => `<li><button type="button" class="stretch" data-i="${i}" aria-current="${state.picked === i}">
        <span class="rank">${i + 1}</span>
        <span class="why">${c.why.length ? esc(cap(c.why[0])) : "Several weaker signals together"}
          ${c.why.length > 1 ? `<span>${c.why.slice(1).map(esc).join(" · ")}</span>` : ""}</span>
        <span class="risk"><b>${pct(c.risk)}</b><span class="meter"><i style="width:${Math.round(c.risk * 100)}%;background:var(--${c.tier === "high" ? "high" : "watch"})"></i></span></span>
      </button></li>`).join("");

    return `<div class="block">
        <p class="kicker">Monsoon ${REP.live.year} forecast, from ${esc(F.issued_from)} satellite maps</p>
        <h2 class="headline">${fmt(hi)} stretches of bank in ${name} are on High alert</h2>
        <p class="body">The river bank is split into 240 m squares of farmland, homesteads or trees. Each one gets the chance that the river
          takes at least ${fmt(REP.protocol.min_loss_ha)} ha of it before next dry season.</p>
        <div class="tierbar" role="img" aria-label="${hi} High alert, ${wa} Watch, ${lo} lower risk">
          <i style="flex:${hi / tot};background:var(--high)"></i><i style="flex:${wa / tot};background:var(--watch)"></i><i style="flex:${lo / tot};background:var(--none);opacity:.45"></i></div>
        <div class="tierkey">
          <div><span class="v">${fmt(hi)}</span><span class="k"><i class="swatch" style="background:var(--high)"></i>High alert, ${pct(REP.thresholds.high)}+</span></div>
          <div><span class="v">${fmt(wa)}</span><span class="k"><i class="swatch" style="background:var(--watch)"></i>Watch, ${pct(REP.thresholds.watch)}+</span></div>
          <div><span class="v">${fmt(lo)}</span><span class="k"><i class="swatch" style="background:var(--none)"></i>Lower risk</span></div>
        </div>
        ${ex ? (nEx ? `<div class="callout"><p class="body"><b>Radar on ${esc(exDate)} confirmed erosion next to ${fmt(nEx)} flagged
            stretch${nEx === 1 ? "" : "es"}.</b> This is the point where a warning would go out.</p>
            <p><a href="#risk" data-fly-ex="0">Show on the map</a></p></div>`
          : `<div class="callout calm"><p class="body">Latest radar pass (${esc(exDate)}): no confirmed erosion next to any flagged stretch.</p></div>`) : ""}
      </div>

      <div class="block">
        <h3>Riskiest stretches</h3>
        <p class="fine">Select one to see it on the map. The main reason comes first.</p>
        ${cells.length ? `<ol class="stretches">${list}</ol>` : `<p class="body">No stretch is flagged in ${name}.</p>`}
        ${cells.length > 8 ? `<p><button type="button" class="linkish" id="more">${state.showAll ? "Show fewer" : `Show all ${cells.length}`}</button></p>` : ""}
      </div>

      <div class="block">
        <h3>How far to trust it</h3>
        <p class="fine">Scored on the ${testSpan()} monsoons, which the model never saw during training.</p>
        ${figures([
          [`${all.high.lift}×`, "", "as likely to erode as an average stretch, if on High alert"],
          [pct(all.high.precision_within_240m), "", "of High alerts: that stretch or the next one lost a hectare"],
          [pct(all.high_or_watch.land_caught), "", "of all land lost was on the High or Watch list"],
        ])}
        <p class="fine">In a given year only ${pct(REP.ranking.all_rivers.ai.base_rate, 1)} of riverside stretches lose a hectare.
          On High alert it is ${pct(all.high.precision)}, counting the stretch's own 240 m square only. Every High alert is worth a
          field check, but not every one will erode. <a href="#record">See the track record</a></p>
      </div>

      <div class="stack">
        ${ex && ex.cells.length ? det("Stretches with erosion confirmed nearby", `<p class="fine">Flagged stretches where radar saw established
            land turn to water within 500 m in the pass of ${esc((ex.after_dates || []).map(fdate).join(", "))}.
            How many days of warning this gives has not been measured yet.</p>
          <table class="t"><thead><tr><th>Tier</th><th class="n">Risk</th><th class="n">Eroded</th><th class="n">Distance</th></tr></thead>
          ${ex.cells.slice(0, 10).map((c, i) => `<tr data-fly-ex="${i}"><td><span class="chip ${c.tier}">${TIER[c.tier] || ""}</span></td>
            <td class="n">${pct(c.risk)}</td><td class="n">${fmt(c.detected_ha, 1)} ha</td><td class="n">${c.nearest_m} m</td></tr>`).join("")}</table>`, nEx) : ""}
        ${al ? det("The warning people would get", simAlert(`
          <div class="sms" lang="bn">${esc(al.message_bn)}</div>
          <p class="fine">${esc(al.message_en)}</p>
          <p class="fine"><b>To officials:</b> ${esc(al.message_official)}</p>
          <ul>${al.recipients.map((r) => `<li>${esc(r.role)}: ${esc(r.channel)}</li>`).join("")}</ul>
          <p class="fine"><b>Replies:</b> ${esc(al.reply_loop)}</p>`)) : ""}
        ${det("What the forecast looks at", `<p class="fine">The inputs that mattered most in the test years:</p>
          <ol class="fine" style="margin:0;padding-left:20px">${Object.keys(REP.importance).slice(0, 6).map((k) => `<li>${esc(k)}</li>`).join("")}</ol>
          <p class="fine">It can't see bank protection such as geobags or revetments, so protected banks may be over-flagged.</p>`)}
      </div>`;
  }

  // ---- view: latest radar ---------------------------------------------------------
  function radarPanel() {
    const res = result(state.run, state.site), run = RUNS.find((r) => r.id === state.run);
    const runSeg = `<div class="seg" id="runs">${RUNS.map((r) => `<button type="button" data-run="${esc(r.id)}" title="${esc(r.label)}"
      aria-pressed="${r.id === state.run}">${esc(r.label.split("(")[0].trim())}</button>`).join("")}</div>`;
    const srcSeg = `<div class="seg" id="srcs">${[["s1", "Radar"], ["s2", "Optical"], ["none", "Satellite map"]].map(([k, t]) =>
      `<button type="button" data-src="${k}" aria-pressed="${k === state.src}">${t}</button>`).join("")}</div>`;
    const controls = (res) => {
      const none = state.src === "s2" ? "no clear image" : "";
      const bl = res?.layers?.[`${state.src}_before`]?.label, al = res?.layers?.[`${state.src}_after`]?.label;
      const short = (s) => (s || "").replace(/^.*\(/, "").replace(/\)$/, "");
      return `<div class="controls">
        <div class="control"><span>Image under the change map</span>${srcSeg}</div>
        ${state.src !== "none" ? `<div class="control"><span>Slide to compare before and after</span>
          <div class="fade"><span>${esc(short(bl) || none || "before")}</span>
          <input id="fade" type="range" min="0" max="100" value="${state.fade}" aria-label="Fade from the before image to the after image">
          <span>${esc(short(al) || none || "after")}</span></div></div>` : ""}
      </div>`;
    };
    const pickWindow = `<div class="control"><span>Two-week window</span>${runSeg}</div>`;
    if (!res || res.error) return `<div class="block">${pickWindow}${controls(res)}<h2 class="headline">No radar result for this window</h2>
      <p class="body">${esc(res?.error || "The analysis has not been run for this window.")}</p></div>`;

    const st = res.stats, sc = res.scenes, ev = R.reference_events.find((e) => e.site === state.site);
    const est = st.established_land_filter, m = R.model;
    const inWindow = ev && new Date(ev.reported_date) >= new Date(run.before[0]) &&
      new Date(ev.reported_date) <= new Date(new Date(run.after[1]).getTime() + 864e5 * 14);
    return `<div class="block">
        ${pickWindow}
        <p class="kicker" style="margin-top:6px">${esc(run.label)}</p>
        <h2 class="headline">${fmt(st.bank_erosion_ha, st.bank_erosion_ha < 10 ? 1 : 0)} ha of ${est ? "established land" : "land"} lost to the river in ${esc(siteName(state.site))}</h2>
        <p class="body">Sentinel-1 radar sees through monsoon cloud. Passes of ${esc(sc.before_dates.map(fdate).join(", "))} and
          ${esc(sc.after_dates.map(fdate).join(", "))}.</p>
        ${figures([
          [fmt(st.bank_erosion_ha, 1), "ha", "bank erosion"],
          ...(est ? [[fmt(st.flooded_low_land_ha), "ha", "flooded low land (not erosion)"]] : []),
          [fmt(st.accretion_ha, 1), "ha", "new land"],
        ])}
        <p class="fine">Bank erosion means farmland, homesteads or trees at the channel that became river. Flooded sandbars and low land are
          under water now but usually come back, so they are not counted.</p>
        <p class="note">${esc(run.note)}</p>
      </div>
      <div class="block">${controls(res)}</div>
      <div class="stack">
        ${det("Why this is lower than raw radar change", `<p class="fine">A pixel counts as eroded only if (1) the radar model is at least
          70% sure it was land before and water after, (2) the patch is at least 1 ha and touches the old channel, and (3) it was
          <b>established land</b>: vegetated in the Jan–Mar 2026 optical image and not water in any of the last 5 dry seasons.</p>
          <p class="fine">Rule 3 removes the biggest false alarm: low sandbars that go under when the river rises. A plain radar
          threshold sees ${fmt(st.threshold_land_to_water_ha)} ha of land-to-water change in this window.</p>`)}
        ${ev ? det(`In the news: ${esc(ev.places.join(", "))}`, `<table class="t kv">
            <tr><th>Reported</th><td>${esc(ev.as_reported)}</td></tr>
            <tr><th>Date</th><td>${esc(fdate(ev.reported_date))} ${inWindow ? "(inside this window)" : "<b>(outside this window; try the other window above)</b>"}</td></tr>
            <tr><th>Figure</th><td>${ev.reported_quantity ? esc(ev.reported_quantity) : "none given"}. Our longest single eroding stretch is about ${fmt(st.longest_patch_edge_km, 1)} km.</td></tr>
            <tr><th>Source check</th><td>${esc(ev.verification)}</td></tr>
            <tr><th>Sources</th><td>${sources(ev.sources)}</td></tr></table>`) : ""}
        ${m && !m.error ? det("The radar model", `<p class="fine">${esc(m.model)}. It labels each 10 m pixel as water, sand or land.
          It learned from ${fmt(m.n_train)} pixels on ${m.train_dates.length} clear days, where optical images and Google's Dynamic World
          agree on the label, and was tested on ${fmt(m.n_test)} pixels from ${m.test_dates.length} days it never saw.</p>
          <table class="t"><thead><tr><th>On unseen days</th><th class="n">Accuracy</th></tr></thead>
            <tr class="me"><td>Radar model, water or not</td><td class="n">${pct(m.rf_water_vs_not.overall_accuracy, 1)}</td></tr>
            <tr><td>Plain ${m.baseline_threshold_db} dB threshold</td><td class="n">${pct(m.threshold_water_vs_not.overall_accuracy, 1)}</td></tr>
            <tr><td>Radar model, water / sand / land</td><td class="n">${pct(m.rf_3class.overall_accuracy, 1)}</td></tr></table>
          <p class="fine">The clear days are mostly outside the monsoon, so accuracy during the monsoon may be lower.</p>`) : ""}
        ${det("Alert to officials", res.alert ? simAlert(`<div class="sms">${esc(res.alert.message)}</div>
            <ul>${res.alert.recipients.map((r) => `<li>${esc(r.role)}: ${esc(r.channel)}</li>`).join("")}</ul>`)
          : `<p class="fine">No alert for this window: ${fmt(st.bank_erosion_ha, 1)} ha is below the ${fmt(R.alert_threshold_ha)} ha trigger.</p>`)}
        ${det("Image quality checks", `<p class="fine">Both images come from orbit ${sc.orbit} (${esc(sc.pass.toLowerCase())} pass), so the viewing angle is the same.
          ${sc.calm_water_db ? `Wind roughens water until it looks like land, so we check calm water: before ${sc.calm_water_db.before} dB, after ${sc.calm_water_db.after} dB, limit ${sc.calm_water_db.limit} dB.` : ""}
          ${st.review_flags} patch${st.review_flags === 1 ? "" : "es"} flagged for a person to check as possible extraction pits (off by default in the map's layer list).</p>
          ${sc.quality_warning ? `<p class="note">${esc(sc.quality_warning)}</p>` : ""}`)}
      </div>`;
  }

  // ---- view: track record ------------------------------------------------------------
  function recordPanel() {
    const rk = REP.ranking.all_rivers, tiers = REP.tiers.all_rivers;
    const sites = REP.tiers.kazipur_chauhali, sitesRk = REP.ranking.kazipur_chauhali;
    const ev = T?.events || [];
    const bars = barChart([
      { label: "AI forecast", value: rk.ai.capture_top20, color: "var(--accent)", me: true },
      { label: "AI, first version", value: rk.ai_version1.capture_top20, color: "var(--accent)", opacity: 0.45, tip: `AI, first version (fewer inputs): ${pct(rk.ai_version1.capture_top20)}` },
      { label: "Eroded last year", value: rk.eroded_last_year.capture_top20, color: "var(--rule-a)", tip: `Rule: flag what eroded last year: ${pct(rk.eroded_last_year.capture_top20)}` },
      { label: "Closest to river", value: rk.closest_to_river.capture_top20, color: "var(--rule-a)", tip: `Rule: flag what is closest to the river: ${pct(rk.closest_to_river.capture_top20)}` },
    ]);
    const yrs = Object.keys(REP.per_year);
    const series = [
      { name: "AI forecast", short: "AI", color: "var(--accent)", me: true, key: "ai" },
      { name: "Rule: eroded last year", short: "Eroded last yr", color: "var(--rule-a)", key: "eroded_last_year" },
      { name: "Rule: closest to river", short: "Closest", color: "var(--rule-b)", key: "closest_to_river" },
    ].map((s) => ({ ...s, points: yrs.map((y) => [Number(y), REP.per_year[y].ranking[s.key].auc]) }));
    const lo = Math.floor(Math.min(...series.flatMap((s) => s.points.map((p) => p[1]))) * 20) / 20;
    const row = (name, b, me) => `<tr class="${me ? "me" : ""}"><td>${name}</td><td class="n">${fmt(b.true_alerts_per_year + b.false_alarms_per_year)}</td>
      <td class="n">${pct(b.precision)}</td><td class="n">${pct(b.precision_within_240m)}</td><td class="n">${pct(b.land_caught)}</td></tr>`;
    const ur = REP.unseen_river || {};
    const early = (e) => String(e.verdict || "").startsWith("early");

    return `<div class="block">
        <p class="kicker">Scored on ${testSpan()}, monsoons the model never saw</p>
        <h2 class="headline">The forecast catches more of the land lost than simple rules do</h2>
        <p class="body">Share of all land lost that was inside the <b>riskiest 20% of the bank</b>, across ${fmt(rk.ai.cells)} stretch-years on the Jamuna and Ganges–Padma.</p>
        ${bars}
        <p class="fine">Ranking score (AUC, 1 is perfect, 0.5 is a coin toss): AI ${rk.ai.auc}, first version ${rk.ai_version1.auc},
          rules ${rk.eroded_last_year.auc} and ${rk.closest_to_river.auc}. We quote the first version's
          ${pct(rk.ai_version1.capture_top20)}: its inputs date from before the test years were looked at, and the newer inputs add
          too little to claim.${sitesRk ? ` Kazipur and Chauhali alone: AI ${sitesRk.ai.auc}, and the riskiest 20% held ${pct(sitesRk.ai.capture_top20)} of land lost.` : ""}</p>
      </div>

      ${ev.length ? `<div class="block">
        <h3>Erosion in the news: did the forecast see it coming?</h3>
        <p class="fine">Each forecast was made before that monsoon. Select a row to see the place on the map.</p>
        <div class="scroll-x"><table class="t"><thead><tr><th>Union, reported</th><th class="n">Flagged</th><th class="n">Loss in flagged</th><th>Result</th></tr></thead>
        ${ev.map((e) => `<tr data-fly="${e.lat},${e.lon}"><td>${esc(e.union)}<br><span class="fine">${esc(fdate(e.date))}</span></td>
          <td class="n">${pct(e.flagged_share)}</td><td class="n">${e.outcome?.caught_share != null ? pct(e.outcome.caught_share) : "–"}</td>
          <td>${chip(e.verdict)}</td></tr>`).join("")}</table></div>
        <p class="fine"><b>Flagged</b> is the share of bank near the union the forecast put on a list. <b>Loss in flagged</b> is the share of land
          actually lost that fell on those stretches. If the forecast were guessing, the two would be about equal. Up to 2025 the outcome
          is the next dry-season map; for 2026 it is our radar, because the monsoon is not over.</p>
        <p class="fine"><b>The two 2026 Kazipur misses are real.</b> There the river ran in narrow side channels in the dry season. The
          side-channel inputs help on average, but not at these two places.</p>
      </div>` : ""}

      <div class="block">
        <h3>False alarms</h3>
        <p class="fine">Alert thresholds were fixed on ${REP.protocol.validation_years.join("–")} and then counted once on the test years. All rivers, per year:</p>
        <div class="scroll-x"><table class="t"><thead><tr><th></th><th class="n">Flagged</th><th class="n">Eroded</th>
          <th class="n">Or next door</th><th class="n">Land caught</th></tr></thead>
          ${row("High alert", tiers.high, true)}${row("High + Watch", tiers.high_or_watch)}</table></div>
        <p class="fine"><b>Eroded</b>: the stretch itself lost ${fmt(REP.protocol.min_loss_ha)} ha or more. <b>Or next door</b>: it or a touching stretch did.
          An average stretch erodes ${pct(rk.ai.base_rate, 1)} of the time, so a High alert is ${tiers.high.lift}× as likely to be right as a guess.
          High alert averages ${fmt(tiers.high.false_alarms_per_year)} false alarms a year across the ${Object.keys(REP.protocol.reaches).length} river reaches.
          ${sites ? `In Kazipur and Chauhali, ${pct(sites.high.precision)} of High alerts were right.` : ""}
          ${REP.thresholds.target_met_on_validation ? "" : "<b>The 50% target could not be reached on the validation years, so the most precise tier is used.</b>"}
          ${tiers.high.precision < REP.thresholds.high_precision_target ? `The threshold reached ${pct(REP.thresholds.validation_high_precision)}
            on the validation years but ${pct(tiers.high.precision)} on the test years. We report that as measured rather than re-tune on the
            test years, which would make the test meaningless.` : ""}</p>
      </div>

      <div class="stack">
        ${(REP.precision_vs_coverage || []).length ? det("Shorter list, fewer false alarms", `<p class="fine">A shorter list is right more
          often but catches less of the land lost. Test years, all rivers. This shows the trade-off; the High alert cut-off itself was
          fixed on the validation years.</p>
          <div class="scroll-x"><table class="t"><thead><tr><th>Riskiest share of bank</th><th class="n">Eroded</th><th class="n">Land caught</th></tr></thead>
          ${REP.precision_vs_coverage.map((r) => `<tr><td>${pct(r.share, r.share < 0.01 ? 1 : 0)}</td><td class="n">${pct(r.precision)}</td>
            <td class="n">${pct(r.land_caught)}</td></tr>`).join("")}</table></div>`) : ""}
        ${det("Year by year",`<p class="fine">Ranking score (AUC) for each test monsoon, all rivers. The AI is ahead of both rules every year.</p>
          ${lineChart(series, { ymin: lo, ymax: 1, ticks: [lo, (lo + 1) / 2, 1].map((v) => Math.round(v * 100) / 100), yFmt: (v) => v.toFixed(2) })}`)}
        ${det("Can the percentages be taken at face value?", `<p class="fine">Each dot is a group of stretches with similar forecast risk.
          Dots near the dashed line mean that when the forecast says 40%, about 40% of those stretches erode.</p>${calibChart(REP.calibration.reliability_test)}`)}
        ${Object.keys(ur).length ? det("Does it work on a river it never saw?", `<p class="fine">We trained the model without one river and tested it on that river.
          It lost very little skill, which suggests it learned how rivers behave rather than memorising one map.</p>
          <div class="scroll-x"><table class="t"><thead><tr><th>Tested on</th><th class="n">AUC, never seen</th><th class="n">AUC, trained on it</th><th class="n">Top 20%, never seen</th></tr></thead>
          ${Object.entries(ur).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${v.never_seen.auc}</td><td class="n">${v.trained_with_it.auc}</td>
            <td class="n">${pct(v.never_seen.capture_top20)}</td></tr>`).join("")}</table></div>`) : ""}
        ${(T?.cases || []).length ? det("Satellite pictures of hits and misses", (T.cases).map((c) => `<div class="case"><h4>${chip(c.kind === "hit" ? "hit" : "miss")}
          Monsoon ${c.year}, ${esc(c.place)}</h4><p class="fine">${fmt(c.lost_ha)} ha lost; ${pct(c.caught_share)} of it was flagged in March ${c.year}.
          <a href="#record" data-fly="${c.lat},${c.lon}">Show on map</a></p>${trio(c.images, [`Jan–Mar ${c.year}`, `Jan–Mar ${c.year + 1}`, "Flagged vs lost"])}</div>`).join("")
          + `<p class="keyline"><span><i class="swatch" style="background:#4a90c4"></i>river</span><span><i class="swatch" style="background:#bed6a4"></i>farmland</span>
            <span><i class="box" style="border-color:#f06e14"></i>Watch</span><span><i class="box" style="border-color:#7a0020"></i>High alert</span>
            <span><i class="swatch" style="background:#d62728"></i>land lost</span></p>
            <p class="fine">Picked as clear examples. The fair score is the chart at the top.</p>`, T.cases.length) : ""}
        ${ev.length ? det("Every news event in detail", ev.map((e) => `<div class="case"><h4>${esc(fdate(e.date))}, ${esc(e.union)} ${chip(e.verdict)}</h4>
          <p class="fine">${esc(e.what)}</p><p class="fine">${sources(e.sources)}</p>
          ${e.note ? `<p class="fine"><i>${esc(e.note)}</i></p>` : ""}
          ${trio(e.images, early(e) ? [`Jan–Mar ${e.forecast_year}`, "Radar, latest", "Flagged vs radar"] : [`Jan–Mar ${e.forecast_year}`, `Jan–Mar ${e.forecast_year + 1}`, "Flagged vs lost"])}</div>`).join(""), ev.length) : ""}
        ${det("How the test was kept fair", protocolList())}
      </div>`;
  }
  function trio(imgs, labels) {
    return `<div class="trio">${["before", "after", "outcome"].map((k, i) => imgs && imgs[k] ? `<figure>
      <a href="${esc(imgs[k])}" target="_blank" rel="noopener"><img src="${esc(imgs[k])}" alt="${esc(labels[i])}" loading="lazy"></a>
      <figcaption>${esc(labels[i])}</figcaption></figure>` : "").join("")}</div>`;
  }
  function protocolList(cls = "fine") {
    const p = REP.protocol, sel = REP.input_selection || [], sh = REP.shuffled_label_check;
    return `<ul class="${cls}" style="margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px">
      <li><b>Years split three ways, fixed in code:</b> train on ${p.train_years.join("–")}, make every choice on ${p.validation_years.join("–")},
        score once on ${p.test_years.join("–")}.</li>
      <li><b>No peeking at the future:</b> every input for a year uses only maps up to that year. An automated test scrambles all later
        years and checks that nothing changes (<code>tests/test_forecast.py</code>).</li>
      <li><b>Leakage check:</b> trained on shuffled answers, the model has no skill (AUC ${sh.auc_min} to ${sh.auc_max}).</li>
      <li><b>Each new input had to improve the validation score to stay:</b>
        ${sel.map((s) => `${esc(s.inputs)} ${s.kept ? "kept" : "dropped"} (${s.val_avg_precision})`).join("; ")}.</li>
      <li><b>Reproduce it:</b> <code>python evaluate.py</code> reruns all of this offline from the cached maps.</li></ul>`;
  }

  // ---- view: predicted vs actual (full width, made for a slide) ---------------------------
  // Two maps locked together: the forecast issued before the monsoon on that March's
  // satellite picture, and the land that was river by the next dry season on the next one.
  let duo = null;
  function compareMaps() {
    if (duo) return duo;
    const mk = (id) => {
      // No basemap: tiles from another date around the edges would muddle the comparison.
      return L.map(id, { zoomControl: false, zoomSnap: 0.1, attributionControl: false, maxZoom: 17 });
    };
    duo = { pred: mk("map-pred"), act: mk("map-act"), layers: [] };
    L.control.zoom({ position: "topright" }).addTo(duo.act);
    L.control.attribution({ prefix: false, position: "bottomright" })
      .addAttribution("Sentinel-2 (ESA), Landsat (USGS/NASA)").addTo(duo.act);
    let busy = false;
    const follow = (a, b) => a.on("move", () => {
      if (busy) return;
      busy = true; b.setView(a.getCenter(), a.getZoom(), { animate: false }); busy = false;
    });
    follow(duo.pred, duo.act); follow(duo.act, duo.pred);
    return duo;
  }
  function comparePage() {
    const p = S.places.find((x) => x.id === state.place) || S.places[0];
    const y = p.year, ev = p.kind === "event";
    const opts = S.places.map((x) => `<option value="${esc(x.id)}" ${x.id === p.id ? "selected" : ""}>${esc(x.place)}, monsoon ${x.year}${x.id === S.featured ? " (featured)" : ""}</option>`).join("");
    const c = p.circle, hi = c.high, wa = c.watch, km = fmt(S.radius_m / 1000), minHa = fmt(S.min_loss_ha);
    const highLed = hi.loss_share >= 0.5, both = (hi.loss_share || 0) + (wa.loss_share || 0);
    const figs = highLed ? [
      [fmt(c.lost_ha, 1), "ha", `of farmland and homesteads lost within ${km} km`],
      [pct(hi.loss_share), "", `of it was inside the ${hi.n} High-alert squares`],
      [pct(hi.n / c.cells), "", "of the bank there was on High alert"],
      [`${hi.eroded} of ${hi.n}`, "", `High-alert squares lost ${minHa} ha or more`],
    ] : [
      [fmt(c.lost_ha, 1), "ha", `of farmland and homesteads lost within ${km} km`],
      [pct(both), "", "of it was on the High alert or Watch list"],
      [pct((hi.n + wa.n) / c.cells), "", "of the bank there was on those lists"],
      [`${hi.eroded + wa.eroded} of ${hi.n + wa.n}`, "", `listed squares lost ${minHa} ha or more`],
    ];
    const headline = highLed
      ? `The ${hi.n} squares on High alert held ${pct(hi.loss_share)} of the land the river took`
      : `${pct(both)} of the land the river took here was on the forecast's lists`;
    const watchOn = showWatch(p);
    return `<div class="cmp-head">
        <div class="cmp-title">
          <p class="kicker">Monsoon ${y} · ${esc(p.place)} · Jamuna, Sirajganj</p>
          <h1>${headline}</h1>
        </div>
        <label class="cmp-pick"><span>Place</span><select id="place">${opts}</select></label>
      </div>
      <div class="duo">
        <figure class="pane">
          <figcaption class="pane-head"><span class="tag pred">Predicted</span><span>Forecast issued <b>March ${y}</b>, before the monsoon</span></figcaption>
          <div class="cmap" id="map-pred"></div>
          <div class="keyline"><span><i class="swatch" style="background:#e2261c"></i>High alert</span>
            ${watchOn ? `<span><i class="swatch" style="background:#ffaa1e"></i>Watch</span>` : ""}
            <span class="muted">Satellite: Jan–Mar ${y}</span></div>
        </figure>
        <figure class="pane">
          <figcaption class="pane-head"><span class="tag act">What happened</span><span>Land that was river by <b>Jan–Mar ${y + 1}</b></span></figcaption>
          <div class="cmap" id="map-act"></div>
          <div class="keyline"><span><i class="swatch" style="background:#ff2424"></i>Farmland, homesteads or trees lost to the river</span>
            ${state.boxes ? `<span><i class="box" style="border-color:#e2261c"></i>Forecast squares</span>` : ""}
            <span class="muted">Satellite: Jan–Mar ${y + 1}</span></div>
        </figure>
      </div>
      ${figures(figs)}
      <div class="cmp-foot">
        <p class="fine">${ev ? `<b>In the news, ${esc(fdate(p.date))}:</b> ${esc(p.what)} ${sources(p.sources)}<br>` : ""}
          The forecast was made by a model that never saw ${y} or later. "Lost" means land with crops, homes or trees in the
          Jan–Mar ${y} Landsat map that was river in Jan–Mar ${y + 1}. Figures count the bank within ${km} km of the ${ev ? "union centre" : "place"}; the pictures show
          ${fmt(p.images.lost_ha_window)} ha lost across the whole frame. Each square is 240 m.<br>
          <b>Watch list${watchOn ? "" : " (hidden)"}:</b> ${wa.n} squares, ${wa.eroded} lost ${minHa} ha or more, ${pct(wa.loss_share)} of the loss.
          <b>Not flagged:</b> ${c.none.n} squares, ${pct(c.none.loss_share)} of the loss.</p>
        <div class="checks">
          <label class="fine check"><input type="checkbox" id="watch" ${watchOn ? "checked" : ""}> Show Watch squares</label>
          <label class="fine check"><input type="checkbox" id="boxes" ${state.boxes ? "checked" : ""}> Also draw the forecast squares on the right</label>
        </div>
      </div>`;
  }
  // High alert alone where it carries most of the loss; Watch too where it doesn't.
  const showWatch = (p) => state.watch ?? !(p.circle.high.loss_share >= 0.5);
  function drawCompare() {
    const box = $("compare");
    if (!S || !S.places.length) {
      box.innerHTML = `<div class="block"><h2 class="headline">No comparison yet</h2>
        <p class="body">Run <code>python run_showcase.py</code> to create <code>web/data/showcase.js</code>.</p></div>`;
      return;
    }
    if (duo) { duo.pred.remove(); duo.act.remove(); duo = null; }
    box.innerHTML = comparePage();
    const p = S.places.find((x) => x.id === state.place) || S.places[0];
    // Frame each map to the picture's own shape (Web Mercator), so nothing shows around it.
    const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
    const ratio = ((p.bounds[2] - p.bounds[0]) * Math.PI / 180) / (merc(p.bounds[3]) - merc(p.bounds[1]));
    box.querySelectorAll(".cmap").forEach((el) => { el.style.aspectRatio = String(ratio); });
    const { pred, act } = compareMaps();
    const [w, s, e, n] = p.bounds, bb = [[s, w], [n, e]];
    const img = (m, url, op = 1) => L.imageOverlay(url, bb, { opacity: op, interactive: false }).addTo(m);
    const tiers = showWatch(p) ? [p.images.watch, p.images.high] : [p.images.high];
    img(pred, p.images.before); tiers.forEach((u) => img(pred, u));
    img(act, p.images.after); img(act, p.images.actual);
    if (state.boxes) tiers.forEach((u) => img(act, u, 0.55));
    setTimeout(() => { pred.invalidateSize(); act.invalidateSize(); pred.fitBounds(bb, { padding: [0, 0] }); }, 0);
  }
  $("compare").addEventListener("change", (e) => {
    if (e.target.id === "place") { state.place = e.target.value; state.watch = null; store.set("place", state.place); }
    else if (e.target.id === "boxes") state.boxes = e.target.checked;
    else if (e.target.id === "watch") state.watch = e.target.checked;
    else return;
    drawCompare();
  });

  // ---- view: how it works (reading page) -------------------------------------------------
  function aboutPage() {
    const all = REP.tiers.all_rivers;
    return `<section>
        <h1>How Jamuna Bank Watch works</h1>
        <p class="lede">Every year the Jamuna and Padma take thousands of hectares of homes and fields. Nobody can stop the river,
          but people can move belongings, livestock and papers in time if they know which stretch of bank goes next.</p>
        <p>This prototype supports BWDB field checks, CEGIS's yearly prediction and local reporting. It doesn't replace any of them.</p>
      </section>
      <section>
        <h2>Three steps each year</h2>
        <ol class="flow">
          <li><span class="when">March, before the monsoon</span><b>Forecast</b><span>A model trained on dry-season Landsat maps since the 1980s
            gives every 240 m of bank a risk. <a href="#risk">See this year's</a></span></li>
          <li><span class="when">Every ~12 days in the monsoon</span><b>Detect</b><span>Sentinel-1 radar sees through cloud and shows where the bank
            is actually going. <a href="#radar">See the latest</a></span></li>
          <li><span class="when">When both agree</span><b>Warn</b><span>High-alert stretches with erosion confirmed nearby get a Bangla SMS or voice call.
            Simulated here.</span></li>
        </ol>
      </section>
      <section>
        <h2>The forecast</h2>
        <p>We turned each Jan–Mar since the 1980s into a water map (${REP.protocol.cell_m} m cells, ${Object.keys(REP.protocol.reaches).length} reaches
          of the Jamuna and Ganges–Padma) and trained a gradient-boosted tree model on what a stretch of farmland looks like the year
          before the river takes it: how close and how wide the channels are, whether they are moving closer, erosion nearby in past years,
          open water upstream, and how young the land is.</p>
      </section>
      <section>
        <h2>The radar check</h2>
        <p>Cloud hides the ground all monsoon, but radar sees through it. A Random Forest (100 decision trees, run in Google Earth Engine)
          learned to read Sentinel-1 radar as water, sand or land from clear days when optical images could check it. Erosion is counted
          only where the model is sure, the patch is at least 1 ha, touches the channel and was established land.</p>
      </section>
      <section>
        <h2>The warning</h2>
        <p>High-alert stretches, especially where radar confirms erosion nearby, would trigger a Bangla SMS and voice call to people in that
          240 m square, and a map to the Union Parishad, UNO and BWDB. Replies ("erosion started" or "not yet") become new training labels.
          Nothing is sent in this prototype.</p>
      </section>
      <section>
        <h2>How the test was kept fair</h2>
        ${protocolList("")}
      </section>
      <section>
        <h2>Limits</h2>
        <ul>
          <li><b>False alarms exist.</b> About ${pct(1 - all.high.precision)} of High alerts did not lose a full hectare in their own square in the test years.
            Use it to decide where to check first, not as a reason to evacuate on its own.</li>
          <li><b>Yearly maps say where, not when.</b> The radar check gives timing, but how many days of warning it gives is not measured yet.</li>
          <li><b>Bank protection isn't in the data.</b> Geobagged or revetted banks may be over-flagged.</li>
          <li><b>The radar model learned from optical labels</b> (Dynamic World and indices on clear days), not field surveys. Its mistakes carry
            over, and clear days are mostly in the dry season.</li>
          <li><b>Very young chars are not counted.</b> Land that was water in any of the last 5 dry seasons is left out of radar erosion. That keeps
            false alarms down but misses some real loss.</li>
          <li><b>News locations are union-level</b> because villages aren't on open maps, so comparisons use 3 km circles.</li>
          <li><b>The extraction-pit flag is a shape rule</b> for a person to check, never a finding.</li>
          <li>Image overlays are stretched onto the web map, so edges can be off by a few tens of metres.</li>
        </ul>
        <p>Code: <code>run_forecast.py</code>, <code>evaluate.py</code>, <code>detector/</code>. All thresholds live in
          <code>config.py</code> and <code>detector/evaluation.py</code>.</p>
      </section>`;
  }

  // ---- wiring -------------------------------------------------------------------------
  function scrollToMapOnPhone() {
    if (window.innerWidth <= 900) $("map").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  $("content").addEventListener("click", (e) => {
    const s = e.target.closest(".stretch");
    if (s) { pick(Number(s.dataset.i), true); return; }
    const x = e.target.closest("[data-fly-ex]");
    if (x) {
      e.preventDefault();
      const c = ((F.escalated || {})[state.site]?.cells || [])[Number(x.dataset.flyEx)];
      if (c) { map.setView([c.lat, c.lon], 14); scrollToMapOnPhone(); }
      return;
    }
    const f = e.target.closest("[data-fly]");
    if (f) {
      if (f.tagName === "A") e.preventDefault();
      const [la, lo] = f.dataset.fly.split(",").map(Number);
      map.setView([la, lo], 13); scrollToMapOnPhone();
      return;
    }
    if (e.target.id === "more") { state.showAll = !state.showAll; renderPanel(); return; }
    const run = e.target.closest("[data-run]");
    if (run) { state.run = run.dataset.run; render(); return; }
    const src = e.target.closest("[data-src]");
    if (src) { state.src = src.dataset.src; render(); }
  });
  $("content").addEventListener("input", (e) => {
    if (e.target.id === "fade") { state.fade = Number(e.target.value); applyFade(); }
  });

  function renderPanel() {
    const body = $("content");
    if (state.view === "risk") body.innerHTML = riskPanel();
    else if (state.view === "radar") body.innerHTML = radarPanel();
    else body.innerHTML = recordPanel();
  }
  function render() {
    document.querySelectorAll("#views a").forEach((a) => {
      if (a.dataset.view === state.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const about = document.querySelector(".about-link");
    about.textContent = state.view === "about" ? "Back to the map" : "How it works";
    about.href = state.view === "about" ? "#risk" : "#about";
    const cmpLink = document.querySelector(".cmp-link");
    cmpLink.textContent = state.view === "compare" ? "Back to the map" : "Predicted vs actual";
    cmpLink.href = state.view === "compare" ? "#risk" : "#compare";
    document.querySelectorAll("#sites button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.site === state.site)));
    const reading = state.view === "about", comparing = state.view === "compare";
    $("app").classList.toggle("reading", reading || comparing);
    $("page").hidden = !reading;
    $("compare").hidden = !comparing;
    if (comparing) { drawCompare(); return; }
    if (!F || !REP) {
      $("content").innerHTML = `<div class="block"><h2 class="headline">No forecast yet</h2>
        <p class="body">Run <code>python run_forecast.py --project &lt;id&gt;</code> to create <code>web/data/forecast.js</code>.</p></div>`;
      return;
    }
    if (reading) { $("page").innerHTML = aboutPage(); return; }
    if (state.view === "risk") drawRiskMap(state.site);
    else if (state.view === "radar") {
      const res = result(state.run, state.site);
      if (res && !res.error) drawRadarMap(res); else { clearMap(); legend("What changed", []); }
    } else drawRecordMap();
    renderPanel();
    setTimeout(() => map.invalidateSize(), 0);
  }

  $("sites").innerHTML = Object.entries(SITES).map(([id, name]) =>
    `<button type="button" data-site="${esc(id)}">${esc(name.replace(" upazila", ""))}${BANGLA[id] ? `<span class="bn" lang="bn">${BANGLA[id]}</span>` : ""}</button>`).join("");
  $("sites").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.site = b.dataset.site; state.picked = null; state.showAll = false; store.set("site", state.site);
    if (state.view === "about") location.hash = "risk"; else render();
  });
  window.addEventListener("hashchange", () => {
    const v = viewFromHash();
    if (v !== state.view) { state.view = v; render(); if (window.innerWidth > 900 || v === "about") window.scrollTo({ top: 0 }); }
  });
  if (R) $("generated").textContent = `Radar results generated ${R.generated}.`;
  render();
})();
