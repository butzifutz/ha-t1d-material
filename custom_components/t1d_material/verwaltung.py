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
    CONF_INSULIN_FUELLEN_U,
    CONF_INSULIN_KANUELE_U,
    CONF_INSULIN_RESERVOIR_U,
    CONF_RESERVOIR_ERKENNEN,
    CONF_TIR_HOCH,
    CONF_TIR_SEHR_HOCH,
    CONF_TIR_SEHR_TIEF,
    CONF_TIR_TIEF,
    CONF_INSULIN_VOLL_U,
    CONF_INSULIN_WARNUNG_U,
    CONF_MERGE_H,
    CONF_PACKUNG,
    CONF_PASSWORD,
    CONF_PRODUKT,
    CONF_TOLERANZ_H,
    CONF_TRAGEZEIT_D,
    DATA_DIR,
    DEFAULTS_GLOBAL,
    DEFAULTS_TYP,
    AMPULLE,
    GLOOKO_SCHLAUCH,
    GLOOKO_SERIE,
    MIT_TRAGEZEIT,
    STORAGE_KEY,
    SCHLAUCH_FENSTER_MIN,
    SET,
    STORAGE_VERSION,
    TIR_HALBWERTSZEIT_TAGE,
    TIR_SCHNITT_TAGE,
    TYPEN,
)
from .glooko import (
    CGM_SERIEN,
    INSULIN_SERIEN,
    GlookoClient,
    GlookoFehler,
    cgm_werte,
    glooko_zeit,
    insulin_daten,
)
from .model import (
    InsulinConfig,
    Modell,
    TypConfig,
    groesste_luecke_min,
    insulin_summe,
    iso,
    leerer_zustand,
    luecke_stufe,
    migrieren,
    parse,
    tir_statistik,
)

_LOGGER = logging.getLogger(__name__)

