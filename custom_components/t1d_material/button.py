"""Schnell-Button: Wechsel jetzt (ohne Details) – für Dashboards."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.util import dt as dt_util

from .const import CGM, QUELLE_HA, TYPEN
from .entity import T1DEntity


async def async_setup_entry(hass, entry, add) -> None:
    v = entry.runtime_data
    add([WechselJetzt(v, typ) for typ in TYPEN])


class WechselJetzt(T1DEntity, ButtonEntity):
    _attr_icon = "mdi:swap-horizontal"

    def __init__(self, v, typ):
        super().__init__(v, typ, "wechsel")

    async def async_press(self) -> None:
        start = {"produkt": self.v.produkt} if self.typ == CGM else None
        jetzt = dt_util.now()
        await self.v.aendern(
            lambda m: m.wechsel(self.typ, jetzt, jetzt, start=start, quelle=QUELLE_HA,
                                merge_fenster=self.v.merge_fenster)
        )
