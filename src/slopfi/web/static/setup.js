// Setup pages: inline row editing, form dialogs, reveal-on-select, and the prune confirmation.
// Toasts, confirmations and tabs come from app.js; this file only wires page controls to them.
(function () {
  "use strict";
  const doc = document;
  const $ = (sel, from) => (from || doc).querySelector(sel);

  // ---- inline edit rows: <button data-edit="row-id"> shows that row and hides its own; data-cancel reverses it.
  function openEdit(button) {
    const edit = doc.getElementById(button.dataset.edit), display = button.closest("tr");
    if (!edit || !display) return;
    display.hidden = true;
    edit.hidden = false;
    const first = $("input, select", edit);
    if (first) { first.focus(); if (first.select) first.select(); }
  }
  function closeEdit(edit, displayId) {
    const display = doc.getElementById(displayId);
    if (!edit || !display) return;
    edit.hidden = true;
    display.hidden = false;
    const again = $("[data-edit]", display);
    if (again) {                      // the row action is hidden until the row is hovered or holds focus: reveal, focus, let focus-within keep it
      display.classList.add("is-editing");
      again.focus();
      display.classList.remove("is-editing");
    }
  }
  doc.addEventListener("click", (e) => {
    const edit = e.target.closest("[data-edit]");
    if (edit) { openEdit(edit); return; }
    const cancel = e.target.closest("[data-cancel]");
    if (cancel) { closeEdit(cancel.closest("tr"), cancel.dataset.cancel); return; }
    const opener = e.target.closest("[data-dialog]");
    if (opener) { const d = $(opener.dataset.dialog); if (d && d.showModal) { d.showModal(); const f = $("input, select", d); if (f) f.focus(); } return; }
    const closer = e.target.closest("[data-close]");
    if (closer) { const d = closer.closest("dialog"); if (d) d.close(); }
  });
  doc.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const row = e.target.closest && e.target.closest("tr.edit-row");
    const cancel = row && $("[data-cancel]", row);
    if (cancel) { e.preventDefault(); closeEdit(row, cancel.dataset.cancel); }
  });

  // ---- a dialog rendered with data-open (a form that came back with field errors) opens at once
  doc.querySelectorAll("dialog[data-open]").forEach((d) => {
    if (!d.showModal) return;
    d.showModal();
    const bad = $("[aria-invalid=true]", d);
    if (bad) bad.focus();
  });

  // ---- <select data-reveal="#id" data-reveal-value="new"> shows the target only for that value
  doc.querySelectorAll("select[data-reveal]").forEach((sel) => {
    const target = $(sel.dataset.reveal);
    if (!target) return;
    const sync = () => { target.hidden = sel.value !== sel.dataset.revealValue; };
    sel.addEventListener("change", sync);
    sync();
  });

  // ---- rules: Export downloads the file and says so; Import confirms, and says more when Replace is ticked
  const exportLink = $("[data-export-rules]");
  if (exportLink && window.FF) {
    exportLink.addEventListener("click", () => {
      const n = Number(exportLink.dataset.count || 0);
      window.FF.toast(`${n} rule${n === 1 ? "" : "s"} exported to slopfi-rules.json`, "success");
    });
  }
  const importForm = $("#import-rules-form"), replace = $("#import-replace");
  if (importForm && replace) {
    const keep = importForm.dataset.confirmDetail;
    const sync = () => {
      importForm.dataset.confirmDetail = replace.checked ? importForm.dataset.replaceDetail : keep;
      importForm.dataset.confirmLabel = replace.checked ? "Replace rules" : "Import rules";
      importForm.dataset.confirmKind = replace.checked ? "danger" : "primary";
    };
    replace.addEventListener("change", sync);
    sync();
  }

  // ---- the sync form confirms only when "Remove statements whose files are gone" is ticked and would remove something
  const sync = $("#sync-form"), prune = $("#prune");
  if (sync && prune) {
    const update = () => {
      if (prune.checked && sync.dataset.pruneTitle) {
        sync.dataset.confirm = sync.dataset.pruneTitle;
        sync.dataset.confirmDetail = sync.dataset.pruneDetail || "";
      } else {
        delete sync.dataset.confirm;
        delete sync.dataset.confirmDetail;
      }
    };
    prune.addEventListener("change", update);
    update();
  }
})();