GLOOKO_FENSTER = timedelta(days=14)
EVIDENZ_AUFGEBEN = timedelta(hours=48)  # danach Evidenz mit dem abschließen, was da ist
VERBRAUCH_TAGE = 7  # Ø-Tagesverbrauch Insulin über so viele Tage


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
        self._cgm: list[tuple[datetime, int]] = []  # CGM-Werte der letzten 14 Tage (für TIR)
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

    def insulin_config(self) -> InsulinConfig:
        return InsulinConfig(
            voll_u=float(self.cfg[CONF_INSULIN_VOLL_U]),
            fuellen_u=float(self.cfg[CONF_INSULIN_FUELLEN_U]),
            warnung_u=float(self.cfg[CONF_INSULIN_WARNUNG_U]),
            kanuele_u=float(self.cfg[CONF_INSULIN_KANUELE_U]),
            reservoir_u=float(self.cfg[CONF_INSULIN_RESERVOIR_U]),
        )

    @property
    def reservoir_erkennen(self) -> bool:
        return bool(self.cfg[CONF_RESERVOIR_ERKENNEN])

    @property
    def tir_grenzen(self) -> tuple[float, float, float, float]:
        return tuple(float(self.cfg[k]) for k in (CONF_TIR_SEHR_TIEF, CONF_TIR_TIEF, CONF_TIR_HOCH, CONF_TIR_SEHR_HOCH))

    def config_uebernehmen(self) -> None:
        """Geänderte Optionen ohne Neuladen anwenden (Tragezeiten, Packungen, Insulin)."""
        self.cfg = wirksame_config(self.entry)
        self.modell.config = self.typ_config()
        self.modell.insulin = self.insulin_config()
        if self._cgm:  # neue Grenzen sofort anwenden
            self.modell.z["tir"] = self._tir(self._cgm)
        self.benachrichtigen()

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
        migriert = migrieren(daten)
        self.modell = Modell(daten, self.typ_config(), self.insulin_config())
        if migriert:
            await self._speichern()
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
                jetzt - GLOOKO_FENSTER, jetzt + timedelta(days=1), [*GLOOKO_SERIE.values(), GLOOKO_SCHLAUCH]
            )
            async with self._lock:
                def zeiten(serie: str) -> list[datetime]:
                    z = [glooko_zeit(p["timestamp"], tz) for p in serien[serie] if p.get("timestamp")]
                    return [t for t in z if t <= jetzt + timedelta(minutes=5)]

                for typ, serie in GLOOKO_SERIE.items():
                    neu = self.modell.glooko_events(typ, zeiten(serie), jetzt, self.dedupe, self.merge_fenster)
                    for c in neu:
                        _LOGGER.info("Glooko: %s-Wechsel %s übernommen (%s)", typ, c["start"], c["quelle"])
                # „Schlauch füllen“: mit Kanüle füllen daneben = Setwechsel mit Schlauch, sonst Reservoirwechsel
                fenster = timedelta(minutes=SCHLAUCH_FENSTER_MIN)
                kanuele = zeiten(GLOOKO_SERIE[SET])
                mit_set, allein = [], []
                for t in zeiten(GLOOKO_SCHLAUCH):
                    (mit_set if any(abs(t - k) <= fenster for k in kanuele) else allein).append(t)
                n = self.modell.glooko_reservoir_zuruecknehmen(mit_set, jetzt)
                if n:
                    _LOGGER.info("Glooko: %d Reservoirwechsel als Setwechsel mit Schlauch erkannt", n)
                n = self.modell.glooko_schlauch(mit_set, jetzt, reservoir=allein)
                if n:
                    _LOGGER.info("Glooko: %d Schlauchfüllung(en) übernommen", n)
                if self.reservoir_erkennen:
                    neu = self.modell.glooko_events(AMPULLE, allein, jetzt, self.dedupe, self.merge_fenster)
                    for c in neu:
                        _LOGGER.info("Glooko: Reservoirwechsel %s übernommen (%s)", c["start"], c["quelle"])
                g["letzter_sync"] = iso(jetzt)
                g["fehler"] = None
                await self._speichern()
            await self._insulin_abgleich(jetzt, tz)
            await self._tir_abgleich(jetzt, tz)
            await self._evidenz_sammeln(jetzt, tz)
        except GlookoFehler as e:
            _LOGGER.warning("Glooko-Abgleich fehlgeschlagen: %s", e)
            async with self._lock:
                g["fehler"] = str(e)
                await self._speichern()
        self.benachrichtigen()

    async def _insulin_abgleich(self, jetzt: datetime, tz) -> None:
        """Verbrauch seit dem letzten Reservoirwechsel + Ø-Tagesverbrauch aus Basal und Bolus."""
        c = self.modell.aktiv(AMPULLE)
        von = jetzt - timedelta(days=VERBRAUCH_TAGE + 1)
        if c is not None:
            von = min(von, parse(c["start"]))
        serien = await self._glooko.serien(von, jetzt + timedelta(days=1), INSULIN_SERIEN)
        basal, boli, stand = insulin_daten(serien, tz)
        if stand is None:
            return
        stand = min(stand, jetzt)
        tage = insulin_summe(basal, boli, stand - timedelta(days=VERBRAUCH_TAGE), stand) / VERBRAUCH_TAGE
        info: dict[str, Any] = {"stand": iso(stand), "tagesverbrauch_u": round(tage, 2) if tage > 0 else None}
        if c is not None:
            info["zyklus_id"] = c["id"]
            info["verbrauch_u"] = round(insulin_summe(basal, boli, parse(c["start"]), stand), 2)

        def setzen(m: Modell):
            m.z["insulin"] = info

        await self.aendern(setzen)

    async def _tir_abgleich(self, jetzt: datetime, tz) -> None:
        """CGM-Werte der letzten 14 Tage holen und die gewichtete Zeit in den Bereichen berechnen."""
        serien = await self._glooko.serien(
            jetzt - timedelta(days=TIR_SCHNITT_TAGE, hours=3), jetzt + timedelta(days=1), CGM_SERIEN
        )
        self._cgm = [w for w in cgm_werte(serien, tz) if w[0] <= jetzt]
        tir = self._tir(self._cgm)

        def setzen(m: Modell):
            m.z["tir"] = tir

        await self.aendern(setzen)

    def _tir(self, werte: list[tuple[datetime, int]]) -> dict[str, Any]:
        """Gewichtet ab dem letzten verfügbaren Wert (Glooko hinkt ~1,5 h hinterher)."""
        if not werte:
            return {"stand": None, "grenzen": list(self.tir_grenzen)}
        bis = werte[-1][0]
        anteile = tir_statistik(
            werte, bis, self.tir_grenzen,
            timedelta(days=TIR_HALBWERTSZEIT_TAGE), timedelta(days=TIR_SCHNITT_TAGE),
        )
        return {"stand": iso(bis), "grenzen": list(self.tir_grenzen), **(anteile or {})}

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
