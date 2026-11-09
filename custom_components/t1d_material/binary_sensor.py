"""Glooko-Verbindung (Problem = letzter Abruf fehlgeschlagen)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import T1DEntity


async def async_setup_entry(hass, entry, add) -> None:
    v = entry.runtime_data
    if v.glooko_aktiv:
        add([GlookoVerbindung(v)])


class GlookoVerbindung(T1DEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, v):
        super().__init__(v, None, "glooko_verbindung")

    @property
    def is_on(self) -> bool | None:
        g = self.v.modell.z["glooko"]
        if g["letzter_sync"] is None and g["fehler"] is None:
            return None
        return g["fehler"] is None
