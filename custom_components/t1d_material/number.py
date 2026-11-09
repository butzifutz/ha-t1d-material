"""Bestand je Materialtyp (manuell korrigierbar)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.util import dt as dt_util

from .const import TYPEN
from .entity import T1DEntity


async def async_setup_entry(hass, entry, add) -> None:
    v = entry.runtime_data
    add([Bestand(v, typ) for typ in TYPEN])


class Bestand(T1DEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 999
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:package-variant"
    _attr_native_unit_of_measurement = "Stk."

    def __init__(self, v, typ):
        super().__init__(v, typ, "bestand")

    @property
    def native_value(self) -> int:
        return self.v.modell.z["bestand"].get(self.typ, 0)

    async def async_set_native_value(self, value: float) -> None:
        await self.v.aendern(lambda m: m.bestand_setzen(self.typ, int(value), dt_util.now()))
