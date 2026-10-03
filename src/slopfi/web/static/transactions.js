// Transactions: keyboard filing (j/k/c/o/Enter/n/x), the cloned category editor, "Make a rule", One-off, selection, sorting.
// Rows carry no per-row handlers (a 577-row month must stay small): everything is delegated from the document,
// so a swapped results region or row needs no rebinding. Cells are found by column position.
(function () {
  "use strict";
  const doc = document;
  const $ = (sel, from) => (from || doc).querySelector(sel);
  const $$ = (sel, from) => Array.from((from || doc).querySelectorAll(sel));
  const rows = () => $$("#txn-table tbody tr");
  const SEL = "#txn-table tbody td:first-child input", ONE = "td:nth-child(8) input";
  const typing = (el) => Boolean(el && el.matches && el.matches("input, select, textarea, [contenteditable]"));
  const NUMERIC = /^[\d\-*#@:.]+$/;
  const CAT = "td:nth-child(6)", DESC = "td:nth-child(4)";
  const ONE_OFF_DELAY = 400;  // ms: a double toggle sends one request with the final state

  // Mirror of categorise.suggest_pattern: the first useful line with pure-number tokens removed, upper-cased.
  function suggest(description) {
    const lines = description.split(" / ").map((l) => l.trim()).filter(Boolean);
    for (const line of lines) {
      const upper = line.toUpperCase();
      if (upper.startsWith("INT'L") || line.startsWith("EUR ") || upper.startsWith("VISA RATE")) continue;
      const tokens = line.split(/\s+/).filter((t) => !NUMERIC.test(t));
      if (tokens.length) return tokens.join(" ").toUpperCase();
    }
    return lines.length ? lines[0].toUpperCase().replace(/\s+/g, " ") : "";
  }

  // ------------------------------------------------------------ current row
  let currentId = null;
  function current() {
    const active = doc.activeElement && doc.activeElement.closest && doc.activeElement.closest("#txn-table tbody tr");
    return active || (currentId && doc.getElementById(currentId)) || rows()[0] || null;
  }
  function focusRow(tr) {
    if (!tr) return;
    rows().forEach((r) => r.classList.toggle("is-current", r === tr));
    currentId = tr.id;
    tr.tabIndex = -1;
    tr.focus({ preventScroll: true });
    tr.scrollIntoView({ block: "nearest" });
  }
  function move(delta) {
    const list = rows(), cur = current();
    if (!list.length) return;
    const i = Math.max(0, Math.min(list.length - 1, (cur ? list.indexOf(cur) : -1) + delta));
    focusRow(list[i]);
  }

  // ---------------------------------------------------------- the editor
  function openEditor(tr) {
    if (!tr) return;
    const cell = $(CAT, tr);
    const open = $("input[list]", cell);
    if (open) { open.focus(); open.select(); return; }
    const tpl = doc.getElementById("cat-editor");
    if (!tpl) return;
    const id = tr.id.slice(4), label = ($("button.cat", cell) || { textContent: "" }).textContent.trim();
    cell.dataset.prev = cell.innerHTML;
    cell.innerHTML = tpl.innerHTML.replaceAll("txn-0-", `txn-${id}-`).replace("/transactions/0/", `/transactions/${id}/`);
    const input = $("input[list]", cell);
    input.value = label;
    const cancel = doc.createElement("button");
    cancel.type = "button"; cancel.className = "btn-quiet btn-sm"; cancel.dataset.cancel = ""; cancel.textContent = "Cancel";
    $("button", cell).after(cancel);
    tr.classList.add("is-editing");
    if (window.htmx) htmx.process(cell);
    input.focus(); input.select();
  }
  function closeEditor(tr) {
    const cell = tr && $(CAT, tr);
    if (!cell || cell.dataset.prev === undefined) { if (tr) focusRow(tr); return; }
    cell.innerHTML = cell.dataset.prev;
    delete cell.dataset.prev;
    tr.classList.remove("is-editing");
    ($("button.cat", cell) || tr).focus();
  }
  // "Make a rule" adds the pattern field beneath the chooser, pre-filled from the description.
  function ruleField(box) {
    const form = box.closest("form"), existing = $(".rule-field", form);
    if (!box.checked) { if (existing) existing.remove(); return; }
    if (existing) return;
    const tpl = doc.getElementById("rule-field");
    form.appendChild(tpl.content.cloneNode(true));
    const input = $(".rule-field input", form), tr = form.closest("tr");
    input.id = `${tr.id}-pattern`;
    input.value = suggest((($(DESC, tr).childNodes[0]) || { textContent: "" }).textContent.trim());
    input.focus();
  }
  // One-off posts after a 400ms pause (hx-trigger "change delay:400ms" without the per-row attributes).
  const pending = new Map();
  function oneOff(box) {
    const tr = box.closest("tr");
    clearTimeout(pending.get(tr.id));
    pending.set(tr.id, setTimeout(() => {
      pending.delete(tr.id);
      const row = doc.getElementById(tr.id), live = row && $(ONE, row);
      if (!live || !window.htmx) return;
      htmx.ajax("POST", `/transactions/${tr.id.slice(4)}/one_off`,
        { source: live, target: row, swap: "outerHTML", values: { one_off: live.checked ? "1" : "" } });
    }, ONE_OFF_DELAY));
  }
  function toggleOneOff(tr) {
    const box = tr && $(ONE, tr);
    if (!box) return;
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function nextUncategorised() {
    const list = rows(), cur = current();
    const from = cur ? list.indexOf(cur) + 1 : 0;
    const next = list.slice(from).find((r) => r.classList.contains("uncat")) || list.find((r) => r.classList.contains("uncat"));
    if (next) { focusRow(next); openEditor(next); }
    else if (window.FF) FF.toast("Nothing left to categorise here.", "info");
  }

  // ------------------------------------------------------------ selection
  function syncSelection() {
    const all = $$(SEL), n = all.filter((b) => b.checked).length;
    all.forEach((b) => b.closest("tr").classList.toggle("is-selected", b.checked));
    const bulk = doc.getElementById("bulk"), head = doc.getElementById("sel-all");
    if (bulk) { bulk.hidden = n === 0; $$("[data-sel-count]", bulk).forEach((el) => { el.textContent = n; }); }
    if (head) { head.checked = n > 0 && n === all.length; head.indeterminate = n > 0 && n < all.length; }
  }

  // --------------------------------------------------------------- events
  doc.addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "sel-all") { $$(SEL).forEach((b) => { b.checked = t.checked; }); syncSelection(); }
    else if (t.matches(SEL)) syncSelection();
    else if (t.matches("[name=create_rule]")) ruleField(t);
    else if (t.matches(`#txn-table tbody ${ONE}`)) oneOff(t);
  });
  // A header's sort goes through the filter form, so the form keeps it for the next filter change and the URL updates.
  let sortFocus = null;
  function sortBy(link) {
    const form = doc.getElementById("txn-filters");
    if (!form || !window.htmx) return false;
    form.elements.sort.value = link.dataset.sort;
    form.elements.dir.value = link.dataset.dir;
    sortFocus = link.dataset.col;
    htmx.trigger(form, "submit");
    return true;
  }
  doc.addEventListener("click", (e) => {
    const sort = e.target.closest("#txn-table thead a[data-col]");
    if (sort) { if (sortBy(sort)) e.preventDefault(); return; }
    const edit = e.target.closest("button.cat");
    if (edit) { openEditor(edit.closest("tr")); return; }
    const cancel = e.target.closest("[data-cancel]");
    if (cancel) { closeEditor(cancel.closest("tr")); return; }
    if (e.target.closest("[data-clear-selection]")) { $$(SEL).forEach((b) => { b.checked = false; }); syncSelection(); return; }
    const tr = e.target.closest("#txn-table tbody tr");
    if (tr && !e.target.closest("a, button, input, label, form")) focusRow(tr);
  });
  doc.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (typing(t)) {
      if (e.key === "Escape" && t.closest(CAT)) { e.preventDefault(); closeEditor(t.closest("tr")); }
      return;
    }
    if (t.closest && t.closest("button, a, summary, [role=tab]") && (e.key === "Enter" || e.key === " ")) return;
    switch (e.key) {
      case "j": e.preventDefault(); move(1); break;
      case "k": e.preventDefault(); move(-1); break;
      case "c": case "Enter": e.preventDefault(); openEditor(current()); break;
      case "o": e.preventDefault(); toggleOneOff(current()); break;
      case "n": e.preventDefault(); nextUncategorised(); break;
      case "x": { e.preventDefault(); const box = current() && $("td:first-child input", current()); if (box) { box.checked = !box.checked; syncSelection(); } break; }
      case "?": { e.preventDefault(); const keys = doc.getElementById("keys"); if (keys) keys.open = !keys.open; break; }
      case "Escape": { const keys = doc.getElementById("keys"); if (keys && keys.open) keys.open = false; break; }
      default: break;
    }
  });
  // Bulk actions post the selected rows' ids (rows carry no name/value pairs of their own).
  doc.body.addEventListener("htmx:configRequest", (e) => {
    if (!e.detail.elt.closest("[data-bulk]")) return;
    e.detail.formData.delete("ids");
    $$(SEL).filter((b) => b.checked).forEach((b) => e.detail.formData.append("ids", b.closest("tr").id.slice(4)));
  });
  // After a swap the row ids are the same; keep the current marker and the toolbar's count in step.
  doc.body.addEventListener("htmx:afterSettle", () => {
    syncSelection();
    const head = sortFocus && doc.querySelector(`#txn-table thead a[data-col="${sortFocus}"]`);
    sortFocus = null;
    if (head) head.focus();
    const again = currentId && doc.getElementById(currentId);
    if (again) {
      again.classList.add("is-current");
      // A swapped row loses its tabindex; if focus fell to the body, put it back on the row by id.
      if (doc.activeElement === doc.body || !doc.activeElement) focusRow(again);
    }
  });
  // A rule save sends HX-Refresh, and Firefox restores checkbox ticks by position on reload, landing them on whichever
  // rows now sit there. Rows are too many to carry autocomplete="off", so put back what the server rendered.
  $$("#txn-table tbody input[type=checkbox]").forEach((b) => { b.checked = b.defaultChecked; });
  syncSelection();
})();
