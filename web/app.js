/* Jamuna Bank Watch: a full-screen, three-step journey, then the details.
   Reads window.FORECAST (run_forecast.py), window.RESULTS (run_analysis.py, for upazila
   outlines) and window.SHOWCASE (run_showcase.py). Static: opens straight from disk.
   Step 1 this year's forecast · step 2 what a past forecast said · step 3 what really happened. */
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
  const CALM = matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (!F || !REP) {
    $("carousel").innerHTML = `<p class="sub empty">No forecast yet. Run <code>python run_forecast.py</code> to create <code>web/data/forecast.js</code>.</p>`;
    $("details").hidden = true;
    return;
  }

  // Numbers roll up to their value when a step opens. The final value is already in the HTML.
  function rollUp(root) {
    if (CALM) return;
    root.querySelectorAll("[data-count]").forEach((el) => {
      const to = Number(el.dataset.count), tail = el.dataset.tail || "", t0 = performance.now();
      const tick = (t) => {
        const k = Math.min(1, (t - t0) / 900), eased = 1 - Math.pow(1 - k, 3);
        el.textContent = fmt(Math.round(to * eased)) + tail;
        if (k < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  // ---- the journey ---------------------------------------------------------------------
  const hasPast = !!(S && S.places.length);
  const HASHES = ["#forecast", "#predicted", "#actual"];
  const TITLES = ["This year's forecast", "What a past forecast said", "What really happened"];
  const NEXT = ["Did it work before?", "What happened?", "Details"];
  const PREV = ["", "This year", "The forecast"];
  const last = hasPast ? 2 : 0;
  const stepBtns = [...document.querySelectorAll("#stepper button")];
  const slideFc = $("slide-forecast"), slidePa = $("slide-past");
  let cur = 0, entered = -1, revealTimer = 0, past = null;

  function setOverlays() {
    clearTimeout(revealTimer);
    if (!past) return;
    const show = (list, on) => list.forEach((o) => o.setOpacity(on ? 1 : 0));
    const later = (fn, ms) => { if (CALM) fn(); else revealTimer = setTimeout(fn, ms); };
    if (cur === 0) { show(past.pred, false); show(past.act, false); }
    else if (cur === 1) { show(past.act, false); later(() => show(past.pred, true), 500); }
    else { show(past.pred, true); show(past.act, false); later(() => show(past.act, true), 750); }
  }
  function enterStep() {
    slideFc.classList.toggle("on", cur === 0);
    slidePa.classList.toggle("on", cur > 0);
    slidePa.dataset.phase = cur === 2 ? "actual" : "predict";
    setOverlays();
    if (entered !== cur) {
      entered = cur;
      if (cur === 0) rollUp($("stats"));
      else rollUp(slidePa.querySelector(cur === 1 ? ".story" : ".t-act") || slidePa);
    }
    const act = slidePa.querySelector(".fig.act"), story = slidePa.querySelector(".story");
    if (act) act.inert = cur !== 2;
    if (story) story.inert = cur !== 1;
  }
  function goTo(i, opts = {}) {
    cur = Math.max(0, Math.min(last, i));
    $("track").style.transform = `translateX(${cur ? -100 : 0}%)`;
    slideFc.inert = cur !== 0; slidePa.inert = cur === 0;
    stepBtns.forEach((b, n) => {
      if (n === cur) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
      b.parentElement.classList.toggle("done", n <= cur);
    });
    $("prev").disabled = cur === 0;
    $("prev-label").textContent = PREV[cur];
    $("prev").setAttribute("aria-label", cur ? `Previous step: ${PREV[cur]}` : "Previous step");
    $("next-label").textContent = NEXT[cur];
    $("next").setAttribute("aria-label", cur === last ? "Go to the details" : `Next step: ${NEXT[cur]}`);
    $("next").classList.toggle("down", cur === last);
    $("announce").textContent = `Step ${cur + 1} of ${last + 1}: ${TITLES[cur]}`;
    if (!opts.quiet) history.replaceState(null, "", HASHES[cur]);
    enterStep();
  }
  function advance(d) {
    $("next").classList.remove("nudge");
    if (d > 0 && cur === last) { $("details").scrollIntoView({ behavior: CALM ? "auto" : "smooth" }); return; }
    goTo(cur + d);
  }
  $("stepper").addEventListener("click", (e) => {
    const b = e.target.closest("[data-step]");
    if (b) { $("next").classList.remove("nudge"); goTo(Number(b.dataset.step)); }
  });
  $("prev").addEventListener("click", () => advance(-1));
  $("next").addEventListener("click", () => advance(1));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    if (e.target.closest?.("select, input, textarea, .leaflet-container")) return;
    if (scrollY > innerHeight / 2) return;
    if (e.key === "ArrowLeft") advance(-1); else if (cur < last) advance(1);
  });
  // Swipe anywhere except on a map, where a drag must still pan.
  let swipeX = null, swipeY = null;
  $("viewport").addEventListener("touchstart", (e) => {
    const skip = e.target.closest(".leaflet-container, select, .list");
    swipeX = skip || e.touches.length > 1 ? null : e.touches[0].clientX;
    swipeY = e.touches[0].clientY;
  }, { passive: true });
  $("viewport").addEventListener("touchend", (e) => {
    if (swipeX == null) return;
    const dx = e.changedTouches[0].clientX - swipeX, dy = e.changedTouches[0].clientY - swipeY;
    swipeX = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && (dx > 0 || cur < last)) advance(dx < 0 ? 1 : -1);
  }, { passive: true });
  window.addEventListener("hashchange", () => {
    const i = HASHES.indexOf(location.hash); if (i >= 0) goTo(i, { quiet: true });
  });
  if (!hasPast) {
    stepBtns.slice(1).forEach((b) => { b.parentElement.hidden = true; });
    slidePa.hidden = true; $("prev").hidden = true; NEXT[0] = "Details";
  }

  // ---- step 1: this year's forecast ------------------------------------------------------
  const SITES = F.sites || (R && R.sites) || {};
  const siteName = (sid) => (SITES[sid] || sid).replace(" upazila", "");
  let site = SITES[store.get("site")] ? store.get("site") : ("chauhali" in SITES ? "chauhali" : Object.keys(SITES)[0]);
  $("year").textContent = $("year-h").textContent = REP.live.year;
  $("fc-sub").textContent = `Each colored stretch is ${REP.protocol.cell_m} m of bank, scored from ${F.issued_from} satellite images, before the rains.`;

  // Whole zoom levels only: Esri tiles show seams when Leaflet scales them to a fractional zoom.
  const map = L.map("map", { scrollWheelZoom: false, zoomControl: true }).setView([24.4, 89.72], 9);
  L.tileLayer(ESRI, { maxZoom: 18, attribution: "Esri, Maxar" }).addTo(map);
  (F.reaches || []).forEach((r) => {
    const [w, s, e, n] = r.bounds;
    L.imageOverlay(r.png, [[s, w], [n, e]], { opacity: 0.9, interactive: false }).addTo(map);
  });
  let siteLayer = L.layerGroup().addTo(map), markers = [], activeRow = -1, outline = null;

  function geo(sid) {
    const r = R && Object.values(R.results).find((x) => x.site === sid && x.bounds);
    return r && { bounds: r.bounds, boundary: r.boundary };
  }
  const why = (c) => (c.why.length ? cap(c.why[0]) : "Several smaller warning signs together");

  // The panel floats over the map's right side, so "centre" means the centre of what is left.
  function panelWidth() {
    if (innerWidth <= 860) return 0;
    const m = $("map").getBoundingClientRect(), p = document.querySelector(".panel").getBoundingClientRect();
    return Math.max(0, m.right - p.left);
  }
  function centerFor(latlng, zoom) {
    return map.unproject(map.project(latlng, zoom).add([panelWidth() / 2, 0]), zoom);
  }
  function fly(latlng, zoom, duration) {
    const c = centerFor(latlng, zoom);
    if (CALM) map.setView(c, zoom, { animate: false });
    else map.flyTo(c, zoom, { duration });
  }
  function overview(duration) {
    if (!outline) return;
    // One level closer than the upazila's outline, centred on the risk, with every pin on screen.
    const pad = L.point(panelWidth() + 80, 80), outer = outline.getBounds();
    const pts = [...markers.map((m) => m.getLatLng()), ...((F.escalated || {})[site]?.cells || []).map((c) => L.latLng(c.lat, c.lon))];
    const focus = pts.length ? L.latLngBounds(pts) : outer;
    fly(focus.getCenter(), Math.min(map.getBoundsZoom(outer, false, pad) + 1, map.getBoundsZoom(focus, false, pad)), duration);
  }
  function markRow(i) {
    activeRow = i;
    document.querySelectorAll("#list button").forEach((b) => {
      const on = Number(b.dataset.i) === i;
      if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    });
    markers.forEach((m, n) => m.getElement()?.classList.toggle("on", n === i));
  }

  function drawSite(duration) {
    siteLayer.clearLayers(); markers = []; activeRow = -1;
    const g = geo(site);
    outline = g ? L.geoJSON(g.boundary, { style: { color: "#fff", weight: 2, dashArray: "6 4", fill: false }, interactive: false }).addTo(siteLayer) : null;
    const cells = (F.watch[site] || []).slice(0, 5);
    cells.forEach((c, i) => {
      markers[i] = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "pin", html: `<span style="--i:${i}">${i + 1}</span>`, iconSize: [24, 24] }) })
        .bindPopup(`<b>${pct(c.risk)} chance</b> of losing land this monsoon<br>${c.why.map((x) => esc(cap(x))).join("<br>")}`)
        .on("click", () => markRow(i))
        .addTo(siteLayer);
    });
    ((F.escalated || {})[site]?.cells || []).forEach((c) =>
      L.circleMarker([c.lat, c.lon], { radius: 6, color: "#fff", weight: 2, fillColor: css("--map-high"), fillOpacity: 1 })
        .bindPopup(`<b>Erosion already seen here</b><br>Radar spotted ${fmt(c.detected_ha, 1)} ha of bank lost ${c.nearest_m} m from a flagged stretch.`)
        .addTo(siteLayer));

    const t = F.tier_counts[site] || {}, ex = (F.escalated || {})[site];
    const nEx = ex ? (ex.total ?? ex.cells.length) : 0, exDate = ex && fdate((ex.after_dates || []).slice(-1)[0]);
    $("stats").innerHTML = `
      <div class="stat"><i class="sw hi"></i><b data-count="${t.high || 0}">${fmt(t.high)}</b><span>High alert</span></div>
      <div class="stat"><i class="sw wa"></i><b data-count="${t.watch || 0}">${fmt(t.watch)}</b><span>Watch</span></div>
      ${ex ? `<div class="stat"><i class="sw ring"></i><b data-count="${nEx}">${fmt(nEx)}</b><span>Confirmed by radar${exDate ? `, ${esc(exDate)}` : ""}</span></div>` : ""}`;
    $("h-list").textContent = `Riskiest stretches in ${siteName(site)}`;
    $("list").innerHTML = cells.length ? cells.map((c, i) => `<li><button type="button" data-i="${i}" aria-label="Stretch ${i + 1}, ${pct(c.risk)} risk: ${esc(why(c))}">
        <span class="n">${i + 1}</span><span class="w">${esc(why(c))}</span><span class="r">${pct(c.risk)}</span></button></li>`).join("")
      : `<li class="none">No stretch is flagged in ${esc(siteName(site))}.</li>`;
    document.querySelectorAll("#sites button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.site === site)));
    overview(duration);
  }
  $("sites").innerHTML = Object.keys(SITES).map((id) => `<button type="button" data-site="${esc(id)}">${esc(siteName(id))}${
    BANGLA[id] ? ` <span lang="bn">${BANGLA[id]}</span>` : ""}</button>`).join("");
  $("sites").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    site = b.dataset.site; store.set("site", site); map.closePopup(); drawSite(1.2); rollUp($("stats"));
  });
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]"); if (!b) return;
    const i = Number(b.dataset.i), m = markers[i]; if (!m) return;
    map.closePopup();
    if (activeRow === i) { markRow(-1); overview(0.9); return; }   // a second click steps back out
    markRow(i);
    fly(m.getLatLng(), 14, 1.1);
    if (CALM) m.openPopup(); else map.once("moveend", () => { if (activeRow === i) m.openPopup(); });
  });

  // ---- steps 2 and 3: one past forecast, then what happened --------------------------------
  let duo = null;
  function drawCompare(id) {
    const box = slidePa;
    const p = S.places.find((x) => x.id === id) || S.places.find((x) => x.id === S.featured) || S.places[0];
    const c = p.circle, y = p.year, km = fmt(S.radius_m / 1000);
    const highLed = c.high.loss_share >= 0.5;
    const caught = highLed ? c.high.loss_share : c.high.loss_share + c.watch.loss_share;
    const nFlag = highLed ? c.high.n : c.high.n + c.watch.n;
    const nHit = highLed ? c.high.eroded : c.high.eroded + c.watch.eroded;
    const list = highLed ? "High alert" : "High alert or Watch";
    const newest = [...S.places].sort((a, b) => b.year - a.year);
    const opts = newest.map((x) => `<option value="${esc(x.id)}"${x.id === p.id ? " selected" : ""}>${esc(x.place)} · ${x.year}</option>`).join("");
    box.innerHTML = `
      <div class="pa-grid">
        <figure class="fig pred"><div class="cmap" id="map-pred"></div>
          <figcaption><b>Predicted</b> March ${y}<span class="long">, before the monsoon</span></figcaption>
          <p class="key"><span><i class="sw hi"></i>High alert</span>${highLed ? "" : `<span><i class="sw wa"></i>Watch</span>`}</p></figure>
        <div class="pa-right">
          <div class="story">
            <p class="eyebrow">Back to March ${y}</p>
            <h2>Before the rains, the AI marked this bank</h2>
            <p class="sub">${esc(p.place)}</p>
            <p class="big"><b data-count="${nFlag}">${fmt(nFlag)}</b> of ${fmt(c.cells)} stretches on the ${list} list</p>
          </div>
          <figure class="fig act"><div class="cmap" id="map-act"></div>
            <figcaption><b>What happened</b> One year later</figcaption>
            <p class="key"><span><i class="sw lost"></i>Land the river took</span></p></figure>
        </div>
      </div>
      <div class="strip">
        <div class="strip-text">
          <p class="t-pred sub">The model had never seen ${y} when it made this forecast.</p>
          <div class="t-act">
            <p class="headline"><b data-count="${Math.round(caught * 100)}" data-tail="%">${pct(caught)}</b> of the land lost was on the ${list} list, which covered ${pct(nFlag / c.cells)} of the bank.</p>
            <p class="fine">Within ${km} km: ${fmt(c.lost_ha, 1)} ha lost, ${fmt(nHit)} of ${fmt(nFlag)} flagged stretches eroded.
              ${p.kind === "event" && p.sources?.length ? `Reported in <a href="${esc(p.sources[0].url)}" target="_blank" rel="noopener">${esc(p.sources[0].title)}</a>.` : ""}
              One example. <a href="#score">See the overall score</a>.</p>
          </div>
        </div>
        <label class="pick"><span class="vh">Choose a place</span><select id="place">${opts}</select></label>
      </div>`;

    if (duo) { duo.a.remove(); duo.b.remove(); }
    // On touch screens these two maps let one finger scroll the page; pinch still zooms.
    const mk = (el) => L.map(el, { zoomControl: false, attributionControl: false, scrollWheelZoom: false,
      dragging: !L.Browser.mobile, zoomSnap: 0.1, maxZoom: 17 });
    duo = { a: mk("map-pred"), b: mk("map-act") };
    L.control.zoom({ position: "topright" }).addTo(duo.b);
    let busy = false;
    const follow = (a, b) => a.on("move", () => { if (busy) return; busy = true; b.setView(a.getCenter(), a.getZoom(), { animate: false }); busy = false; });
    follow(duo.a, duo.b); follow(duo.b, duo.a);
    const [w, s, e, n] = p.bounds, bb = [[s, w], [n, e]];
    const img = (m, url, o) => L.imageOverlay(url, bb, { interactive: false, opacity: o ?? 1, className: "fades" }).addTo(m);
    img(duo.a, p.images.before); img(duo.b, p.images.after);
    past = { pred: [...(highLed ? [] : [img(duo.a, p.images.watch, 0)]), img(duo.a, p.images.high, 0)],
             act: [img(duo.b, p.images.actual, 0)] };
    duo.a.invalidateSize(); duo.b.invalidateSize();
    // Fill the frame edge to edge; the images end at their bounds, so never show past them.
    duo.a.setView(L.latLngBounds(bb).getCenter(), duo.a.getBoundsZoom(bb, true), { animate: false });
  }
  if (hasPast) {
    slidePa.addEventListener("change", (e) => {
      if (e.target.id !== "place") return;
      store.set("place", e.target.value); drawCompare(e.target.value);
      entered = -1; enterStep();
      $("place").focus();
    });
    drawCompare(store.get("place"));
  }

  // ---- open: land on the requested step, then let the first one play --------------------
  const start = Math.max(0, HASHES.indexOf(location.hash));
  $("track").style.transition = "none";
  drawSite(start === 0 ? 2.2 : 0.01);
  goTo(start, { quiet: true });
  slideFc.classList.remove("on"); slidePa.classList.remove("on");
  requestAnimationFrame(() => requestAnimationFrame(() => { $("track").style.transition = ""; enterStep(); }));

  // ---- details: score --------------------------------------------------------------------
  // Quote the first version's figure: its inputs were fixed before the test years were looked at.
  const rk = REP.ranking.all_rivers, yrs = REP.protocol.test_years.join("–"), hi = REP.tiers.all_rivers.high;
  const rows = [
    ["AI forecast", rk.ai_version1.capture_top20, true],
    ["Rule: flag what eroded last year", rk.eroded_last_year.capture_top20],
    ["Rule: flag what is closest to the river", rk.closest_to_river.capture_top20],
  ];
  $("score-line").textContent = `Each method marked the riskiest 20% of the bank. In ${yrs}, years the AI never saw, this is how much of the land the Jamuna and Padma took fell inside that 20%.`;
  $("bars").innerHTML = rows.map(([label, v, me]) => `<div class="bar${me ? " me" : ""}">
      <span class="bl">${esc(label)}</span><span class="bt"><i style="width:${(v * 100).toFixed(1)}%" title="${(v * 100).toFixed(1)}%"></i></span><span class="bv">${pct(v)}</span></div>`).join("");
  $("score-fine").textContent = `${pct(hi.precision)} of High alert stretches lost land, about ${Math.round(hi.lift)}× the rate of an average stretch. That is about ${fmt(Math.round(hi.true_alerts_per_year / 50) * 50)} correct alerts and ${fmt(Math.round(hi.false_alarms_per_year / 50) * 50)} false alarms a year.`;
})();
