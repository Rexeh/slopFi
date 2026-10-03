// Overview charts: tokens read from CSS, re-themed live, external tooltip, DOM legend, hatched partial months,
// top-three tip labels, table twins. Chart.js 4 (vendored). Data comes from #dash-data; no colours live here.
(function () {
  "use strict";
  const doc = document;
  const data = JSON.parse(doc.getElementById("dash-data").textContent);
  const css = (p) => getComputedStyle(doc.documentElement).getPropertyValue(p).trim();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const gbp = (v) => (v < 0 ? "−" : "") + "£" + Math.round(Math.abs(v)).toLocaleString("en-GB");
  const compact = (v) => {
    const a = Math.abs(v), sign = v < 0 ? "−" : "";
    if (a >= 1000) return sign + "£" + (a / 1000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "") + "k";
    return sign + "£" + Math.round(a);
  };

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
  function tooltip(wrap, axis, keyShape) {
    let tip = wrap.querySelector(".chart-tip");
    if (!tip) { tip = doc.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; wrap.appendChild(tip); }
    return {
      enabled: false, mode: "index", intersect: false,
      external(ctx) {
        const model = ctx.tooltip;
        if (!model || model.opacity === 0) { tip.hidden = true; return; }
        const title = (model.title || []).flat().join(" · ");
        const rows = model.dataPoints.filter((p) => p.raw !== null && p.raw !== undefined).map((p) => {
          const shape = keyShape(p.dataset) || "rect";
          const colour = shape === "line" ? p.dataset.borderColor : (Array.isArray(p.dataset.backgroundColor) ? p.dataset.backgroundColor[p.dataIndex] : p.dataset.backgroundColor);
          const swatch = colour instanceof CanvasPattern ? p.dataset.plainColour[p.dataIndex] : colour;
          return `<div class="tip-row"><i class="tip-key tip-${shape}" style="--key:${swatch}"></i><b>${gbp(p.parsed[axis])}</b><span>${p.dataset.label || ""}</span></div>`;
        });
        tip.innerHTML = `<div class="tip-title">${title}</div>${rows.join("")}`;
        tip.hidden = false;
        const w = wrap.clientWidth, tw = tip.offsetWidth, x = model.caretX, y = model.caretY;
        tip.style.left = Math.min(Math.max(8, x - tw / 2), w - tw - 8) + "px";
        tip.style.top = Math.max(0, y - tip.offsetHeight - 12) + "px";
      },
    };
  }

  // DOM legend: rect keys for bars, a line key for the average.
  function legend(el, items) {
    if (!el) return;
    el.innerHTML = items.map((i) => `<span><i class="tip-key tip-${i.shape}" style="--key:${i.colour}"></i>${i.label}</span>`).join("");
  }

  // Direct labels on the three largest bars; the rest live in the table and tooltip.
  const tipLabels = {
    id: "tipLabels",
    afterDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(0), ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = css("--chart-ink-strong"); ctx.font = `500 11px ${css("--font-ui")}`; ctx.textBaseline = "middle";
      meta.data.slice(0, 3).forEach((bar, i) => ctx.fillText(gbp(chart.data.datasets[0].data[i]), bar.x + 6, bar.y));
      ctx.restore();
    },
  };

  const charts = [];
  function axes(moneyAxis) {
    const grid = css("--chart-grid");
    const money = { grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { callback: compact, maxTicksLimit: 7 } };
    const cat = { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, maxRotation: 0 } };
    return moneyAxis === "x" ? { x: { ...money, beginAtZero: true }, y: { ...cat, ticks: { ...cat.ticks, color: css("--chart-ink") } } }
                             : { x: cat, y: { ...money, beginAtZero: true } };
  }

  function build() {
    charts.splice(0).forEach((c) => c.destroy());
    Chart.defaults.font.family = css("--font-ui");
    Chart.defaults.font.size = 11;
    Chart.defaults.color = css("--chart-ink");
    Chart.defaults.animation = reduced.matches ? false : Chart.defaults.animation;
    const c1 = css("--chart-1"), c2 = css("--chart-2"), other = css("--chart-other"), ink2 = css("--color-ink-2");

    const whereWrap = doc.getElementById("chart-where-wrap");
    if (whereWrap && data.where.length) {
      whereWrap.style.height = data.where.length * 28 + 48 + "px";
      charts.push(new Chart(doc.getElementById("chart-where"), {
        type: "bar",
        data: { labels: data.where, datasets: [{ label: data.month, data: data.where_values,
          backgroundColor: data.where_uncat.map((u) => (u ? other : c1)), borderRadius: { topRight: 4, bottomRight: 4 },
          borderSkipped: "left", maxBarThickness: 20 }] },
        options: { indexAxis: "y", maintainAspectRatio: false, layout: { padding: { right: 56 } },
          plugins: { legend: { display: false }, tooltip: tooltip(whereWrap, "x", () => "rect") }, scales: axes("x") },
        plugins: [tipLabels],
      }));
    }

    const sixWrap = doc.getElementById("chart-six-wrap");
    if (sixWrap && data.months.length) {
      const labels = data.months.map((m, i) => (data.partial[i] ? [m, "partial"] : m));  // two lines, no rotation
      const fill = (colour) => data.partial.map((p) => (p ? hatch(colour) : colour));
      charts.push(new Chart(doc.getElementById("chart-six"), {
        type: "bar",
        data: { labels, datasets: [
          { label: "Spending", data: data.spending, backgroundColor: fill(c1), plainColour: data.partial.map(() => c1), borderRadius: 4, maxBarThickness: 24, order: 2 },
          { label: "Income", data: data.income, backgroundColor: fill(c2), plainColour: data.partial.map(() => c2), borderRadius: 4, maxBarThickness: 24, order: 2 },
          { type: "line", label: "3-month average spending", data: data.avg3, borderColor: ink2, borderWidth: 2,
            pointRadius: 0, pointHitRadius: 8, tension: 0, spanGaps: false, order: 1 },
        ] },
        options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          plugins: { legend: { display: false }, tooltip: tooltip(sixWrap, "y", (ds) => (ds.type === "line" ? "line" : "rect")) },
          scales: axes("y") },
      }));
      legend(doc.getElementById("legend-six"), [
        { label: "Spending", colour: c1, shape: "rect" }, { label: "Income", colour: c2, shape: "rect" },
        { label: "3-month average spending", colour: ink2, shape: "line" },
      ]);
    }
  }
  build();

  // Re-theme when the scheme flips (system setting or a data-theme override on <html>).
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", build);
  new MutationObserver(build).observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  reduced.addEventListener("change", build);

  // Chart <-> table twins, and the fund target reveal.
  doc.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-table-toggle]");
    if (toggle) {
      const key = toggle.dataset.tableToggle, on = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", String(on));
      toggle.lastChild.textContent = on ? "Chart" : "Table";
      const wrap = doc.getElementById(`chart-${key}-wrap`), twin = doc.getElementById(`twin-${key}`), leg = doc.getElementById(`legend-${key}`);
      if (wrap) wrap.hidden = on;
      if (leg) leg.hidden = on;
      if (twin) twin.hidden = !on;
      if (!on) charts.forEach((c) => c.resize());
    }
    const reveal = e.target.closest("[data-reveal]");
    if (reveal) {
      const form = doc.getElementById(reveal.dataset.reveal);
      if (form) { form.hidden = !form.hidden; if (!form.hidden) form.querySelector("input:not([type=hidden])").focus(); }
    }
  });
})();
