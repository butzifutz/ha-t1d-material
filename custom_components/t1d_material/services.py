"""Services – für Automationen/Dashboards und als API der Android-App."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    CGM,
    DOMAIN,
    ENDGRUND_REGULAER,
    ENDGRUND_VORZEITIG,
    PROBLEMARTEN,
    PRODUKTE,
    QUELLE_APP,
    SEITEN,
    SETZSTELLEN,
    TYP_NAMEN,
    TYPEN,
)
from .model import EDITIERBAR, ENDE_FELDER, START_FELDER, ModellFehler, iso, parse

TYP = vol.In(TYPEN)
OPT_STR = vol.Any(None, cv.string)

START_SCHEMA = {
    vol.Optional("setzstelle"): OPT_STR,
    vol.Optional("seite"): vol.Any(None, vol.In(SEITEN)),
    vol.Optional("charge"): OPT_STR,
    vol.Optional("seriennummer"): OPT_STR,
    vol.Optional("produkt"): vol.Any(None, vol.In(PRODUKTE)),
}
ENDE_SCHEMA = {
    vol.Optional("endgrund"): vol.Any(None, vol.In([ENDGRUND_REGULAER, ENDGRUND_VORZEITIG])),
    vol.Optional("problemart"): OPT_STR,
    vol.Optional("problem_zeit"): vol.Any(None, cv.datetime),
    vol.Optional("problem_text"): OPT_STR,
    vol.Optional("ruecksendung"): vol.Any(None, cv.boolean),
}

WECHSEL = vol.Schema({
    vol.Required("typ"): TYP,
    vol.Optional("zeit"): cv.datetime,
    vol.Optional("request_id"): cv.string,
    **START_SCHEMA,
    **ENDE_SCHEMA,
})
BEENDEN = vol.Schema({
    vol.Required("typ"): TYP,
    vol.Optional("zeit"): cv.datetime,
    vol.Optional("request_id"): cv.string,
    **ENDE_SCHEMA,
})
BEARBEITEN = vol.Schema({
    vol.Required("zyklus_id"): cv.string,
    vol.Optional("start"): cv.datetime,
    vol.Optional("ende"): vol.Any(None, cv.datetime),
    **START_SCHEMA,
    **ENDE_SCHEMA,
})
LOESCHEN = vol.Schema({vol.Required("zyklus_id"): cv.string})
BESTAND = vol.Schema({vol.Required("typ"): TYP, vol.Required("anzahl"): vol.All(vol.Coerce(int), vol.Range(min=0))})
PACKUNG = vol.Schema({vol.Required("typ"): TYP, vol.Optional("packungen", default=1): vol.All(vol.Coerce(int), vol.Range(min=1))})
DATEN = vol.Schema({vol.Optional("historie", default=50): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000))})


def _zeit(d: dict, key: str = "zeit") -> datetime:
    z = d.get(key)
    if z is None:
        return dt_util.now()
    return z if z.tzinfo else z.replace(tzinfo=dt_util.get_default_time_zone())


def _felder(d: dict, felder) -> dict[str, Any]:
    out = {}
    for f in felder:
        if f in d:
            v = d[f]
            if isinstance(v, datetime):
                v = iso(v if v.tzinfo else v.replace(tzinfo=dt_util.get_default_time_zone()))
            out[f] = v
    return out


def _pruefe_listen(typ: str, d: dict) -> None:
    if d.get("setzstelle") and SETZSTELLEN[typ] and d["setzstelle"] not in SETZSTELLEN[typ]:
        raise ServiceValidationError(f"Unbekannte Setzstelle für {TYP_NAMEN[typ]}: {d['setzstelle']}")
    if d.get("problemart") and d["problemart"] not in PROBLEMARTEN[typ]:
        raise ServiceValidationError(f"Unbekannte Problemart für {TYP_NAMEN[typ]}: {d['problemart']}")


def registrieren(hass: HomeAssistant) -> None:
    from . import verwaltung

    async def ausfuehren(fn):
        try:
            return await verwaltung(hass).aendern(fn)
        except ModellFehler as e:
            raise ServiceValidationError(str(e)) from e

    async def wechsel(call: ServiceCall) -> ServiceResponse:
        d = call.data
        typ = d["typ"]
        _pruefe_listen(typ, d)
        v = verwaltung(hass)
        start = _felder(d, START_FELDER)
        if typ == CGM and not start.get("produkt"):
            start["produkt"] = v.produkt
        c = await ausfuehren(lambda m: m.wechsel(
            typ, _zeit(d), dt_util.now(), start=start, ende=_felder(d, ENDE_FELDER),
            quelle=QUELLE_APP, request_id=d.get("request_id"), merge_fenster=v.merge_fenster,
        ))
        return {"zyklus": c}

    async def beenden(call: ServiceCall) -> ServiceResponse:
        d = call.data
        _pruefe_listen(d["typ"], d)
        c = await ausfuehren(lambda m: m.beenden(
            d["typ"], _zeit(d), dt_util.now(), ende=_felder(d, ENDE_FELDER), request_id=d.get("request_id"),
        ))
        return {"zyklus": c}

    async def bearbeiten(call: ServiceCall) -> ServiceResponse:
        d = call.data
        v = verwaltung(hass)
        try:
            typ = v.modell.zyklus(d["zyklus_id"])["typ"]
        except ModellFehler as e:
            raise ServiceValidationError(str(e)) from e
        _pruefe_listen(typ, d)
        felder = _felder(d, EDITIERBAR)
        c = await ausfuehren(lambda m: m.bearbeiten(d["zyklus_id"], felder, dt_util.now()))
        return {"zyklus": c}

    async def loeschen(call: ServiceCall) -> None:
        await ausfuehren(lambda m: m.loeschen(call.data["zyklus_id"], dt_util.now()))

    async def bestand_setzen(call: ServiceCall) -> ServiceResponse:
        n = await ausfuehren(lambda m: m.bestand_setzen(call.data["typ"], call.data["anzahl"], dt_util.now()))
        return {"bestand": n}

    async def packung_einbuchen(call: ServiceCall) -> ServiceResponse:
        n = await ausfuehren(lambda m: m.packung_einbuchen(call.data["typ"], call.data["packungen"], dt_util.now()))
        return {"bestand": n}

    async def glooko_abrufen(call: ServiceCall) -> None:
        v = verwaltung(hass)
        if not v.glooko_aktiv:
            raise ServiceValidationError("Kein Glooko-Zugang konfiguriert")
        await v.glooko_abgleich()

    async def daten(call: ServiceCall) -> ServiceResponse:
        """Alles, was die App braucht, in einem Aufruf."""
        v = verwaltung(hass)
        jetzt = dt_util.now()
        m = v.modell
        historie = sorted(m.z["zyklen"], key=lambda c: parse(c["start"]), reverse=True)[: call.data["historie"]]
        return {
            "jetzt": iso(jetzt),
            "status": {t: _ohne_zyklus(m.status(t, jetzt)) for t in TYPEN},
            "historie": historie,
            "listen": {
                "setzstellen": SETZSTELLEN,
                "problemarten": PROBLEMARTEN,
                "seiten": SEITEN,
                "produkte": PRODUKTE,
                "typ_namen": TYP_NAMEN,
            },
            "config": {
                "produkt": v.produkt,
                "packung": {t: c.packung for t, c in v.typ_config().items()},
            },
            "glooko": {"aktiv": v.glooko_aktiv, **m.z["glooko"]},
        }

    reg = hass.services.async_register
    reg(DOMAIN, "wechsel", wechsel, WECHSEL, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "beenden", beenden, BEENDEN, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "bearbeiten", bearbeiten, BEARBEITEN, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "loeschen", loeschen, LOESCHEN)
    reg(DOMAIN, "bestand_setzen", bestand_setzen, BESTAND, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "packung_einbuchen", packung_einbuchen, PACKUNG, SupportsResponse.OPTIONAL)
    reg(DOMAIN, "glooko_abrufen", glooko_abrufen)
    reg(DOMAIN, "daten", daten, DATEN, SupportsResponse.ONLY)


def _ohne_zyklus(s: dict) -> dict:
    s = dict(s)
    c = s.pop("zyklus")
    s["zyklus_id"] = c["id"] if c else None
    s["zyklus"] = c
    return s
