"""T1D-Material: Infusionssets, CGM-Sensoren und Insulinampullen verwalten."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_INTERVALL_MIN, DOMAIN
from .http import registrieren as http_registrieren
from .services import registrieren as services_registrieren
from .verwaltung import Verwaltung

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON, Platform.BINARY_SENSOR]

type T1DConfigEntry = ConfigEntry[Verwaltung]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    services_registrieren(hass)
    http_registrieren(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: T1DConfigEntry) -> bool:
    v = Verwaltung(hass, entry)
    await v.async_laden()
    entry.runtime_data = v

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Restzeiten jede Minute neu berechnen
    @callback
    def _minute(_now) -> None:
        v.benachrichtigen()

    entry.async_on_unload(async_track_time_interval(hass, _minute, timedelta(minutes=1)))
    if v.glooko_aktiv:
        intervall = timedelta(minutes=float(v.cfg[CONF_INTERVALL_MIN]))

        async def _abruf(_now=None):
            await v.glooko_abgleich()

        entry.async_on_unload(async_track_time_interval(hass, _abruf, intervall))
        entry.async_create_background_task(hass, _abruf(), f"{DOMAIN}_glooko_start")

    entry.async_on_unload(entry.add_update_listener(_neu_laden))
    return True


async def _neu_laden(hass: HomeAssistant, entry: T1DConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: T1DConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def verwaltung(hass: HomeAssistant) -> Verwaltung:
    """Die (einzige) geladene Verwaltung – für Services und HTTP-Views."""
    from homeassistant.exceptions import ServiceValidationError

    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data
    raise ServiceValidationError("T1D-Material ist nicht eingerichtet")
