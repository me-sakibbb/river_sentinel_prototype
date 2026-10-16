/* Jamuna Bank Watch: one scrolling page.
   Reads window.FORECAST (run_forecast.py), window.RESULTS (run_analysis.py, for upazila
   outlines) and window.SHOWCASE (run_showcase.py). Static: opens straight from disk.
   1 forecast map · 2 predicted vs actual · 3 score vs simple rules · 4 how it works. */
(function () {
  const R = window.RESULTS, F = window.FORECAST, S = window.SHOWCASE;
  const REP = F && F.report;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fdate = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : (s || ""); };
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };
  const BANGLA = { kazipur: "কাজীপুর", chauhali: "চৌহালী" };
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

  if (!F || !REP) {
    $("forecast").innerHTML = `<p class="sub">No forecast yet. Run <code>python run_forecast.py</code> to create <code>web/data/forecast.js</code>.</p>`;
    return;
  }

  // ---- 1. forecast map ----------------------------------------------------------------
  const SITES = F.sites || (R && R.sites) || {};
  const siteName = (sid) => (SITES[sid] || sid).replace(" upazila", "");
  let site = SITES[store.get("site")] ? store.get("site") : ("chauhali" in SITES ? "chauhali" : Object.keys(SITES)[0]);
  $("year").textContent = REP.live.year;

  const map = L.map("map", { scrollWheelZoom: false, zoomControl: true }).setView([24.4, 89.72], 10);
  L.tileLayer(ESRI, { maxZoom: 18, attribution: "Esri, Maxar" }).addTo(map);
  (F.reaches || []).forEach((r) => {
    const [w, s, e, n] = r.bounds;
    L.imageOverlay(r.png, [[s, w], [n, e]], { opacity: 0.9, interactive: false }).addTo(map);
  });
  let siteLayer = L.layerGroup().addTo(map), markers = [];

  function geo(sid) {
    const r = R && Object.values(R.results).find((x) => x.site === sid && x.bounds);
    return r && { bounds: r.bounds, boundary: r.boundary };
  }
  const why = (c) => (c.why.length ? cap(c.why[0]) : "Several smaller warning signs together");

  function drawSite() {
    siteLayer.clearLayers(); markers = [];
    const g = geo(site);
    if (g) {
      L.geoJSON(g.boundary, { style: { color: "#fff", weight: 2, dashArray: "6 4", fill: false }, interactive: false }).addTo(siteLayer);
      const [w, s, e, n] = g.bounds; map.fitBounds([[s, w], [n, e]]);
    }
    const cells = (F.watch[site] || []).slice(0, 5);
    cells.forEach((c, i) => {
      markers[i] = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "pin", html: `<span>${i + 1}</span>`, iconSize: [24, 24] }) })
        .bindPopup(`<b>${pct(c.risk)} chance</b> of losing land this monsoon<br>${c.why.map((x) => esc(cap(x))).join("<br>")}`)
        .addTo(siteLayer);
    });
    ((F.escalated || {})[site]?.cells || []).forEach((c) =>
      L.circleMarker([c.lat, c.lon], { radius: 6, color: "#fff", weight: 2, fillColor: css("--high"), fillOpacity: 1 })
        .bindPopup(`<b>Erosion already seen here</b><br>Radar spotted ${fmt(c.detected_ha, 1)} ha of bank lost ${c.nearest_m} m from a flagged stretch.`)
        .addTo(siteLayer));

    const t = F.tier_counts[site] || {}, ex = (F.escalated || {})[site];
    const nEx = ex ? (ex.total ?? ex.cells.length) : 0, exDate = ex && fdate((ex.after_dates || []).slice(-1)[0]);
    $("stats").innerHTML = `
      <div class="stat hi"><b>${fmt(t.high)}</b><span>stretches on <strong>High alert</strong></span></div>
      <div class="stat wa"><b>${fmt(t.watch)}</b><span>stretches on <strong>Watch</strong></span></div>
      ${ex ? `<div class="stat"><b>${fmt(nEx)}</b><span>of these where radar has already seen erosion${exDate ? ` (${esc(exDate)})` : ""}</span></div>` : ""}`;
    $("list").innerHTML = cells.length ? cells.map((c, i) => `<li><button type="button" data-i="${i}">
        <span class="n">${i + 1}</span><span class="w">${esc(why(c))}</span><span class="r">${pct(c.risk)}</span></button></li>`).join("")
      : `<li class="none">No stretch is flagged in ${esc(siteName(site))}.</li>`;
    document.querySelectorAll("#sites button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.site === site)));
  }
  $("sites").innerHTML = Object.keys(SITES).map((id) => `<button type="button" data-site="${esc(id)}">${esc(siteName(id))}${
    BANGLA[id] ? ` <span lang="bn">${BANGLA[id]}</span>` : ""}</button>`).join("");
  $("sites").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    site = b.dataset.site; store.set("site", site); drawSite();
  });
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]"); if (!b) return;
    const m = markers[Number(b.dataset.i)]; if (!m) return;
    map.setView(m.getLatLng(), 14); m.openPopup();
    if (window.innerWidth <= 860) $("map").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  drawSite();

  // ---- 2. predicted vs actual -------------------------------------------------------------
  let duo = null;
  function drawCompare(id) {
    const box = $("compare");
    if (!S || !S.places.length) { $("proof").hidden = true; return; }
    const p = S.places.find((x) => x.id === id) || S.places.find((x) => x.id === S.featured) || S.places[0];
    const c = p.circle, y = p.year, km = fmt(S.radius_m / 1000);
    const highLed = c.high.loss_share >= 0.5;
    const caught = highLed ? c.high.loss_share : c.high.loss_share + c.watch.loss_share;
    const flagged = highLed ? c.high.n / c.cells : (c.high.n + c.watch.n) / c.cells;
    const list = highLed ? "High alert" : "High alert or Watch";
    const opts = S.places.map((x) => `<option value="${esc(x.id)}"${x.id === p.id ? " selected" : ""}>${esc(x.place)} · ${x.year}</option>`).join("");
    box.innerHTML = `
      <div class="cmp-bar">
        <p class="cmp-line">${esc(p.place)}, ${y}: <b>${pct(caught)} of the land lost</b> was on the forecast's ${list} list,
          which covered only ${pct(flagged)} of the bank.</p>
        <label class="pick"><span>Try another place</span><select id="place">${opts}</select></label>
      </div>
      <div class="duo">
        <figure><figcaption><span class="pill pred">Predicted</span> March ${y}, before the monsoon</figcaption><div class="cmap" id="map-pred"></div>
          <p class="key"><span><i style="background:#e2261c"></i>High alert</span>${highLed ? "" : `<span><i style="background:#ffaa1e"></i>Watch</span>`}</p></figure>
        <figure><figcaption><span class="pill act">What happened</span> One year later</figcaption><div class="cmap" id="map-act"></div>
          <p class="key"><span><i style="background:#ff2424"></i>Land the river took</span></p></figure>
      </div>
      <p class="fine">Figures count the bank within ${km} km of the place. ${fmt(c.lost_ha, 1)} ha of farmland and homes were lost there.
        ${p.kind === "event" && p.sources?.length ? `Reported in <a href="${esc(p.sources[0].url)}" target="_blank" rel="noopener">${esc(p.sources[0].title)}</a>.` : ""}</p>`;

    if (duo) { duo.a.remove(); duo.b.remove(); }
    const mk = (el) => L.map(el, { zoomControl: false, attributionControl: false, scrollWheelZoom: false, zoomSnap: 0.1, maxZoom: 17 });
    duo = { a: mk("map-pred"), b: mk("map-act") };
    L.control.zoom({ position: "topright" }).addTo(duo.b);
    let busy = false;
    const follow = (a, b) => a.on("move", () => { if (busy) return; busy = true; b.setView(a.getCenter(), a.getZoom(), { animate: false }); busy = false; });
    follow(duo.a, duo.b); follow(duo.b, duo.a);
    const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
    const ratio = ((p.bounds[2] - p.bounds[0]) * Math.PI / 180) / (merc(p.bounds[3]) - merc(p.bounds[1]));
    box.querySelectorAll(".cmap").forEach((el) => { el.style.aspectRatio = String(ratio); });
    const [w, s, e, n] = p.bounds, bb = [[s, w], [n, e]];
    const img = (m, url) => L.imageOverlay(url, bb, { interactive: false }).addTo(m);
    img(duo.a, p.images.before); if (!highLed) img(duo.a, p.images.watch); img(duo.a, p.images.high);
    img(duo.b, p.images.after); img(duo.b, p.images.actual);
    setTimeout(() => { duo.a.invalidateSize(); duo.b.invalidateSize(); duo.a.fitBounds(bb, { padding: [0, 0] }); }, 0);
  }
  $("compare").addEventListener("change", (e) => {
    if (e.target.id === "place") { store.set("place", e.target.value); drawCompare(e.target.value); }
  });
  drawCompare(store.get("place"));

  // ---- 3. score -------------------------------------------------------------------------
  // Quote the first version's figure: its inputs were fixed before the test years were looked at.
  const rk = REP.ranking.all_rivers, yrs = REP.protocol.test_years.join("–");
  const rows = [
    ["AI forecast", rk.ai_version1.capture_top20, true],
    ["Rule: flag what eroded last year", rk.eroded_last_year.capture_top20],
    ["Rule: flag what is closest to the river", rk.closest_to_river.capture_top20],
  ];
  $("h-score").textContent = `The AI caught ${pct(rows[0][1])} of the land lost, more than simple rules`;
  $("score-sub").textContent = `Each method marked the riskiest 20% of the bank. Then we checked how much of the land the Jamuna and Padma took in ${yrs}, years the AI never saw, fell inside that 20%.`;
  $("bars").innerHTML = rows.map(([label, v, me]) => `<div class="bar${me ? " me" : ""}">
      <span class="bl">${esc(label)}</span><span class="bt"><i style="width:${(v * 100).toFixed(1)}%"></i></span><span class="bv">${pct(v)}</span></div>`).join("")
    + `<p class="fine">A stretch on High alert was about ${Math.round(REP.tiers.all_rivers.high.lift)}× as likely to erode as an average one.</p>`;
})();
