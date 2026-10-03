// slopFi shell: sidebar rail, toasts, htmx busy states and focus restore, confirm dialog, tabs.
// No dependencies; htmx events are listened for by name so the file also works on pages without htmx.
(function () {
  "use strict";
  const doc = document, root = doc.documentElement;
  const $ = (sel, from) => (from || doc).querySelector(sel);
  const svg = (name) => `<svg class="icon icon-sm" viewBox="0 0 20 20" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");

  // ---------------------------------------------------------------- sidebar
  const toggle = $("[data-toggle-sidebar]");
  function setRail(on) {
    if (on) root.dataset.sidebar = "rail"; else delete root.dataset.sidebar;
    try { localStorage.setItem("sidebar", on ? "rail" : "full"); } catch (e) { /* private mode */ }
    if (!toggle) return;
    toggle.setAttribute("aria-expanded", String(!on));
    const title = $("title", toggle);
    if (title) title.textContent = on ? "Expand sidebar" : "Collapse sidebar";
  }
  if (toggle) {
    toggle.addEventListener("click", () => setRail(root.dataset.sidebar !== "rail"));
    if (root.dataset.sidebar === "rail") setRail(true);
  }
  // The Setup group is a plain list in the sidebar and a dropdown in the phone top bar.
  const setup = $("#nav-setup"), phone = matchMedia("(max-width: 768px)");
  function syncSetup() { if (setup) setup.open = !phone.matches; }
  syncSetup();
  phone.addEventListener("change", syncSetup);
  doc.addEventListener("click", (e) => { if (phone.matches && setup && setup.open && !setup.contains(e.target)) setup.open = false; });

  // ------------------------------------------------------------------ toasts
  const region = $("#toast");
  const ICON = { success: "check", error: "alert", info: "info", warning: "alert" };
  function toast(message, kind, action) {
    if (!region) return;
    kind = ICON[kind] ? kind : "info";
    const el = doc.createElement("div");
    el.className = `toast toast-${kind}`;
    el.innerHTML = `${svg(ICON[kind])}<span class="toast-text"></span>`;
    $(".toast-text", el).textContent = message;
    if (action && action.label && action.href) {
      const a = doc.createElement("a"); a.className = "btn-quiet"; a.href = action.href; a.textContent = action.label; el.appendChild(a);
    }
    const close = doc.createElement("button");
    close.type = "button"; close.className = "btn-quiet toast-close"; close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = svg("close");
    el.appendChild(close);
    const remove = () => { el.classList.add("is-leaving"); setTimeout(() => el.remove(), reduced.matches ? 0 : 150); };
    close.addEventListener("click", remove);
    region.appendChild(el);
    if (kind !== "error") setTimeout(remove, 5000);
    return el;
  }
  // A redirect queues its toast in a cookie set by flash() on the server; consume it once.
  const flash = doc.cookie.split("; ").find((c) => c.startsWith("flash="));
  if (flash) {
    try { const f = JSON.parse(decodeURIComponent(flash.slice(6))); toast(f.message, f.kind, f.action); } catch (e) { /* ignore */ }
    doc.cookie = "flash=; Max-Age=0; Path=/; SameSite=Lax";
  }
  doc.body.addEventListener("toast", (e) => { const d = e.detail || {}; toast(d.message, d.kind, d.action); });
  doc.body.addEventListener("htmx:responseError", (e) => {
    const status = e.detail.xhr && e.detail.xhr.status;
    toast(status === 400 || status === 422 ? "That couldn't be saved. Check the field and try again." : "Something went wrong. Try again.", "error");
  });
  doc.body.addEventListener("htmx:sendError", () => toast("Couldn't reach the app. Is it still running?", "error"));

  // ------------------------------------------------------- htmx busy states
  // After 150ms the target dims and ignores clicks; after 600ms a 2px indeterminate bar tops it. Never a skeleton.
  // FF.busy(el) returns the function that clears it, for requests made with fetch (the Projection recalculation).
  function busyOn(target) {
    const t = { dim: setTimeout(() => target.classList.add("hx-busy"), 150),
                bar: setTimeout(() => target.classList.add("hx-busy-long"), 600) };
    target.setAttribute("aria-busy", "true");
    return () => {
      clearTimeout(t.dim); clearTimeout(t.bar);
      target.classList.remove("hx-busy", "hx-busy-long");
      target.removeAttribute("aria-busy");
    };
  }
  const busy = new WeakMap();
  doc.body.addEventListener("htmx:beforeRequest", (e) => {
    const target = e.detail.target;
    if (!target || target === doc.body) return;
    const prior = busy.get(target);
    if (prior) prior();
    busy.set(target, busyOn(target));
    const btn = e.detail.elt && e.detail.elt.closest("button, .btn");
    if (btn) btn.classList.add("is-loading");
  });
  function settle(target, elt) {
    const done = target && busy.get(target);
    if (done) { done(); busy.delete(target); }
    else if (target && target.classList) { target.classList.remove("hx-busy", "hx-busy-long"); target.removeAttribute("aria-busy"); }
    const btn = elt && elt.closest && elt.closest("button, .btn");
    if (btn) btn.classList.remove("is-loading");
  }
  doc.body.addEventListener("htmx:afterRequest", (e) => settle(e.detail.target, e.detail.elt));
  doc.body.addEventListener("htmx:sendError", (e) => settle(e.detail.target, e.detail.elt));

  // ------------------------------------------------- focus restore on swaps
  // A row swap replaces the control that had focus; the fragment keeps the same ids, so find it again.
  let focusId = null;
  doc.body.addEventListener("htmx:beforeSwap", (e) => {
    const active = doc.activeElement;
    focusId = active && active.id && e.detail.target && e.detail.target.contains(active) ? active.id : null;
  });
  doc.body.addEventListener("htmx:afterSettle", (e) => {
    const swapped = e.detail.elt;
    if (swapped && swapped.matches && swapped.matches("tr")) {
      swapped.classList.add("row-flash");
      setTimeout(() => swapped.classList.remove("row-flash"), 700);
    } else if (swapped && swapped.id === "txn-results") {
      swapped.classList.add("swap-in");                       // a new result set fades in; rows flash instead
      swapped.addEventListener("animationend", () => swapped.classList.remove("swap-in"), { once: true });
    }
    scrollRegions(swapped);
    if (focusId) {
      const again = doc.getElementById(focusId);
      if (again) again.focus({ preventScroll: true });
      focusId = null;
    }
  });

  // --------------------------------------------------------- confirm dialog
  const dialog = $("#confirm");
  function confirm(title, detail, label, kind) {
    if (!dialog || !dialog.showModal) return Promise.resolve(window.confirm(`${title}\n${detail || ""}`));
    $("#confirm-title").textContent = title;
    $("#confirm-detail").textContent = detail || "";
    const action = $("[value=confirm]", dialog);
    action.textContent = label || "Delete";
    // data-confirm-kind="primary" for a confirmation that destroys nothing (an import); danger otherwise
    action.classList.toggle("btn-danger", kind !== "primary");
    action.classList.toggle("btn-primary", kind === "primary");
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
      dialog.returnValue = "cancel";
      dialog.showModal();
      $("[value=cancel]", dialog).focus();
    });
  }
  // <form data-confirm="Delete Vanguard ISA?" data-confirm-detail="…" data-confirm-label="Delete">
  doc.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.dataset || !form.dataset.confirm || form.dataset.confirmed) return;
    e.preventDefault();
    confirm(form.dataset.confirm, form.dataset.confirmDetail, form.dataset.confirmLabel, form.dataset.confirmKind).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = "1";
      form.requestSubmit ? form.requestSubmit() : form.submit();
      delete form.dataset.confirmed;
    });
  }, true);
  doc.body.addEventListener("htmx:confirm", (e) => {
    const el = e.detail.elt && e.detail.elt.closest("[data-confirm]");
    if (!el || el.tagName === "FORM") return;
    e.preventDefault();
    confirm(el.dataset.confirm, el.dataset.confirmDetail, el.dataset.confirmLabel, el.dataset.confirmKind).then((ok) => ok && e.detail.issueRequest(true));
  });

  // -------------------------------------------------------------------- tabs
  // role="tablist" of buttons; panels are server-rendered with `hidden`; the active tab lives in the URL hash.
  doc.querySelectorAll("[role=tablist]").forEach((list) => {
    const tabs = Array.from(list.querySelectorAll("[role=tab]"));
    const panelOf = (tab) => doc.getElementById(tab.getAttribute("aria-controls"));
    function select(tab, opts) {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const p = panelOf(t);
        if (p) p.hidden = !on;
      });
      if (opts && opts.focus) tab.focus();
      if (opts && opts.hash !== false) {
        const slug = tab.getAttribute("aria-controls").replace(/^panel-/, "");
        history.replaceState(null, "", "#" + slug);
      }
    }
    function fromHash() {
      const h = location.hash.slice(1);
      if (!h) return false;
      const direct = tabs.find((t) => t.getAttribute("aria-controls") === "panel-" + h || t.getAttribute("aria-controls") === h);
      const target = direct || tabs.find((t) => { const p = panelOf(t); return p && doc.getElementById(h) && p.contains(doc.getElementById(h)); });
      if (target) select(target, { hash: false });
      return Boolean(target);
    }
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        select(tabs[(next + tabs.length) % tabs.length], { focus: true });
      });
    });
    if (!fromHash()) select(tabs.find((t) => t.getAttribute("aria-selected") === "true") || tabs[0], { hash: false });
    addEventListener("hashchange", fromHash);
  });

  // ------------------------------------------------------- scroll regions
  // A table wrapper that scrolls sideways must be reachable by keyboard (axe scrollable-region-focusable):
  // it is a named, focusable region only while it actually overflows.
  function scrollRegions(scope) {
    ((scope && scope.querySelectorAll) ? scope : doc).querySelectorAll(".table-scroll, .table-wrap").forEach((el) => {
      const overflows = el.scrollWidth > el.clientWidth + 1, heading = el.closest(".panel, .card, section")?.querySelector("h2, h3");
      if (overflows && !el.hasAttribute("tabindex")) {
        Object.assign(el, { tabIndex: 0 }); el.setAttribute("role", "region"); el.dataset.scrollRegion = "1";
        el.setAttribute("aria-label", `${heading ? heading.textContent.trim() : "Table"} (scrolls sideways)`);
      } else if (!overflows && el.dataset.scrollRegion) {
        ["tabindex", "role", "aria-label", "data-scroll-region"].forEach((a) => el.removeAttribute(a));
      }
    });
  }
  scrollRegions(doc);
  let resizeTimer;
  addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => scrollRegions(doc), 150); });
  doc.addEventListener("click", (e) => { if (e.target.closest("[aria-controls]")) requestAnimationFrame(() => scrollRegions(doc)); });

  // ----------------------------------------------------------- chart motion
  // Chart.js keeps its own clock: give it the token duration and enter curve, and none under reduced motion. This runs
  // before the deferred page scripts build their charts; each chart well's skeleton lifts once they have.
  const token = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const bezier = (x1, y1, x2, y2) => (x) => {
    const at = (a, b, t) => ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t;
    let lo = 0, hi = 1, t = x;
    for (let i = 0; i < 20 && x > 0 && x < 1; i++) { t = (lo + hi) / 2; if (at(x1, x2, t) < x) lo = t; else hi = t; }
    return x <= 0 || x >= 1 ? x : at(y1, y2, t);
  };
  function chartMotion() {
    const C = window.Chart, fx = C && C.helpers && C.helpers.easingEffects;
    if (!C || !C.defaults) return;
    if (fx) fx.ffEnter = bezier(...(token("--ease-enter").match(/[\d.]+/g) || [0.2, 0, 0, 1]).map(Number));
    chartMotion.anim = chartMotion.anim || C.defaults.animation || {};
    C.defaults.animation = reduced.matches ? false : Object.assign(chartMotion.anim,
      { duration: parseFloat(token("--duration-base")) || 180, easing: fx ? "ffEnter" : "easeOutCubic" });
    const active = C.defaults.transitions && C.defaults.transitions.active;
    if (active && active.animation) active.animation.duration = reduced.matches ? 0 : parseFloat(token("--duration-fast")) || 120;
  }
  chartMotion();
  reduced.addEventListener("change", chartMotion);
  doc.addEventListener("DOMContentLoaded", () => root.classList.add("charts-ready"));

  // Page-level scripts (charts) can reuse these.
  window.FF = { toast, confirm, setRail, busy: busyOn, scrollRegions, chartMotion };
})();
