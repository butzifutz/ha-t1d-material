"""Gemeinsame Entitäts-Basis: ein Gerät je Materialtyp."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, TYP_NAMEN
from .verwaltung import Verwaltung


class T1DEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, v: Verwaltung, typ: str | None, key: str):
        self.v = v
        self.typ = typ
        geraet = typ or "glooko"
        self._attr_unique_id = f"{v.entry.entry_id}_{geraet}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{v.entry.entry_id}_{geraet}")},
            name=TYP_NAMEN.get(typ, "Glooko"),
            manufacturer="T1D-Material",
            model=TYP_NAMEN.get(typ, "Glooko-Abgleich"),
        )

    @property
    def status(self) -> dict:
        return self.v.modell.status(self.typ, dt_util.now())

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.v.anmelden(self.async_write_ha_state))
