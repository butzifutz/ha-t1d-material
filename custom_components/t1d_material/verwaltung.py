"""Verwaltung: hält Modell + Store, kapselt Glooko-Abgleich, Fotos und Evidenz."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CGM,
    CONF_DEDUPE_MIN,
    CONF_EMAIL,
    CONF_EVIDENZ_H,
    CONF_MERGE_H,
    CONF_PACKUNG,
    CONF_PASSWORD,
    CONF_PRODUKT,
    CONF_TOLERANZ_H,
    CONF_TRAGEZEIT_D,
    DATA_DIR,
    DEFAULTS_GLOBAL,
    DEFAULTS_TYP,
    GLOOKO_SERIE,
    MIT_TRAGEZEIT,
    STORAGE_KEY,
    STORAGE_VERSION,
    TYPEN,
)
from .glooko import CGM_SERIEN, GlookoClient, GlookoFehler, cgm_werte, glooko_zeit
from .model import Modell, TypConfig, groesste_luecke_min, iso, leerer_zustand, luecke_stufe, parse

_LOGGER = logging.getLogger(__name__)

GLOOKO_FENSTER = timedelta(days=14)
EVIDENZ_AUFGEBEN = timedelta(hours=48)  # danach Evidenz mit dem abschließen, was da ist


def typ_key(typ: str, key: str) -> str:
    return f"{typ}_{key}"


def wirksame_config(entry: ConfigEntry) -> dict[str, Any]:
    cfg: dict[str, Any] = dict(DEFAULTS_GLOBAL)
    for typ, werte in DEFAULTS_TYP.items():
        for k, v in werte.items():
            cfg[typ_key(typ, k)] = v
    cfg.update(entry.data)
    cfg.update(entry.options)
    return cfg


class Verwaltung:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.cfg = wirksame_config(entry)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
        self._lock = asyncio.Lock()
        self._listener: list[Callable[[], None]] = []
        self.modell: Modell
        self.verzeichnis = Path(hass.config.path(DATA_DIR))
        self._glooko: GlookoClient | None = None
        if self.cfg.get(CONF_EMAIL) and self.cfg.get(CONF_PASSWORD):
            session = async_create_clientsession(hass, auto_cleanup=True)
            self._glooko = GlookoClient(session, self.cfg[CONF_EMAIL], self.cfg[CONF_PASSWORD])

    # ------------------------------------------------------------- Grundlagen
    def typ_config(self) -> dict[str, TypConfig]:
        return {
            t: TypConfig(
                tragezeit_h=float(self.cfg[typ_key(t, CONF_TRAGEZEIT_D)]) * 24 if t in MIT_TRAGEZEIT else None,
                toleranz_h=float(self.cfg.get(typ_key(t, CONF_TOLERANZ_H), 0)),
                packung=int(self.cfg[typ_key(t, CONF_PACKUNG)]),
            )
            for t in TYPEN
        }

    @property
    def produkt(self) -> str:
        return self.cfg.get(typ_key(CGM, CONF_PRODUKT), "G7")

    @property
    def glooko_aktiv(self) -> bool:
        return self._glooko is not None

    async def async_laden(self) -> None:
        daten = await self._store.async_load()
        if daten is None:
            daten = leerer_zustand(dt_util.now())
        self.modell = Modell(daten, self.typ_config())
        await self.hass.async_add_executor_job(self._verzeichnisse_anlegen)

    def _verzeichnisse_anlegen(self) -> None:
        (self.verzeichnis / "fotos").mkdir(parents=True, exist_ok=True)
        (self.verzeichnis / "evidenz").mkdir(parents=True, exist_ok=True)

    async def _speichern(self) -> None:
        await self._store.async_save(self.modell.z)

    @callback
    def anmelden(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listener.append(cb)
        return lambda: self._listener.remove(cb)

    @callback
    def benachrichtigen(self) -> None:
        for cb in list(self._listener):
            cb()

    async def aendern(self, fn: Callable[[Modell], Any]) -> Any:
        """Modelländerung atomar ausführen, speichern, Entitäten aktualisieren."""
        async with self._lock:
            ergebnis = fn(self.modell)
            await self._speichern()
        self.benachrichtigen()
        return ergebnis

    @property
    def merge_fenster(self) -> timedelta:
        return timedelta(hours=float(self.cfg[CONF_MERGE_H]))

    @property
    def dedupe(self) -> timedelta:
        return timedelta(minutes=float(self.cfg[CONF_DEDUPE_MIN]))

    # ----------------------------------------------------------------- Glooko
    async def glooko_abgleich(self) -> None:
        if self._glooko is None:
            return
        jetzt = dt_util.now()
        tz = dt_util.get_default_time_zone()
        g = self.modell.z["glooko"]
        try:
            serien = await self._glooko.serien(
                jetzt - GLOOKO_FENSTER, jetzt + timedelta(days=1), list(GLOOKO_SERIE.values())
            )
            async with self._lock:
                for typ, serie in GLOOKO_SERIE.items():
                    zeiten = [glooko_zeit(p["timestamp"], tz) for p in serien[serie] if p.get("timestamp")]
                    zeiten = [z for z in zeiten if z <= jetzt + timedelta(minutes=5)]
                    neu = self.modell.glooko_events(typ, zeiten, jetzt, self.dedupe, self.merge_fenster)
                    for c in neu:
                        _LOGGER.info("Glooko: %s-Wechsel %s übernommen (%s)", typ, c["start"], c["quelle"])
                g["letzter_sync"] = iso(jetzt)
                g["fehler"] = None
                await self._speichern()
            await self._evidenz_sammeln(jetzt, tz)
        except GlookoFehler as e:
            _LOGGER.warning("Glooko-Abgleich fehlgeschlagen: %s", e)
            async with self._lock:
                g["fehler"] = str(e)
                await self._speichern()
        self.benachrichtigen()

    async def _evidenz_sammeln(self, jetzt: datetime, tz) -> None:
        for c in self.modell.offene_evidenz():
            start, ende = parse(c["start"]), parse(c["ende"])
            serien = await self._glooko.serien(start, ende + timedelta(minutes=5), CGM_SERIEN)
            werte = cgm_werte(serien, tz)
            letzte = werte[-1][0] if werte else None
            vollstaendig = letzte is not None and letzte >= ende - timedelta(minutes=15)
            if not vollstaendig and jetzt - ende < EVIDENZ_AUFGEBEN:
                continue  # Glooko hat die Daten bis zum Ende noch nicht – nächster Abruf
            await self.evidenz_speichern(c, werte)

    async def evidenz_speichern(self, c: dict, werte: list[tuple[datetime, int]]) -> None:
        start, ende = parse(c["start"]), parse(c["ende"])
        fenster = timedelta(hours=float(self.cfg[CONF_EVIDENZ_H]))
        luecke = groesste_luecke_min([t for t, _ in werte], start, ende)
        letzte = [{"zeit": iso(t), "mg_dl": v} for t, v in werte if t >= ende - fenster]
        datei = self.verzeichnis / "evidenz" / f"{c['id']}.json"
        inhalt = {"zyklus_id": c["id"], "start": c["start"], "ende": c["ende"],
                  "fenster_h": fenster.total_seconds() / 3600, "werte": letzte}
        await self.hass.async_add_executor_job(
            datei.write_text, json.dumps(inhalt, ensure_ascii=False), "utf-8"
        )

        def setzen(m: Modell):
            c["groesste_luecke_min"] = luecke
            c["luecke_stufe"] = luecke_stufe(luecke)
            c["evidenz_werte"] = len(letzte)
            c["evidenz_status"] = "fertig" if werte else "keine_daten"

        await self.aendern(setzen)

    def evidenz_laden(self, zyklus_id: str) -> dict | None:
        datei = self.verzeichnis / "evidenz" / f"{zyklus_id}.json"
        return json.loads(datei.read_text("utf-8")) if datei.exists() else None

    # ------------------------------------------------------------------ Fotos
    def foto_pfad(self, zyklus_id: str) -> Path:
        return self.verzeichnis / "fotos" / f"{zyklus_id}.jpg"
