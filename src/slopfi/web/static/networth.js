// Net worth page: composition stack, history line, Update balances mode and the asset dialog.
(function () {
  "use strict";
  const doc = document, root = doc.documentElement;
  const node = doc.getElementById("networth-data");
  const data = node ? JSON.parse(node.textContent) : { parts: [], debts: [], history: [], holdings: [] };
  const css = (n) => getComputedStyle(root).getPropertyValue(n).trim();
  const gbp = (v) => (v < 0 ? "−" : "") + "£" + Math.round(Math.abs(v)).toLocaleString("en-GB");
  const tick = (v) => { const a = Math.abs(v); const s = a >= 1000 ? (a / 1000).toLocaleString("en-GB", { maximumFractionDigits: a >= 100000 ? 0 : 1 }) + "k" : String(a); return (v < 0 ? "−" : "") + "£" + s; };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let charts = [];

  // One external tooltip per chart, as on Overview: value first, then the label, with a rect or line key.
  function tooltip(wrap, axis, shape, titleOf) {
    let tip = wrap.querySelector(".chart-tip");
    if (!tip) { tip = doc.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; tip.setAttribute("aria-hidden", "true"); wrap.appendChild(tip); }
    return {
      enabled: false, mode: "index", intersect: false,
      external(ctx) {
        const model = ctx.tooltip;
        if (!model || model.opacity === 0) { tip.hidden = true; return; }
        const points = model.dataPoints.filter((p) => p.parsed[axis] !== 0 || model.dataPoints.length === 1);
        tip.innerHTML = `<div class="tip-title"></div>` + points.map((p) => {
          const colour = shape === "line" ? p.dataset.borderColor : p.dataset.backgroundColor;
          return `<div class="tip-row"><i class="tip-key tip-${shape}" style="--key:${colour}"></i><b>${gbp(p.parsed[axis])}</b><span>${p.dataset.label}</span></div>`;
        }).join("");
        tip.querySelector(".tip-title").textContent = titleOf(model);
        tip.hidden = false;
        const w = wrap.clientWidth, tw = tip.offsetWidth;
        tip.style.left = Math.min(Math.max(8, model.caretX - tw / 2), w - tw - 8) + "px";
        tip.style.top = Math.max(0, model.caretY - tip.offsetHeight - 12) + "px";
      },
    };
  }

  function buildComposition() {
    const canvas = doc.getElementById("chart-composition");
    if (!canvas || !(data.parts.length || data.debts.length)) return;
    const surface = css("--color-surface"), grid = css("--chart-grid");
    const gap = { borderColor: surface, borderWidth: { right: 2 }, borderSkipped: false, maxBarThickness: 24, stack: "s" };
    const datasets = data.parts.map((p, i) => ({ label: p.name, data: [p.value, 0], backgroundColor: css(p.token), ...gap,
      borderRadius: i === data.parts.length - 1 ? { topRight: 4, bottomRight: 4 } : 0 }))
      .concat(data.debts.map((d, i) => ({ label: d.name, data: [0, d.value], backgroundColor: css(d.token), ...gap,
        borderRadius: i === 0 ? { topLeft: 4, bottomLeft: 4 } : 0 })));
    // Round ticks: a 1/2/5 step that gives about six intervals across what we own and owe.
    const own = data.parts.reduce((a, p) => a + p.value, 0), owe = -data.debts.reduce((a, d) => a + d.value, 0);
    const raw = (own + owe) / 6 || 1, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw);
    charts.push(new Chart(canvas, {
      type: "bar", data: { labels: ["Own", "Owe"], datasets },
      options: { indexAxis: "y", maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: tooltip(canvas.parentNode, "x", "rect", (t) => t.title.join(" ") + " · " + gbp(t.dataPoints.reduce((a, p) => a + p.parsed.x, 0))) },
        scales: { x: { stacked: true, min: -Math.ceil(owe * 1.02 / step) * step, max: Math.ceil(own * 1.02 / step) * step, grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { callback: tick, stepSize: step } },
          y: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: css("--chart-ink-strong") } } } },
    }));
    const legend = doc.getElementById("legend-composition");
    if (legend) legend.innerHTML = data.parts.concat(data.debts).map((p) => `<span><i class="tip-key tip-rect" style="--key:${css(p.token)}"></i>${p.name}</span>`).join("");
  }

  function buildHistory() {
    const canvas = doc.getElementById("chart-history");
    if (!canvas || data.history.length < 2) return;
    const chart1 = css("--chart-1"), surface = css("--color-surface");
    charts.push(new Chart(canvas, {
      type: "line", data: { labels: data.history.map((p) => p.label), datasets: [{ label: "Net worth", data: data.history.map((p) => p.value), borderColor: chart1, borderWidth: 2, tension: 0, fill: false,
        pointRadius: 3, pointHoverRadius: 4, pointBackgroundColor: chart1, pointBorderColor: surface, pointBorderWidth: 2 }] },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, layout: { padding: { top: 8, right: 8 } },
        plugins: { legend: { display: false }, tooltip: tooltip(canvas.parentNode, "y", "line", (t) => t.title.join(" ")) },
        scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 6 } },
          y: { grid: { color: css("--chart-grid"), drawTicks: false }, border: { display: false }, ticks: { callback: tick, maxTicksLimit: 5 } } } },
    }));
  }

  function build() {
    if (typeof Chart === "undefined") return;
    charts.forEach((c) => c.destroy());
    charts = [];
    Chart.defaults.font.family = css("--font-ui"); Chart.defaults.font.size = 11; Chart.defaults.color = css("--chart-ink");
    if (reduced.matches) Chart.defaults.animation = false;
    buildComposition();
    buildHistory();
  }
  build();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", build);
  new MutationObserver(build).observe(root, { attributes: true, attributeFilter: ["data-theme"] });

  // Chart <-> table twin, as on Overview: the table replaces the chart and its legend.
  doc.querySelectorAll("[data-table-toggle]").forEach((toggle) => toggle.addEventListener("click", () => {
    const key = toggle.dataset.tableToggle, on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    toggle.querySelector("span").textContent = on ? "Chart" : "Table";
    const wrap = doc.getElementById(`chart-${key}-wrap`), twin = doc.getElementById(`twin-${key}`), leg = doc.getElementById(`legend-${key}`);
    if (wrap) wrap.hidden = on;
    if (leg) leg.hidden = on;
    if (twin) twin.hidden = !on;
    if (!on) charts.forEach((c) => c.resize());
  }));

  // ------------------------------------------------------ Update balances
  const form = doc.getElementById("balances-form"), toggle = doc.querySelector("[data-update-toggle]");
  function setUpdating(on) {
    if (!form) return;
    form.classList.toggle("is-updating", on);
    form.querySelectorAll("tr[data-editable='1']").forEach((row) => {
      row.classList.toggle("is-editing", on);
      row.querySelectorAll(".shown").forEach((el) => { el.hidden = on; });
      row.querySelectorAll(".editing").forEach((el) => { el.hidden = !on; });
    });
    const actions = form.querySelector(".balances-actions");
    if (actions) actions.hidden = !on;
    if (toggle) toggle.setAttribute("aria-pressed", String(on));
    if (on) { const first = form.querySelector("tr.is-editing input[inputmode]"); if (first) first.focus(); }
  }
  if (toggle) toggle.addEventListener("click", (e) => { e.preventDefault(); setUpdating(toggle.getAttribute("aria-pressed") !== "true"); });
  const cancel = doc.querySelector("[data-update-cancel]");
  if (cancel) cancel.addEventListener("click", (e) => { e.preventDefault(); setUpdating(false); if (toggle) toggle.focus(); });

  // ------------------------------------------------------------ asset dialog
  const dialog = doc.getElementById("holding-dialog");
  if (!dialog) return;
  const $ = (id) => doc.getElementById(id);
  const byId = {};
  data.holdings.forEach((h) => { byId[h.id] = h; });
  const LOAN_KINDS = ["property", "loan", "cash", "cash_isa"];
  function syncKind() {
    const kind = $("h-kind").value;
    $("h-mortgage-fields").hidden = kind !== "property";
    $("h-loan-fields").hidden = !LOAN_KINDS.includes(kind);
    $("h-loan-legend").textContent = kind === "property" ? "Mortgage terms" : kind === "loan" ? "Loan terms" : "Interest";
  }
  $("h-kind").addEventListener("change", syncKind);
  const val = (v) => (v === null || v === undefined ? "" : String(v));
  const money2 = (v) => (v === null || v === undefined || v === "" ? "" : Number(v).toFixed(2));
  function fill(h) {
    const loan = h && h.kind === "property" ? (h.mortgage || {}) : (h || {});
    $("holding-title").textContent = h ? "Edit " + h.name : "Add an asset or liability";
    $("h-id").value = h ? h.id : "";
    $("h-name").value = h ? h.name : "";
    $("h-kind").value = h ? h.kind : "property";
    $("h-owner").value = h ? h.owner : "joint";
    $("h-provider").value = h ? val(h.provider) : "";
    $("h-value").value = h ? money2(h.value) : "";
    $("h-valued").value = h ? h.valued_at : $("h-valued").defaultValue;
    $("h-mortgage").value = h && h.mortgage ? money2(h.mortgage.value) : "";
    $("h-lender").value = h && h.mortgage ? val(h.mortgage.provider) : "";
    $("h-rate").value = val(loan.rate);
    $("h-payment").value = money2(loan.monthly_payment);
    $("h-fix").value = val(loan.fix_end);
    $("h-term").value = val(loan.term_end);
    $("h-notes").value = h ? val(h.notes) : "";
    $("holding-submit").textContent = h ? "Save changes" : "Add asset";
    dialog.querySelectorAll("[aria-invalid]").forEach((el) => { el.removeAttribute("aria-invalid"); el.removeAttribute("aria-describedby"); });
    dialog.querySelectorAll(".field-error").forEach((el) => el.remove());
    const del = $("holding-delete");
    del.hidden = !h;
    if (h) {
      del.action = "/holdings/" + h.id + "/delete";
      del.dataset.confirm = "Delete " + h.name + "?";
      del.dataset.confirmDetail = h.n_snapshots + " valuation" + (h.n_snapshots === 1 ? "" : "s") + " recorded" + (h.mortgage ? "; its mortgage goes with it" : "") + ". This can't be undone.";
    }
    syncKind();
  }
  let opener = null;
  function open(h, from) {
    opener = from || doc.activeElement;
    fill(h);
    if (dialog.open) dialog.close();
    if (dialog.showModal) dialog.showModal(); else dialog.open = true;
    $("h-name").focus();
  }
  doc.querySelectorAll("[data-open-holding]").forEach((b) => b.addEventListener("click", () => open(null, b)));
  doc.querySelectorAll("[data-edit-holding]").forEach((a) => a.addEventListener("click", (e) => {
    const h = byId[a.dataset.editHolding];
    if (!h) return;                 // unknown id: let the link fall back to ?edit=
    e.preventDefault();
    open(h, a);
  }));
  dialog.querySelectorAll("[data-dialog-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("close", () => { if (opener && opener.focus) opener.focus(); if (location.search.includes("edit=")) history.replaceState(null, "", "/networth"); });
  // Server-rendered open (deep link or a field error): make it modal without losing the prefilled fields.
  if (dialog.hasAttribute("open") && dialog.showModal) { dialog.removeAttribute("open"); dialog.showModal(); const bad = dialog.querySelector("[aria-invalid]"); (bad || $("h-name")).focus(); }
})();
