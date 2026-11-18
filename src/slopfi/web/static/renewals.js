// Renewals: the live saving line under the renew form, and Switched filling in the other provider.
(() => {
  const form = document.getElementById("renew-form");
  const out = document.getElementById("renew-result");
  if (!form || !out) return;
  const num = (name) => {
    const v = (form.elements[name]?.value || "").replace(/[£,\s]/g, "");
    return v === "" || isNaN(+v) ? null : +v;
  };
  const annual = (price, period) => (price === null ? null : period === "month" ? price * 12 : price);   // as renewals.annual
  const gbp = (n) => "£" + Math.round(Math.abs(n)).toLocaleString("en-GB");
  const update = () => {
    const chosen = annual(num("new_price"), form.elements.new_period.value);
    const quote = annual(num("quote"), form.elements.quote_period.value);
    const current = annual(form.dataset.current === "" ? null : +form.dataset.current, form.dataset.currentPeriod);
    if (chosen === null) { out.textContent = ""; return; }
    const parts = [];
    if (quote !== null) {
      const d = quote - chosen;
      parts.push(d >= 0 ? `<b>${gbp(d)} a year less</b> than the renewal quote` : `<b class="is-worse">${gbp(d)} a year more</b> than the renewal quote`);
    }
    if (current !== null) {
      const d = current - chosen;
      parts.push(d === 0 ? "the same as now" : d > 0 ? `<b>${gbp(d)} a year less</b> than now` : `<b class="is-worse">${gbp(d)} a year more</b> than now`);
    }
    out.innerHTML = parts.length ? parts.join(", and ") + "." : "";
  };
  form.addEventListener("input", update);
  form.addEventListener("change", (e) => {
    if (e.target.name === "outcome" && e.target.value === "switched") {
      const other = form.elements.other_provider.value.trim();
      const provider = form.elements.new_provider;
      if (other && (provider.value === provider.dataset.current || provider.value === "")) provider.value = other;
      const otherPrice = form.elements.other_quote.value.trim();
      const price = form.elements.new_price;   // replaced while it still holds the current price
      if (otherPrice && (!price.value || +price.value === +form.dataset.current)) {
        form.elements.new_price.value = otherPrice;
        form.elements.new_period.value = form.elements.other_period.value;
      }
    }
    update();
  });
  update();
})();
