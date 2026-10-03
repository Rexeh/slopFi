// Spending page: six small multiples, the stacked matrix, sub-category toggles and the chart/table twin.
// Tokens are read from CSS and the charts rebuilt when the scheme changes; no colours live here (as dashboard.js).
(function () {
  "use strict";
  const doc = document, root = doc.documentElement;
  const node = doc.getElementById("spending-data");

  // Sub-category rows: collapsed by default, remembered per category. Runs even if Chart.js failed to load.
  let open = {};
  try { open = JSON.parse(localStorage.getItem("spending.subs") || "{}") || {}; } catch (e) { open = {}; }
  doc.querySelectorAll(".toggle-sub").forEach((b) => {
    const id = b.dataset.sub;
    const set = (on) => { b.setAttribute("aria-expanded", String(on)); doc.querySelectorAll(`[data-sub-of="${id}"]`).forEach((r) => { r.hidden = !on; }); };
    set(Boolean(open[id]));
    b.addEventListener("click", () => {
      const on = b.getAttribute("aria-expanded") !== "true";
      set(on);
      if (on) open[id] = 1; else delete open[id];
      try { localStorage.setItem("spending.subs", JSON.stringify(open)); } catch (e) { /* private mode */ }
    });
  });

  if (!node || typeof Chart === "undefined") return;
  const data = JSON.parse(node.textContent);
  const css = (n) => getComputedStyle(root).getPropertyValue(n).trim();
  const gbp = (v) => (v < 0 ? "−" : "") + "£" + Math.round(Math.abs(v)).toLocaleString("en-GB");
  const compact = (v) => {
    const a = Math.abs(v), sign = v < 0 ? "−" : "";
    if (a >= 1000) return sign + "£" + (a / 1000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "") + "k";
    return sign + "£" + Math.round(a);
  };
  const twoLine = (l) => (l.endsWith(" (partial)") ? [l.replace(" (partial)", ""), "(partial)"] : l);   // no rotation
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const charts = [];

  // 45° hatch of the surface colour over the series colour, 4px pitch: partial months, honest in both schemes.
  function hatch(colour) {
    const c = doc.createElement("canvas"); c.width = c.height = 4;
    const ctx = c.getContext("2d");
    ctx.fillStyle = colour; ctx.fillRect(0, 0, 4, 4);
    ctx.strokeStyle = css("--color-surface"); ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(0, 4); ctx.lineTo(4, 0);
    ctx.moveTo(-1, 1); ctx.lineTo(1, -1);
    ctx.moveTo(3, 5); ctx.lineTo(5, 3);
    ctx.stroke();
    return ctx.createPattern(c, "repeat");
  }

  // One external tooltip per chart: value first, then the label, with a rect or line key per series.
  function tooltip(wrap, keyShape, titleOf) {
    let tip = wrap.querySelector(".chart-tip");
    if (!tip) { tip = doc.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; tip.setAttribute("aria-hidden", "true"); wrap.appendChild(tip); }
    return {
      enabled: false, mode: "index", intersect: false,
      external(ctx) {
        const model = ctx.tooltip;
        if (!model || model.opacity === 0) { tip.hidden = true; return; }
        const points = model.dataPoints.filter((p) => p.raw !== null && p.raw !== undefined && (p.parsed.y !== 0 || model.dataPoints.length === 1));
        const rows = points.slice().reverse().map((p) => {
          const shape = keyShape(p.dataset);
          const colour = shape === "line" ? p.dataset.borderColor : p.dataset.plainColour;
          return `<div class="tip-row"><i class="tip-key tip-${shape}" style="--key:${colour}"></i><b>${gbp(p.parsed.y)}</b><span>${p.dataset.label || ""}</span></div>`;
        });
        tip.innerHTML = `<div class="tip-title"></div>${rows.join("")}`;
        tip.querySelector(".tip-title").textContent = titleOf(model);
        tip.hidden = false;
        const w = wrap.clientWidth, tw = tip.offsetWidth, x = model.caretX, y = model.caretY;
        tip.style.left = Math.min(Math.max(8, x - tw / 2), w - tw - 8) + "px";
        tip.style.top = Math.max(0, y - tip.offsetHeight - 12) + "px";
      },
    };
  }

  function legend(el, items) {
    if (!el) return;
    el.innerHTML = items.map((i) => `<span><i class="tip-key tip-${i.shape}" style="--key:${i.colour}"></i>${i.label}</span>`).join("");
  }

  // The previous-window average label sits on its rule, at whichever end the line is further from, and above the
  // rule unless the line is just above it there.
  function placeRef(chart, item, ref) {
    if (!ref || item.prev_avg === null) return;
    const y = chart.scales.y, area = chart.chartArea, ruleY = y.getPixelForValue(item.prev_avg);
    const first = y.getPixelForValue(item.values[0]), last = y.getPixelForValue(item.values[item.values.length - 1]);
    const atLeft = Math.abs(first - ruleY) > Math.abs(last - ruleY);
    const lineY = atLeft ? first : last;
    const above = !(lineY < ruleY && ruleY - lineY < 18) && ruleY - area.top > 14;
    ref.classList.toggle("below", !above);
    ref.style.top = ruleY + "px";
    if (atLeft) { ref.style.left = area.left + 2 + "px"; ref.style.right = "auto"; }
    else { ref.style.right = chart.width - area.right + 2 + "px"; ref.style.left = "auto"; }
  }

  function buildMultiples() {
    const m = data.multiples, c1 = css("--chart-1"), surface = css("--color-surface"), grid = css("--chart-grid"), ink3 = css("--color-ink-3");
    m.charts.forEach((item, i) => {
      const canvas = doc.getElementById("sm-" + i);
      if (!canvas) return;
      const wrap = canvas.parentNode, ref = wrap.querySelector("[data-ref-label]");
      const last = item.values.length - 1;
      const datasets = [{ label: item.name, data: item.values, borderColor: c1, borderWidth: 2, tension: 0, fill: false, order: 1,
        pointRadius: (c) => (c.dataIndex === last ? 4 : 0), pointHoverRadius: 4, pointBackgroundColor: c1, pointBorderColor: surface, pointBorderWidth: 2 }];
      if (item.prev_avg !== null) datasets.push({ label: m.prev + " average", data: item.values.map(() => item.prev_avg), borderColor: ink3, borderWidth: 1,
        pointRadius: 0, pointHoverRadius: 0, pointHitRadius: 0, tension: 0, fill: false, order: 2 });
      charts.push(new Chart(canvas, {
        type: "line", data: { labels: m.labels.map(twoLine), datasets },
        options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, layout: { padding: { top: 8, right: 6 } },
          plugins: { legend: { display: false }, tooltip: tooltip(wrap, () => "line", (t) => (t.title || []).join(" ")) },
          scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: false } },
            y: { min: 0, max: m.ymax, grid: { color: grid, drawTicks: false }, border: { display: false },
              ticks: { callback: compact, maxTicksLimit: 4 }, afterFit: (s) => { s.width = 40; } } } },
        plugins: [{ id: "refLabel", afterLayout(c) { placeRef(c, item, ref); } }],
      }));
    });
  }

  function buildMatrix() {
    const canvas = doc.getElementById("chart-matrix");
    if (!canvas) return;
    const mx = data.matrix, surface = css("--color-surface"), wrap = doc.getElementById("chart-matrix-wrap");
    const shown = mx.series.filter((s) => s.values.some((v) => v > 0));
    const datasets = shown.map((s, i) => {
      const solid = css(s.token);
      return { label: s.name, data: s.values, plainColour: solid, backgroundColor: mx.partial.map((p) => (p ? hatch(solid) : solid)),
        borderColor: surface, borderWidth: { top: 2 }, borderSkipped: "bottom", maxBarThickness: 24,
        borderRadius: i === shown.length - 1 ? { topLeft: 4, topRight: 4 } : 0 };
    });
    charts.push(new Chart(canvas, {
      type: "bar", data: { labels: mx.labels.map(twoLine), datasets },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false },
          tooltip: tooltip(wrap, () => "rect", (t) => (t.title || []).flat().join(" ") + " · " + gbp(mx.totals[t.dataPoints[0].dataIndex])) },
        scales: { x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: false } },
          y: { stacked: true, beginAtZero: true, grid: { color: css("--chart-grid"), drawTicks: false }, border: { display: false }, ticks: { callback: compact, maxTicksLimit: 7 } } } },
    }));
    legend(doc.getElementById("legend-matrix"), shown.map((s) => ({ label: s.name, colour: css(s.token), shape: "rect" })));
  }

  function build() {
    charts.splice(0).forEach((c) => c.destroy());
    Chart.defaults.font.family = css("--font-ui");
    Chart.defaults.font.size = 11;
    Chart.defaults.color = css("--chart-ink");
    Chart.defaults.animation = reduced.matches ? false : Chart.defaults.animation;
    buildMultiples();
    buildMatrix();
  }
  build();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", build);
  new MutationObserver(build).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  reduced.addEventListener("change", build);
  // A chart built while its tab panel was hidden has no size: resize once a tab is shown.
  const resize = () => setTimeout(() => charts.forEach((c) => c.resize()), 0);
  doc.querySelectorAll("[role=tab]").forEach((t) => { t.addEventListener("click", resize); t.addEventListener("keydown", resize); });
  addEventListener("hashchange", resize);

  // Chart <-> table twin, as on Overview: the table replaces the chart and its legend.
  doc.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-table-toggle]");
    if (!toggle) return;
    const key = toggle.dataset.tableToggle, on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    toggle.querySelector("span").textContent = on ? "Chart" : "Table";
    const wrap = doc.getElementById(`chart-${key}-wrap`), twin = doc.getElementById(`twin-${key}`), leg = doc.getElementById(`legend-${key}`);
    if (wrap) wrap.hidden = on;
    if (leg) leg.hidden = on;
    if (twin) twin.hidden = !on;
    if (!on) resize();
  });
})();
