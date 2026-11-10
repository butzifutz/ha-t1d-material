"""Authentifizierte HTTP-Views: Foto-Upload, Evidenz, Export."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CGM, SETZSTELLE_DEXCOM, TYP_NAMEN
from .model import ModellFehler, parse

MAX_FOTO = 15 * 1024 * 1024

EXPORT_SPALTEN = [
    "id", "typ", "start", "ende", "quelle", "setzstelle", "seite", "produkt", "charge", "schlauch_gefuellt",
    "seriennummer", "endgrund", "problemart", "problem_zeit", "problem_text", "ruecksendung",
    "groesste_luecke_min", "luecke_stufe", "evidenz_status", "foto",
]


def registrieren(hass: HomeAssistant) -> None:
    hass.http.register_view(FotoView)
    hass.http.register_view(EvidenzView)
    hass.http.register_view(ExportView)
    hass.http.register_view(ReklamationView)


def _v(request: web.Request):
    from . import verwaltung

    return verwaltung(request.app["hass"])


def _zyklus(v, zyklus_id: str) -> dict:
    try:
        return v.modell.zyklus(zyklus_id)
    except ModellFehler as e:
        raise web.HTTPNotFound(text=str(e)) from e


class FotoView(HomeAssistantView):
    url = "/api/t1d_material/foto/{zyklus_id}"
    name = "api:t1d_material:foto"

    async def get(self, request: web.Request, zyklus_id: str) -> web.StreamResponse:
        v = _v(request)
        _zyklus(v, zyklus_id)
        pfad = v.foto_pfad(zyklus_id)
        if not await request.app["hass"].async_add_executor_job(pfad.exists):
            raise web.HTTPNotFound()
        return web.FileResponse(pfad, headers={"Content-Type": "image/jpeg"})

    async def post(self, request: web.Request, zyklus_id: str) -> web.Response:
        v = _v(request)
        _zyklus(v, zyklus_id)
        daten = await request.content.read(MAX_FOTO + 1)
        if len(daten) > MAX_FOTO:
            return self.json_message("Foto zu groß", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if not daten.startswith(b"\xff\xd8"):
            return self.json_message("Nur JPEG", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        pfad = v.foto_pfad(zyklus_id)
        await request.app["hass"].async_add_executor_job(pfad.write_bytes, daten)

        def merken(m):
            m.zyklus(zyklus_id)["foto"] = pfad.name

        await v.aendern(merken)
        return self.json({"foto": pfad.name, "bytes": len(daten)})


class EvidenzView(HomeAssistantView):
    url = "/api/t1d_material/evidenz/{zyklus_id}"
    name = "api:t1d_material:evidenz"

    async def get(self, request: web.Request, zyklus_id: str) -> web.Response:
        v = _v(request)
        _zyklus(v, zyklus_id)
        ev = await request.app["hass"].async_add_executor_job(v.evidenz_laden, zyklus_id)
        if ev is None:
            raise web.HTTPNotFound(text="Noch keine Evidenz")
        return self.json(ev)


def _csv(zyklen: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, EXPORT_SPALTEN, extrasaction="ignore", delimiter=";")
    w.writeheader()
    for c in sorted(zyklen, key=lambda c: parse(c["start"])):
        w.writerow({**c, "typ": TYP_NAMEN.get(c["typ"], c["typ"])})
    return buf.getvalue()


class ExportView(HomeAssistantView):
    url = "/api/t1d_material/export.csv"
    name = "api:t1d_material:export"

    async def get(self, request: web.Request) -> web.Response:
        v = _v(request)
        return web.Response(
            body=("﻿" + _csv(v.modell.z["zyklen"])).encode("utf-8"),  # BOM für Excel
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": 'attachment; filename="t1d-material.csv"'},
        )


def reklamation_text(c: dict) -> str:
    """Antworten in der Reihenfolge des Dexcom-Formulars."""
    def j(b):
        return {True: "Ja", False: "Nein"}.get(b, "–")

    def d(z, mit_zeit=True):
        if not z:
            return "–"
        t = dt_util.as_local(parse(z))
        return t.strftime("%d.%m.%Y %H:%M" if mit_zeit else "%d.%m.%Y")

    zeilen = [
        "Dexcom-Reklamation – Angaben",
        "",
        f"Datum des Problems:        {d(c.get('problem_zeit') or c.get('ende'), False)}",
        f"Problem:                   {c.get('problemart') or '–'}",
    ]
    if c.get("problem_text"):
        zeilen.append(f"Beschreibung:              {c['problem_text']}")
    zeilen += [
        f"Produkt:                   {c.get('produkt') or '–'}",
        f"Seriennummer:              {c.get('seriennummer') or '– (siehe Foto)'}",
        f"Charge:                    {c.get('charge') or '–'}",
        f"Setzstelle:                {SETZSTELLE_DEXCOM.get(c.get('setzstelle'), c.get('setzstelle')) or '–'}"
        + (f" ({c['seite']})" if c.get("seite") else ""),
        f"Sensor für Rücksendung:    {j(c.get('ruecksendung'))}",
        f"Längste Zeit ohne Werte:   {c.get('luecke_stufe') or 'Keine Angabe'}"
        + (f" ({c['groesste_luecke_min']:.0f} min)" if c.get("groesste_luecke_min") is not None else ""),
        "Setzstelle mit Alkoholtupfer gereinigt: Ja",
        "",
        f"Gesetzt:  {d(c.get('start'))}",
        f"Entfernt: {d(c.get('ende')) if c.get('ende') else 'läuft noch'}",
    ]
    return "\n".join(zeilen) + "\n"


class ReklamationView(HomeAssistantView):
    url = "/api/t1d_material/reklamation/{zyklus_id}"
    name = "api:t1d_material:reklamation"

    async def get(self, request: web.Request, zyklus_id: str) -> web.Response:
        v = _v(request)
        c = _zyklus(v, zyklus_id)
        if c["typ"] != CGM:
            raise web.HTTPBadRequest(text="Reklamationspaket nur für CGM-Sensoren")
        hass = request.app["hass"]

        def bauen() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("reklamation.txt", reklamation_text(c))
                z.writestr("zyklus.json", json.dumps(c, ensure_ascii=False, indent=2))
                foto = v.foto_pfad(zyklus_id)
                if foto.exists():
                    z.write(foto, "verpackung.jpg")
                ev = v.evidenz_laden(zyklus_id)
                if ev:
                    z.writestr(
                        "cgm_letzte_werte.csv",
                        "zeit;mg_dl\n" + "".join(f"{w['zeit']};{w['mg_dl']}\n" for w in ev["werte"]),
                    )
            return buf.getvalue()

        daten = await hass.async_add_executor_job(bauen)
        name = f"reklamation-{c['start'][:10]}.zip"
        return web.Response(
            body=daten, content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
