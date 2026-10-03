"""Projection: scenarios, live recalculation, levers, assumptions, and the Settings page."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ... import household, projection
from ..app import Conn, FieldError, _int, _num, _opt_int, month_label, redirect, render

router = APIRouter()

KIND_NAMES = {
    "current": "Current", "savings": "Savings", "cash": "Cash", "cash_isa": "Cash ISA", "stocks": "Stocks",
    "stocks_isa": "Stocks ISA", "pension": "Pension", "property": "Property", "other_asset": "Other asset",
    "credit_card": "Credit card", "loan": "Loan", "other_liability": "Other liability", "mortgage": "Mortgage",
}


def _milestone_pack(result: dict) -> list[dict]:
    out = []
    for r in result["milestones"]:
        gain = r["net_worth"] - result["start_net_worth"]
        out.append({**r, "year": r["month"] // 12, "gain": gain})
    return out


def _payoff(result: dict) -> dict | None:
    """The first mortgage payoff as {month, label} ("Jul 2034"), or None when it is never cleared in the horizon."""
    if not result["payoff"]:
        return None
    m = min(result["payoff"].values())
    return {"month": m, "label": month_label(result["rows"][m]["date"][:7])}


def _projection_page(request: Request, conn, scenario: str | None = None, compare: str | None = None,
                     errors=None, form=None, status=200):
    scs = projection.scenarios(conn)
    sid = _opt_int(scenario) or next(s["id"] for s in scs if s["is_default"])
    if not any(s["id"] == sid for s in scs):
        sid = next(s["id"] for s in scs if s["is_default"])
    inp, meta = projection.build_inputs(conn, sid)
    result = projection.run(inp)
    cmp_id = _opt_int(compare)
    cmp_result = cmp_name = None
    if cmp_id and cmp_id != sid and any(s["id"] == cmp_id for s in scs):
        cmp_inp, _ = projection.build_inputs(conn, cmp_id)
        cmp_result = projection.run(cmp_inp)
        cmp_name = next(s["name"] for s in scs if s["id"] == cmp_id)
    cf = meta["_cash_flow"]
    levers = meta["_levers"]
    plan = projection.plan_summary(levers, cf)
    milestones = _milestone_pack(result)
    cmp_milestones = _milestone_pack(cmp_result) if cmp_result else None
    cmp_by_year = {m["year"]: m for m in cmp_milestones} if cmp_milestones else {}
    payoff = _payoff(result)
    chart = {
        "labels": [r["date"][:7] for r in result["rows"]],
        "classes": result["classes"],
        "series": projection.band_series(result),
        "net_worth": [round(r["net_worth"], 2) for r in result["rows"]],
        "net_worth_real": [round(r["net_worth_real"], 2) for r in result["rows"]],
        "compare": [round(r["net_worth"], 2) for r in cmp_result["rows"]] if cmp_result else None,
        "compare_name": cmp_name,
        "milestones": [{"month": m["month"], "year": m["year"], "net_worth": round(m["net_worth"], 2),
                        "net_worth_real": round(m["net_worth_real"], 2)} for m in milestones],
        "payoff": payoff,
        "horizon_months": inp.horizon_years * 12,
        "swatches": projection.swatches(result["classes"]),
    }
    saved = {
        "scenario": meta["_scenario"]["id"],
        "levers": {str(r["id"]): int(r["pct"]) for r in levers["rows"]},
        "surplus_mode": levers["surplus_mode"],
        "surplus_override": "" if levers["surplus_override"] is None else f"{levers['surplus_override']:.0f}",
        "surplus_target": levers["surplus_target"] or "",
        "saved_total": levers["saved_total"], "extra_monthly": levers["extra_monthly"],
    }
    period = (f"{month_label(cf['months'][0])}–{month_label(cf['months'][-1])}" if len(cf["months"]) > 1
              else month_label(cf["months"][0]) if cf["months"] else None)
    return render(
        request, "projection.html", status_code=status, scenarios=scs, scenario=meta["_scenario"], items=inp.items,
        meta=meta, inp=inp, result=result, milestones=milestones, cmp_milestones=cmp_milestones, cmp_by_year=cmp_by_year,
        cmp_name=cmp_name, cmp_id=cmp_id or "", chart=chart, saved=saved, plan=plan, period=period, payoff=payoff,
        cash_flow=cf, levers=levers, yearly=projection.yearly_rows(result),
        cmp_yearly={r["month"]: r for r in projection.yearly_rows(cmp_result)} if cmp_result else {},
        targets=[it for it in inp.items if not it.is_liability and it.kind not in ("property", "credit_card")],
        settings={s["key"]: s for s in projection.all_settings(conn)}, kind_names=KIND_NAMES,
        counts={"levers": sum(1 for r in levers["rows"] if r["pct"]),
                "items": conn.execute("SELECT COUNT(*) FROM scenario_items WHERE scenario_id = ?", (sid,)).fetchone()[0]},
        compact=projection.compact_money, errors=errors or {}, form=form or {},
    )


@router.get("/projection", response_class=HTMLResponse)
def projection_page(request: Request, conn: Conn, scenario: str | None = None, compare: str | None = None):
    return _projection_page(request, conn, scenario, compare)


def _parse_live(savings: str | None, surplus_mode: str | None, surplus_override: str | None, surplus_target: str | None) -> dict:
    live: dict = {}
    if savings is not None:
        cs = {}
        for part in savings.split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    cs[str(int(k))] = float(v)
                except ValueError:
                    continue
        live["category_savings"] = cs
    if surplus_mode:
        live["surplus_mode"] = surplus_mode
    if surplus_override is not None:
        live["surplus_override"] = _num(surplus_override, "surplus_override")
    if surplus_target is not None:
        live["surplus_target"] = surplus_target or None
    return live


@router.get("/api/projection")
def projection_api(conn: Conn, scenario: str | None = None, savings: str | None = None, surplus_mode: str | None = None,
                   surplus_override: str | None = None, surplus_target: str | None = None):
    """Recalculate with unsaved lever values: used by the sliders on the Projection page."""
    scs = projection.scenarios(conn)
    sid = _opt_int(scenario) or next(s["id"] for s in scs if s["is_default"])
    try:
        live = _parse_live(savings, surplus_mode, surplus_override, surplus_target)
    except FieldError as exc:
        return JSONResponse({"errors": {exc.field: exc.message}}, status_code=400)
    inp, meta = projection.build_inputs(conn, sid, live=live)
    result = projection.run(inp)
    lev = meta["_levers"]
    plan = projection.plan_summary(lev, meta["_cash_flow"])
    return JSONResponse({
        "classes": result["classes"],
        "series": projection.band_series(result),
        "net_worth": [round(r["net_worth"], 2) for r in result["rows"]],
        "net_worth_real": [round(r["net_worth_real"], 2) for r in result["rows"]],
        "milestones": [{"month": r["month"], "year": r["month"] // 12, "net_worth": r["net_worth"], "net_worth_real": r["net_worth_real"],
                        "liquid": r["liquid"], "equity": r["equity"], "mortgage": r["mortgage"],
                        "contributions": r["contributions"], "gain": r["net_worth"] - result["start_net_worth"]}
                       for r in result["milestones"]],
        "payoff": _payoff(result),
        "levers": {"saved_total": lev["saved_total"], "surplus": lev["surplus"], "surplus_used": lev["surplus_used"],
                   "extra_monthly": lev["extra_monthly"], "rows": [{"id": r["id"], "saved": r["saved"], "new_spend": r["new_spend"]} for r in lev["rows"]]},
        "plan": plan,
    })


@router.post("/projection/{scenario_id}/levers")
async def save_levers(request: Request, conn: Conn, scenario_id: int):
    form = await request.form()
    cs = {}
    for key, value in form.items():
        if key.startswith("save__"):
            try:
                cs[int(key.split("__")[1])] = float(value)
            except ValueError:
                continue
    try:
        override = _num(form.get("surplus_override"), "surplus_override")
    except FieldError as exc:
        return _projection_page(request, conn, str(scenario_id), errors={exc.field: exc.message},
                                form={exc.field: form.get("surplus_override")}, status=400)
    projection.save_levers(conn, scenario_id, form.get("surplus_mode", "ignore"), override,
                           form.get("surplus_target") or None, cs)
    return redirect(f"/projection?scenario={scenario_id}", "Levers saved to scenario")


@router.post("/projection/{scenario_id}/assumptions")
async def save_assumptions(request: Request, conn: Conn, scenario_id: int):
    form = await request.form()
    try:
        g = {
            "name": (form.get("name") or "").strip() or None,
            "horizon_years": _int(form.get("horizon_years"), "horizon_years"),
            "inflation_pct": _num(form.get("inflation_pct"), "inflation_pct"),
            "property_growth_pct": _num(form.get("property_growth_pct"), "property_growth_pct"),
            "mortgage_rate_after_fix": _num(form.get("mortgage_rate_after_fix"), "mortgage_rate_after_fix"),
            "mortgage_overpayment": _num(form.get("mortgage_overpayment"), "mortgage_overpayment"),
            "contribution_growth_pct": _num(form.get("contribution_growth_pct"), "contribution_growth_pct"),
        }
        if g["horizon_years"] is not None and not 1 <= g["horizon_years"] <= 50:
            raise FieldError("horizon_years", "Enter a horizon between 1 and 50 years.")
        items = []
        for key in form.keys():
            if key.startswith("rate__"):
                _, ref_type, ref_id = key.split("__")
                items.append({
                    "ref_type": ref_type, "ref_id": int(ref_id),
                    "annual_growth_pct": _num(form.get(key), key),
                    "monthly_contribution": _num(form.get(f"contrib__{ref_type}__{ref_id}"), f"contrib__{ref_type}__{ref_id}"),
                    "contribution_until": form.get(f"until__{ref_type}__{ref_id}") or None,
                })
    except FieldError as exc:
        return _projection_page(request, conn, str(scenario_id), errors={exc.field: exc.message},
                                form={k: v for k, v in form.items()}, status=400)
    projection.save_assumptions(conn, scenario_id, g, items)
    return redirect(f"/projection?scenario={scenario_id}", "Assumptions saved")


@router.post("/projection/{scenario_id}/clone")
def clone_scenario(conn: Conn, scenario_id: int, name: str = Form("")):
    name = name.strip()
    if not name:
        return redirect(f"/projection?scenario={scenario_id}", "Give the new scenario a name.", "error")
    new_id = projection.clone_scenario(conn, scenario_id, name)
    return redirect(f"/projection?scenario={new_id}", f"Scenario {name} created")


@router.post("/projection/{scenario_id}/delete")
def delete_scenario(conn: Conn, scenario_id: int):
    if not projection.delete_scenario(conn, scenario_id):
        return redirect(f"/projection?scenario={scenario_id}", "The Base scenario can't be deleted.", "error")
    return redirect("/projection", "Scenario deleted")


# ----------------------------------------------------------------- settings
def household_context(conn) -> dict:
    """The Household panel: name, and each person with what they own (a person who owns anything can't be removed)."""
    people = []
    for p in household.people(conn):
        counts = household.owned_counts(conn, p["key"])
        owns = []
        if counts["accounts"]:
            owns.append(f"{counts['accounts']} account" + ("" if counts["accounts"] == 1 else "s"))
        if counts["holdings"]:
            owns.append(f"{counts['holdings']} asset" + ("" if counts["holdings"] == 1 else "s"))
        people.append({**p, **counts, "owns": " and ".join(owns)})
    return {"name": household.name(conn), "people": people, "subtitle": household.subtitle(conn)}


def settings_page_response(request: Request, conn, status: int = 200, settings=None, errors=None, household_form=None):
    return render(request, "settings.html", status_code=status, settings=settings or projection.all_settings(conn),
                  errors=errors or {}, household=household_context(conn), household_form=household_form or {})


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, conn: Conn):
    return settings_page_response(request, conn)


@router.post("/settings")
async def save_settings(request: Request, conn: Conn):
    form = await request.form()
    values = {k: str(v) for k, v in form.items()}
    errors = {}
    for key, raw in values.items():
        if key in projection.SETTING_DEFAULTS:
            try:
                _num(raw, key)
            except FieldError as exc:
                errors[key] = exc.message
    if errors:
        rows = projection.all_settings(conn)
        for s in rows:
            if s["key"] in values:
                s["value"] = values[s["key"]]
        return settings_page_response(request, conn, status=400, settings=rows, errors=errors)
    projection.save_settings(conn, values)
    return redirect("/settings", "Settings saved")
