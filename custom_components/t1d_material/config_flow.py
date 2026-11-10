"""Einrichtung (Glooko optional) und Optionen (Tragezeiten, Toleranzen, Packungen)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import selector as sel

from .const import (
    CGM,
    CONF_DEDUPE_MIN,
    CONF_EMAIL,
    CONF_EVIDENZ_H,
    CONF_INSULIN_FUELLEN_U,
    CONF_INSULIN_VOLL_U,
    CONF_INSULIN_WARNUNG_U,
    CONF_INTERVALL_MIN,
    CONF_MERGE_H,
    CONF_PACKUNG,
    CONF_PASSWORD,
    CONF_PRODUKT,
    CONF_TOLERANZ_H,
    CONF_TRAGEZEIT_D,
    DOMAIN,
    MIT_TRAGEZEIT,
    PRODUKTE,
    TYPEN,
)
from .glooko import GlookoAuthFehler, GlookoClient, GlookoFehler
from .verwaltung import typ_key, wirksame_config


async def _glooko_pruefen(hass, email: str, passwort: str) -> str | None:
    # eigene Cookie-Session je Prüfung; HA räumt sie beim Beenden auf
    session = async_create_clientsession(hass)
    try:
        await GlookoClient(session, email, passwort).anmelden()
    except GlookoAuthFehler:
        return "invalid_auth"
    except GlookoFehler:
        return "cannot_connect"
    return None


def _zahl(minimum, maximum, schritt, einheit=None):
    return sel.NumberSelector(sel.NumberSelectorConfig(
        min=minimum, max=maximum, step=schritt, mode=sel.NumberSelectorMode.BOX,
        unit_of_measurement=einheit,
    ))


def _glooko_schema(werte: dict) -> dict:
    return {
        vol.Optional(CONF_EMAIL, description={"suggested_value": werte.get(CONF_EMAIL)}): sel.TextSelector(
            sel.TextSelectorConfig(type=sel.TextSelectorType.EMAIL)
        ),
        vol.Optional(CONF_PASSWORD, description={"suggested_value": werte.get(CONF_PASSWORD)}): sel.TextSelector(
            sel.TextSelectorConfig(type=sel.TextSelectorType.PASSWORD)
        ),
    }


class T1DConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        fehler: dict[str, str] = {}
        if user_input is not None:
            email, pw = user_input.get(CONF_EMAIL), user_input.get(CONF_PASSWORD)
            if bool(email) != bool(pw):
                fehler["base"] = "glooko_unvollstaendig"
            elif email and (f := await _glooko_pruefen(self.hass, email, pw)):
                fehler["base"] = f
            else:
                return self.async_create_entry(title="T1D-Material", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_glooko_schema(user_input or {})), errors=fehler
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return T1DOptionsFlow()


class T1DOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        cfg = wirksame_config(self.config_entry)
        fehler: dict[str, str] = {}
        if user_input is not None:
            email, pw = user_input.get(CONF_EMAIL), user_input.get(CONF_PASSWORD)
            if bool(email) != bool(pw):
                fehler["base"] = "glooko_unvollstaendig"
            elif email and (email, pw) != (cfg.get(CONF_EMAIL), cfg.get(CONF_PASSWORD)) and (
                f := await _glooko_pruefen(self.hass, email, pw)
            ):
                fehler["base"] = f
            else:
                user_input.setdefault(CONF_EMAIL, "")
                user_input.setdefault(CONF_PASSWORD, "")
                return self.async_create_entry(data=user_input)
            cfg.update(user_input)

        schema: dict = {}
        for typ in TYPEN:
            if typ in MIT_TRAGEZEIT:
                schema[vol.Required(typ_key(typ, CONF_TRAGEZEIT_D), default=cfg[typ_key(typ, CONF_TRAGEZEIT_D)])] = _zahl(0.5, 30, 0.5, "d")
                schema[vol.Required(typ_key(typ, CONF_TOLERANZ_H), default=cfg[typ_key(typ, CONF_TOLERANZ_H)])] = _zahl(0, 72, 1, "h")
            schema[vol.Required(typ_key(typ, CONF_PACKUNG), default=cfg[typ_key(typ, CONF_PACKUNG)])] = _zahl(1, 100, 1, "Stk.")
        schema[vol.Required(typ_key(CGM, CONF_PRODUKT), default=cfg[typ_key(CGM, CONF_PRODUKT)])] = sel.SelectSelector(
            sel.SelectSelectorConfig(options=PRODUKTE)
        )
        schema[vol.Required(CONF_INSULIN_VOLL_U, default=cfg[CONF_INSULIN_VOLL_U])] = _zahl(10, 1000, 1, "U")
        schema[vol.Required(CONF_INSULIN_FUELLEN_U, default=cfg[CONF_INSULIN_FUELLEN_U])] = _zahl(0, 100, 0.5, "U")
        schema[vol.Required(CONF_INSULIN_WARNUNG_U, default=cfg[CONF_INSULIN_WARNUNG_U])] = _zahl(0, 200, 1, "U")
        schema[vol.Required(CONF_INTERVALL_MIN, default=cfg[CONF_INTERVALL_MIN])] = _zahl(10, 1440, 5, "min")
        schema[vol.Required(CONF_MERGE_H, default=cfg[CONF_MERGE_H])] = _zahl(0.25, 24, 0.25, "h")
        schema[vol.Required(CONF_DEDUPE_MIN, default=cfg[CONF_DEDUPE_MIN])] = _zahl(1, 240, 1, "min")
        schema[vol.Required(CONF_EVIDENZ_H, default=cfg[CONF_EVIDENZ_H])] = _zahl(1, 72, 1, "h")
        schema.update(_glooko_schema(cfg))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=fehler)
