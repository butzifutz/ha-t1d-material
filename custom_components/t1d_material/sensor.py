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
        ents.append(TirSensor(v))
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
    """Zeit im Zielbereich (%), exponentiell gewichtet über 14 Tage; die übrigen Bereiche als Attribute."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:bullseye-arrow"

    def __init__(self, v):
        super().__init__(v, None, "tir")

    @property
    def native_value(self):
        return (self.v.modell.z.get("tir") or {}).get("im_ziel")

    @property
    def extra_state_attributes(self):
        tir = self.v.modell.z.get("tir") or {}
        return {
            "stark_darunter": tir.get("sehr_tief"),
            "leicht_darunter": tir.get("tief"),
            "leicht_darueber": tir.get("hoch"),
            "stark_darueber": tir.get("sehr_hoch"),
            "datenstand": tir.get("stand"),
            "grenzen_mg_dl": tir.get("grenzen"),
        }
