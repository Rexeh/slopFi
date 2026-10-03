"""Settings > Household: the household's name and its people (owner choices everywhere else come from here)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request

from ... import household
from ..app import Conn, redirect
from .projection import settings_page_response

router = APIRouter()
URL = "/settings#household"


@router.post("/settings/household")
def save_name(request: Request, conn: Conn, name: str = Form("")):
    name = name.strip()
    if not name:
        return settings_page_response(request, conn, status=400, errors={"household_name": "Give the household a name."},
                                      household_form={"name": name})
    household.set_name(conn, name)
    return redirect(URL, "Household name saved")


@router.post("/settings/household/people")
def add_person(request: Request, conn: Conn, name: str = Form("")):
    try:
        person = household.add_person(conn, name)
    except ValueError as exc:
        return settings_page_response(request, conn, status=400, errors={"person_name": str(exc)},
                                      household_form={"person_name": name})
    return redirect(URL, f"{person['name']} added")


@router.post("/settings/household/people/{key}/rename")
def rename_person(conn: Conn, key: str, name: str = Form("")):
    try:
        household.rename_person(conn, key, name)
    except KeyError:
        return redirect(URL, "That person is no longer in the household", kind="info")
    except ValueError as exc:
        return redirect(URL, f"Not renamed: {exc}", kind="error")
    return redirect(URL, f"Renamed to {name.strip()}")


@router.post("/settings/household/people/{key}/delete")
def remove_person(conn: Conn, key: str):
    person = next((p for p in household.people(conn) if p["key"] == key), None)
    if person is None:
        return redirect(URL, "That person was already removed", kind="info")
    if not household.remove_person(conn, key):
        counts = household.owned_counts(conn, key)
        owns = ", ".join(f"{n} {noun}{'' if n == 1 else 's'}" for noun, n in
                         (("account", counts["accounts"]), ("asset", counts["holdings"])) if n)
        return redirect(URL, f"{person['name']} owns {owns}, so they were not removed. Change the owner first.",
                        kind="error")
    return redirect(URL, f"{person['name']} removed")
