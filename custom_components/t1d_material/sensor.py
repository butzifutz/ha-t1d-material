"""Sensoren je Materialtyp + Glooko-Sync."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    AMPULLE,
    CGM,
    MIT_TRAGEZEIT,
    STATUS_AKTIV,
    STATUS_KEIN,
    STATUS_TOLERANZ,
    STATUS_UEBERFAELLIG,
    TIR_FENSTER,
    TIR_KATEGORIEN,
    TYPEN,
)
from .entity import T1DEntity
from .model import parse


@dataclass(frozen=True, kw_only=True)
class T1DSensorBeschreibung(SensorEntityDescription):
    wert: Callable[[dict], Any]
    attribute: Callable[[dict], dict] | None = None
    nur_typ: str | None = None
    nur_mit_tragezeit: bool = False


def _zyk(s: dict, feld: str):
    return (s["zyklus"] or {}).get(feld)


SENSOREN: tuple[T1DSensorBeschreibung, ...] = (
    T1DSensorBeschreibung(
        key="gesetzt_am", device_class=SensorDeviceClass.TIMESTAMP,
        wert=lambda s: parse(s["gesetzt_am"]),
    ),
    T1DSensorBeschreibung(
        key="ablauf", device_class=SensorDeviceClass.TIMESTAMP, nur_mit_tragezeit=True,
        wert=lambda s: parse(s["ablauf"]),
    ),
    T1DSensorBeschreibung(
        key="toleranz_ende", device_class=SensorDeviceClass.TIMESTAMP, nur_mit_tragezeit=True,
        wert=lambda s: parse(s["toleranz_ende"]),
    ),
    T1DSensorBeschreibung(
        key="restzeit", device_class=SensorDeviceClass.DURATION, nur_mit_tragezeit=True,
        native_unit_of_measurement=UnitOfTime.HOURS, suggested_display_precision=1,
        wert=lambda s: s["restzeit_h"],
    ),
    T1DSensorBeschreibung(
        key="restzeit_prozent", native_unit_of_measurement=PERCENTAGE, nur_mit_tragezeit=True,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=0,
        icon="mdi:battery-clock", wert=lambda s: s["restzeit_prozent"],
    ),
    T1DSensorBeschreibung(
        key="status", device_class=SensorDeviceClass.ENUM,
        options=[STATUS_AKTIV, STATUS_TOLERANZ, STATUS_UEBERFAELLIG, STATUS_KEIN],
        wert=lambda s: s["status"],
    ),
    T1DSensorBeschreibung(
        key="reichweite", device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS, suggested_display_precision=1,
        wert=lambda s: s["reichweite_d"],
    ),
    T1DSensorBeschreibung(
        key="setzstelle", icon="mdi:map-marker", nur_mit_tragezeit=True,
        wert=lambda s: _zyk(s, "setzstelle"),
        attribute=lambda s: {"seite": _zyk(s, "seite")},
    ),
    T1DSensorBeschreibung(
        key="charge", icon="mdi:barcode", nur_typ=CGM,
        wert=lambda s: _zyk(s, "charge"),
        attribute=lambda s: {"seriennummer": _zyk(s, "seriennummer"), "produkt": _zyk(s, "produkt")},
    ),
    T1DSensorBeschreibung(
        key="fuellstand", icon="mdi:needle", nur_typ=AMPULLE,
        native_unit_of_measurement="U", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, wert=lambda s: s.get("fuellstand_u"),
        attribute=lambda s: {"prozent": s.get("fuellstand_prozent"), "datenstand": s.get("insulin_stand"),
                             "restzeit_h": s.get("restzeit_h")},
    ),
    T1DSensorBeschreibung(
        key="tagesverbrauch", icon="mdi:chart-bar", nur_typ=AMPULLE,
        native_unit_of_measurement="U/d", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, wert=lambda s: s.get("tagesverbrauch_u"),
    ),
    T1DSensorBeschreibung(
        key="quelle", entity_category=EntityCategory.DIAGNOSTIC,
        wert=lambda s: _zyk(s, "quelle"),
        attribute=lambda s: {"zyklus_id": _zyk(s, "id")},
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry, add: AddConfigEntryEntitiesCallback) -> None:
    v = entry.runtime_data
    ents: list[SensorEntity] = [
        T1DSensor(v, typ, b)
        for typ in TYPEN
        for b in SENSOREN
        if b.nur_typ in (None, typ) and (typ in MIT_TRAGEZEIT or not b.nur_mit_tragezeit)
    ]
    if v.glooko_aktiv:
        ents.append(GlookoSyncSensor(v))
        ents += [TirSensor(v, f, k) for f in TIR_FENSTER for k in TIR_KATEGORIEN]
    add(ents)


class T1DSensor(T1DEntity, SensorEntity):
    entity_description: T1DSensorBeschreibung

    def __init__(self, v, typ, beschreibung: T1DSensorBeschreibung):
        super().__init__(v, typ, beschreibung.key)
        self.entity_description = beschreibung

    @property
    def native_value(self):
        return self.entity_description.wert(self.status)

    @property
    def extra_state_attributes(self):
        if self.entity_description.attribute:
            return self.entity_description.attribute(self.status)
        return None


class GlookoSyncSensor(T1DEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, v):
        super().__init__(v, None, "glooko_letzter_sync")

    @property
    def native_value(self):
        return parse(self.v.modell.z["glooko"]["letzter_sync"])

    @property
    def extra_state_attributes(self):
        return {"fehler": self.v.modell.z["glooko"]["fehler"]}


class TirSensor(T1DEntity, SensorEntity):
    """Anteil der CGM-Werte in einem Glukose-Bereich über ein gleitendes Fenster (24 h / 7 d / 14 d)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, v, fenster: str, kategorie: str):
        super().__init__(v, None, f"tir_{fenster}_{kategorie}")
        self.fenster = fenster
        self.kategorie = kategorie
        self._attr_icon = "mdi:bullseye-arrow" if kategorie == "im_ziel" else "mdi:chart-bell-curve"

    def _werte(self) -> dict | None:
        return (self.v.modell.z.get("tir") or {}).get(self.fenster)

    @property
    def native_value(self):
        w = self._werte()
        return w.get(self.kategorie) if w else None

    @property
    def extra_state_attributes(self):
        tir = self.v.modell.z.get("tir") or {}
        w = self._werte() or {}
        attr = {"datenstand": tir.get("stand"), "grenzen_mg_dl": tir.get("grenzen")}
        if self.kategorie == "im_ziel":
            attr |= {k: w.get(k) for k in ("mittelwert", "gmi", "cv_prozent", "anzahl", "abdeckung_prozent")}
        return attr
