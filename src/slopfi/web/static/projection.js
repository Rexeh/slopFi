// Projection: one true stacked area whose top edge is net worth, the in today's money line, an optional compare
// scenario, milestone dots with DOM labels, and the levers that redraw it live against a ghost of the saved scenario.
(function () {
  "use strict";
  const doc = document;
  const data = JSON.parse(doc.getElementById("proj-data").textContent);
  const saved = JSON.parse(doc.getElementById("proj-saved").textContent);
  const canvas = doc.getElementById("chart-projection");
  if (!canvas || !window.Chart) return;

  // ------------------------------------------------------------- formats
  const css = (name) => getComputedStyle(doc.documentElement).getPropertyValue("--" + name).trim();
  const MINUS = "−";
  const gbp = (v) => (v < 0 ? MINUS : "") + "£" + Math.round(Math.abs(v)).toLocaleString("en-GB");
  const signed = (v) => (v > 0 ? "+" : v < 0 ? MINUS : "") + "£" + Math.round(Math.abs(v)).toLocaleString("en-GB");
  const pence = (v) => (v < 0 ? MINUS : "") + "£" + Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function compact(v) {
    const a = Math.abs(v), s = v < 0 ? MINUS : "";
    if (a >= 1e6) return s + "£" + (a / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (a >= 1e5) return s + "£" + (a / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return s + "£" + Math.round(a).toLocaleString("en-GB");
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthLabel = (iso) => MONTHS[parseInt(iso.slice(5, 7), 10) - 1] + " " + iso.slice(0, 4);
  function alpha(hex, a) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  if (reduced.matches) Chart.defaults.animation = false;

  // ------------------------------------------------------------- datasets
  const classes = data.classes, B = classes.length;
  const msMonths = data.milestones.map((m) => m.month);
  const onlyAt = (series) => series.map((v, i) => (msMonths.includes(i) ? v : null));
  const IDX = { real: B, compare: data.compare ? B + 1 : -1 };
  IDX.ghost = data.compare ? B + 2 : B + 1;
  IDX.live = IDX.ghost + 1;
  IDX.ms = IDX.live + 1;

  function buildDatasets() {
    const surface = css("color-surface");
    const ds = classes.map((c, i) => ({
      label: c, data: data.series[c].slice(), stack: "bands", order: 3, fill: i === 0 ? "origin" : "-1",
      backgroundColor: css(data.swatches[c]), borderColor: surface, borderWidth: 2, tension: 0, pointRadius: 0, pointHitRadius: 0,
    }));
    ds.push({ label: "In today's money", data: data.net_worth_real.slice(), stack: "real", order: 1, borderColor: css("color-ink"),
      borderDash: [6, 4], borderWidth: 2, pointRadius: 0, pointHitRadius: 0, tension: 0, fill: false });
    if (data.compare) ds.push({ label: data.compare_name, data: data.compare.slice(), stack: "compare", order: 1, borderColor: css("color-ink-2"),
      borderWidth: 2, pointRadius: 0, pointHitRadius: 0, tension: 0, fill: false });
    ds.push({ label: "Saved scenario", data: data.net_worth.slice(), stack: "ghost", order: 2, borderColor: css("color-ink-3"), borderWidth: 2,
      pointRadius: 0, pointHitRadius: 0, tension: 0, fill: false, hidden: true });
    ds.push({ label: "Net worth", data: data.net_worth.slice(), stack: "live", order: 2, borderColor: "transparent", borderWidth: 0, pointRadius: 0,
      pointHitRadius: 0, tension: 0, hidden: true,
      fill: { target: IDX.ghost, above: alpha(css("color-positive"), 0.25), below: alpha(css("color-negative"), 0.25) } });
    ds.push({ label: "Milestone", data: onlyAt(data.net_worth), stack: "ms", order: 0, showLine: false, spanGaps: false, pointRadius: 4,
      pointHoverRadius: 5, pointBackgroundColor: css("color-ink"), pointBorderColor: surface, pointBorderWidth: 2, pointHitRadius: 6 });
    return ds;
  }

  // ------------------------------------------------------------ DOM labels
  const wrap = canvas.parentElement, labels = doc.getElementById("chart-labels"), foot = doc.getElementById("chart-foot");
  let payoffEl = doc.getElementById("payoff");
  function topEdge(i) { return classes.reduce((s, c) => s + (chart.data.datasets[classes.indexOf(c)].data[i] || 0), 0); }
  function placeLabels(ch) {
    const { x, y } = ch.scales, area = ch.chartArea, w = wrap.clientWidth;
    labels.replaceChildren();
    const frag = doc.createDocumentFragment();
    msMonths.forEach((m) => {
      const px = x.getPixelForValue(m), v = topEdge(m), py = y.getPixelForValue(v);
      const s = doc.createElement("span");
      s.className = "ms-label"; s.textContent = compact(v);
      s.style.left = Math.min(Math.max(px, 24), w - 24) + "px"; s.style.top = Math.max(py, 12) + "px";
      frag.appendChild(s);
    });
    if (w >= 520) {
      const last = ch.data.labels.length - 1, ends = [];
      let low = 0;
      classes.forEach((c, i) => {
        const v = ch.data.datasets[i].data[last] || 0;
        if (Math.abs(v) >= 1) ends.push({ name: c, y: y.getPixelForValue(low + v / 2) });
        low += v;
      });
      ends.sort((a, b) => b.y - a.y);
      for (let i = 1; i < ends.length; i++) if (ends[i - 1].y - ends[i].y < 13) ends[i].y = ends[i - 1].y - 13;
      ends.forEach((e) => {
        const s = doc.createElement("span");
        s.className = "end-label"; s.textContent = e.name;
        s.style.left = area.right + 6 + "px"; s.style.top = e.y + "px";
        frag.appendChild(s);
      });
    }
    labels.appendChild(frag);
    placePayoff(ch);
  }
  function placePayoff(ch) {
    if (!data.payoff) { if (payoffEl) payoffEl.remove(); payoffEl = null; return; }
    if (!payoffEl) { payoffEl = doc.createElement("span"); payoffEl.className = "payoff"; payoffEl.id = "payoff"; foot.appendChild(payoffEl); }
    payoffEl.textContent = "Mortgage paid off · " + data.payoff.label;
    const px = ch.scales.x.getPixelForValue(data.payoff.month), w = foot.clientWidth, half = payoffEl.offsetWidth / 2 || 70;
    payoffEl.classList.toggle("at-left", px - half < 0);
    payoffEl.classList.toggle("at-right", px + half > w);
    payoffEl.style.left = px + "px";
  }
  const domLabels = { id: "domLabels", afterDraw: placeLabels };

  // --------------------------------------------------------------- tooltip
  const tip = doc.getElementById("chart-tip");
  function externalTip(ctx) {
    const t = ctx.tooltip;
    if (!t || t.opacity === 0 || !t.dataPoints || !t.dataPoints.length) { tip.hidden = true; return; }
    const i = t.dataPoints[0].dataIndex, ds = chart.data.datasets;
    const rows = [["Net worth", gbp(topEdge(i)), null, true], ["In today's money", gbp(ds[IDX.real].data[i]), "ink", false]];
    if (IDX.compare >= 0) rows.push([data.compare_name, gbp(ds[IDX.compare].data[i]), "ink-2", false]);
    if (!ds[IDX.ghost].hidden) rows.push(["Saved scenario", gbp(ds[IDX.ghost].data[i]), "ink-3", false]);
    for (let k = B - 1; k >= 0; k--) rows.push([classes[k], gbp(ds[k].data[i]), data.swatches[classes[k]], false, true]);
    tip.innerHTML = `<div class="tip-head">${monthLabel(data.labels[i])}</div>` + rows.map(([k, v, sw, strong, rect]) =>
      `<div class="tip-row"><span class="tip-val">${strong ? "<b>" + v + "</b>" : v}</span><span class="tip-key${sw && !rect ? " line" : ""}${k === "In today's money" ? " dashed" : ""}"${sw ? ` data-sw="${sw}"` : ""}>${k}</span></div>`).join("");
    tip.hidden = false;
    const w = wrap.clientWidth, tw = tip.offsetWidth, x = t.caretX, y = t.caretY;
    tip.style.left = (x + 16 + tw > w ? x - 16 - tw : x + 16) + "px";
    tip.style.top = Math.max(0, Math.min(y - 20, wrap.clientHeight - tip.offsetHeight)) + "px";
  }

  // ----------------------------------------------------------------- chart
  function themeDefaults() {
    Chart.defaults.font.family = css("font-ui") || Chart.defaults.font.family;
    Chart.defaults.font.size = 11;
    Chart.defaults.color = css("chart-ink");
  }
  themeDefaults();
  const padRight = () => (wrap.clientWidth >= 520 ? 88 : 8);
  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: data.labels, datasets: buildDatasets() },
    options: {
      maintainAspectRatio: false, animation: reduced.matches ? false : undefined,
      layout: { padding: { top: 28, right: padRight(), left: 0, bottom: 0 } },
      interaction: { mode: "index", intersect: false },
      onResize(ch) { ch.options.layout.padding.right = padRight(); },
      plugins: { legend: { display: false }, tooltip: { enabled: false, external: externalTip } },
      scales: {
        x: { grid: { display: false }, border: { display: false },
             ticks: { autoSkip: false, maxRotation: 0, callback: (v, i) => (i % 12 === 0 ? data.labels[i].slice(0, 4) : null) } },
        y: { stacked: true, min: 0, grid: { color: css("chart-grid"), lineWidth: 1, drawTicks: false }, border: { display: false },
             ticks: { callback: (v) => compact(v), maxTicksLimit: 8 } },
      },
    },
    plugins: [domLabels],
  });

  function applyTheme() {
    themeDefaults();
    const ds = chart.data.datasets, surface = css("color-surface");
    classes.forEach((c, i) => { ds[i].backgroundColor = css(data.swatches[c]); ds[i].borderColor = surface; });
    ds[IDX.real].borderColor = css("color-ink");
    if (IDX.compare >= 0) ds[IDX.compare].borderColor = css("color-ink-2");
    ds[IDX.ghost].borderColor = css("color-ink-3");
    ds[IDX.live].fill = { target: IDX.ghost, above: alpha(css("color-positive"), 0.25), below: alpha(css("color-negative"), 0.25) };
    ds[IDX.ms].pointBackgroundColor = css("color-ink"); ds[IDX.ms].pointBorderColor = surface;
    chart.options.scales.y.grid.color = css("chart-grid");
    chart.update();
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  new MutationObserver(applyTheme).observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // ---------------------------------------------------------- table toggle
  const toggle = doc.getElementById("chart-table-toggle"), area = doc.getElementById("chart-projection-wrap"), twin = doc.getElementById("twin-projection");
  if (toggle) toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    twin.hidden = !open; area.hidden = open;
    toggle.querySelector(".btn-label").textContent = open ? "Chart" : "Table";
    toggle.querySelector("use").setAttribute("href", open ? "#i-projection" : "#i-table");
    if (!open) chart.resize();
  });

  // ---------------------------------------------------------------- levers
  const form = doc.getElementById("levers-form");
  if (!form) return;
  const sliders = Array.from(form.querySelectorAll("input.lever"));
  const mode = doc.getElementById("surplus-mode"), override = doc.getElementById("surplus-override"), target = doc.getElementById("surplus-target");
  const bar = doc.getElementById("save-bar"), barText = doc.getElementById("save-bar-text"), status = doc.getElementById("lever-status");
  const ghostKey = doc.getElementById("ghost-key"), tiles = Array.from(doc.querySelectorAll("#tiles .strip-item"));
  const arrow = (up) => `<svg class="icon icon-sm" viewBox="0 0 20 20" aria-hidden="true"><use href="#i-arrow-${up ? "up" : "down"}"/></svg>`;
  let timer = null, announce = false, dirty = false, last = null;

  function changedLevers() { return sliders.filter((el) => el.value !== el.dataset.saved).length; }
  function surplusChanged() {
    return mode.value !== mode.dataset.saved || override.value.trim() !== override.dataset.saved || target.value !== target.dataset.saved;
  }
  function updateRows() {
    let total = 0;
    sliders.forEach((el) => {
      const tr = el.closest("tr"), avg = parseFloat(tr.dataset.avg), pct = parseInt(el.value, 10), s = avg * pct / 100;
      total += s;
      tr.querySelector(".pct").textContent = pct + "%";
      tr.querySelector(".saved").textContent = gbp(s);
      el.setAttribute("aria-valuetext", `${pct}%, saves ${gbp(s)} a month`);
    });
    doc.getElementById("saved-total").textContent = gbp(total);
    return total;
  }
  function showGhost(on) {
    const ds = chart.data.datasets;
    ds[IDX.ghost].hidden = !on; ds[IDX.live].hidden = !on;
    ghostKey.hidden = !on;
  }
  function query() {
    const p = new URLSearchParams({ scenario: saved.scenario, savings: sliders.map((el) => el.name.split("__")[1] + ":" + el.value).join(","),
      surplus_mode: mode.value, surplus_target: target.value });
    const ov = override.value.trim();
    if (ov) p.set("surplus_override", ov);
    return p;
  }
  function paintTiles(d) {
    d.milestones.forEach((m, i) => {
      const tile = tiles.find((t) => t.dataset.year === String(m.year));
      if (!tile) return;
      const delta = m.net_worth - data.milestones[i].net_worth, el = tile.querySelector(".strip-delta");
      tile.querySelector(".figure").textContent = compact(m.net_worth);
      tile.querySelector(".real").textContent = gbp(m.net_worth_real);
      if (Math.abs(delta) < 0.5) { el.className = "strip-delta is-same"; el.textContent = "Matches saved scenario"; }
      else { el.className = "strip-delta " + (delta > 0 ? "pos" : "neg"); el.innerHTML = arrow(delta > 0) + signed(delta) + " vs saved"; }
      const row = doc.querySelector(`#milestone-table tr[data-year="${m.year}"]`);
      if (row) {
        row.querySelector(".nw").innerHTML = "<b>" + gbp(m.net_worth) + "</b>";
        row.querySelector(".real").textContent = gbp(m.net_worth_real);
        row.querySelector(".liquid").textContent = gbp(m.liquid);
        row.querySelector(".equity").textContent = gbp(m.equity);
        row.querySelector(".mortgage").textContent = gbp(m.mortgage);
        row.querySelector(".contrib").textContent = gbp(m.contributions);
        row.querySelector(".growth").textContent = signed(m.gain - m.contributions);
      }
    });
  }
  function paintPlan(d) {
    const p = d.plan, notice = doc.getElementById("plan-notice");
    if (!notice) return;
    notice.hidden = !p.exceeds;
    doc.getElementById("plan-planned").textContent = gbp(p.planned) + " a month";
    doc.getElementById("plan-parts").textContent = `${gbp(p.contributions)} contributions plus ${gbp(p.from_levers)} from levers`;
    doc.getElementById("plan-gap").textContent = gbp(p.gap || 0);
    doc.getElementById("plan-surplus").textContent = gbp(p.surplus || 0);
  }
  function paintBar() {
    const n = changedLevers(), s = surplusChanged();
    dirty = n > 0 || s;
    bar.hidden = !dirty;
    showGhost(dirty);
    if (!dirty) return;
    const extra = last ? last.levers.extra_monthly : saved.extra_monthly;
    const what = n ? `${n} lever${n === 1 ? "" : "s"} changed` : "Surplus changed";
    barText.textContent = `${what} · ${gbp(extra)} a month`;
  }
  function sentence(d) {
    const m = d.milestones[d.milestones.length - 1], base = data.milestones[data.milestones.length - 1];
    if (!m) return "";
    const delta = m.net_worth - base.net_worth;
    const vs = Math.abs(delta) < 0.5 ? "the same as the saved scenario" : `${compact(Math.abs(delta))} ${delta > 0 ? "more" : "less"} than the saved scenario`;
    return `Levers save ${gbp(d.levers.saved_total)} a month; in ${m.year} years ${compact(m.net_worth)}, ${vs}.`;
  }
  let pending = 0;
  async function recalc() {
    // The tile row carries the htmx busy rule while the server works (150ms dim, 600ms bar); the levers never block.
    const tiles = doc.getElementById("tiles"), mine = ++pending;
    const done = window.FF && FF.busy && tiles ? FF.busy(tiles) : () => {};
    let res, d;
    try {
      res = await fetch("/api/projection?" + query().toString());
      d = res.ok ? await res.json() : null;
    } catch (e) { d = null; } finally { done(); }
    if (!d || mine !== pending) return;                       // a newer recalculation supersedes this one
    last = d;
    const ds = chart.data.datasets;
    classes.forEach((c, i) => { ds[i].data = d.series[c]; });
    ds[IDX.real].data = d.net_worth_real;
    ds[IDX.live].data = d.net_worth;
    ds[IDX.ms].data = onlyAt(d.net_worth);
    data.payoff = d.payoff;
    paintBar();
    chart.update(announce ? undefined : "none");             // live while dragging; eases on release (chart settle)
    paintTiles(d);
    paintPlan(d);
    doc.getElementById("lev-saved").textContent = pence(d.levers.saved_total);
    doc.getElementById("lev-surplus").textContent = pence(d.levers.surplus_used);
    doc.getElementById("lev-extra").textContent = pence(d.levers.extra_monthly);
    doc.getElementById("fact-levers").textContent = gbp(d.levers.extra_monthly) + " a month";
    const po = doc.getElementById("milestone-payoff");
    if (po) po.textContent = d.payoff ? `Mortgage paid off in month ${d.payoff.month}, ${d.payoff.label}` : "";
    const lastM = d.milestones[d.milestones.length - 1];
    if (lastM) canvas.setAttribute("aria-label", canvas.getAttribute("aria-label").replace(/£[\d.,]+[kM]?/, compact(lastM.net_worth)));
    if (announce) { status.textContent = dirty ? sentence(d) : "Back to the saved scenario."; announce = false; }
  }
  function schedule() {
    updateRows();
    paintBar();
    clearTimeout(timer);
    timer = setTimeout(recalc, 150);
  }
  sliders.forEach((el) => {
    el.addEventListener("input", schedule);
    el.addEventListener("change", () => { announce = true; schedule(); });
  });
  [mode, target].forEach((el) => el.addEventListener("change", () => { announce = true; schedule(); }));
  override.addEventListener("input", schedule);
  override.addEventListener("change", () => { announce = true; schedule(); });

  doc.getElementById("levers-reset").addEventListener("click", () => {
    sliders.forEach((el) => { el.value = el.dataset.saved; });
    mode.value = mode.dataset.saved; override.value = override.dataset.saved; target.value = target.dataset.saved;
    updateRows();
    announce = true;
    clearTimeout(timer);
    recalc();
    if (sliders[0]) sliders[0].focus();
  });
})();
