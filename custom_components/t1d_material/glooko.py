"""Schlanker async Glooko-Client (Web-Login, keine offizielle API).

Login-Ablauf nach github.com/spamsch/glooko-reader:
1. GET /users/sign_in → Redirect auf regionale URL
2. CSRF-Token aus HTML lesen
3. POST Login-Formular
4. Patienten-ID und API-Basis aus Dashboard-HTML lesen
5. API-Aufrufe mit Session-Cookie + Origin/Referer

Glooko-Zeitstempel tragen ein "Z", sind aber Ortszeit des Geräts.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone, tzinfo
from urllib.parse import urlparse

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

BASE = "https://my.glooko.com"
LOGIN_PATH = "/users/sign_in"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0"
SESSION_MAX_S = 50 * 60


class GlookoFehler(Exception):
    """Abruf fehlgeschlagen."""


class GlookoAuthFehler(GlookoFehler):
    """Anmeldung fehlgeschlagen (Zugangsdaten?)."""


class GlookoClient:
    def __init__(self, session: aiohttp.ClientSession, email: str, passwort: str):
        self._s = session
        self._email = email
        self._passwort = passwort
        self.patient: str | None = None
        self.api: str | None = None
        self.origin: str | None = None
        self._login_zeit = 0.0

    async def anmelden(self) -> None:
        try:
            r = await self._s.get(
                f"{BASE}{LOGIN_PATH}?id=login_form&locale=en-GB",
                allow_redirects=False, headers={"User-Agent": UA},
            )
            regional = r.headers.get("Location", f"{BASE}{LOGIN_PATH}")
            if not regional.startswith("http"):
                regional = BASE + regional
            r = await self._s.get(regional, headers={"User-Agent": UA})
            r.raise_for_status()
            html = await r.text()
            m = re.search(r'name="csrf-token" content="([^"]+)"', html)
            if not m:
                raise GlookoAuthFehler("CSRF-Token nicht gefunden")
            r = await self._s.post(
                regional,
                data={
                    "authenticity_token": m.group(1),
                    "user[email]": self._email,
                    "user[password]": self._passwort,
                    "commit": "Log In",
                },
                headers={"User-Agent": UA, "Referer": regional},
            )
            html = await r.text()
        except aiohttp.ClientError as e:
            raise GlookoFehler(f"Glooko nicht erreichbar: {e}") from e

        m = re.search(r'window\.patient\s*=\s*"([^"]+)"', html)
        if not m:
            raise GlookoAuthFehler("Anmeldung fehlgeschlagen – Zugangsdaten prüfen")
        self.patient = m.group(1)
        u = urlparse(str(r.url))
        a = re.search(r"apiUrl:\s*'([^']+)'", html)
        self.api = a.group(1) if a else f"{u.scheme}://{u.hostname.replace('my.glooko', 'api.glooko')}"
        self.origin = f"{u.scheme}://{u.hostname}"
        self._login_zeit = time.monotonic()
        _LOGGER.debug("Glooko angemeldet, API %s", self.api)

    async def _get(self, pfad_und_query: str, nochmal: bool = True) -> dict:
        if not self.patient or time.monotonic() - self._login_zeit > SESSION_MAX_S:
            await self.anmelden()
        url = URL(f"{self.api}{pfad_und_query}", encoded=True)  # series[] nicht kodieren
        try:
            r = await self._s.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": self.origin,
                    "Referer": f"{self.origin}/",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
        except aiohttp.ClientError as e:
            raise GlookoFehler(f"Glooko-Abruf fehlgeschlagen: {e}") from e
        if r.status == 401 and nochmal:
            self.patient = None
            return await self._get(pfad_und_query, nochmal=False)
        if r.status != 200:
            raise GlookoFehler(f"Glooko HTTP {r.status}")
        return await r.json(content_type=None)

    async def serien(self, start: datetime, ende: datetime, serien: list[str]) -> dict[str, list]:
        """Rohdaten der angegebenen Serien (Glooko-Format)."""
        f = "%Y-%m-%dT%H:%M:%S.000Z"
        if not self.patient:
            await self.anmelden()
        q = (
            f"/api/v3/graph/data?patient={self.patient}"
            f"&startDate={start.strftime(f)}&endDate={ende.strftime(f)}"
            "&locale=en-GB&insulinTooltips=true&filterBgReadings=true&splitByDay=false"
            + "".join(f"&series[]={s}" for s in serien)
        )
        # Patient-ID erst nach Login bekannt → Query nach evtl. Re-Login neu bauen
        daten = await self._get(q)
        return {s: (daten.get("series") or {}).get(s) or [] for s in serien}

    async def geraete(self) -> list[dict]:
        if not self.patient:
            await self.anmelden()
        d = await self._get(f"/api/v3/devices_and_settings?patient={self.patient}")
        return d.get("devices") or []


def glooko_zeit(ts: str, tz: tzinfo) -> datetime:
    """'2026-01-15T08:30:00.000Z' (eigentlich Ortszeit) → aware datetime in tz."""
    naiv = datetime.fromisoformat(ts.replace("Z", "")).replace(tzinfo=None)
    return naiv.replace(tzinfo=tz)


CGM_SERIEN = ["cgmHigh", "cgmNormal", "cgmLow"]


def cgm_werte(serien: dict[str, list], tz: tzinfo) -> list[tuple[datetime, int]]:
    out = []
    for name in CGM_SERIEN:
        for p in serien.get(name, []):
            ts, wert = p.get("timestamp"), p.get("y")
            if ts and wert is not None:
                out.append((glooko_zeit(ts, tz), int(wert)))
    out.sort()
    return out


INSULIN_SERIEN = ["scheduledBasal", "deliveredBolus"]


def insulin_daten(
    serien: dict[str, list], tz: tzinfo
) -> tuple[list[tuple[datetime, datetime, float]], list[tuple[datetime, float]], datetime | None]:
    """Basal-Segmente (Start, Ende, U/h), Boli (Zeit, U) und Datenstand (letzter Zeitpunkt).

    `scheduledBasal` enthält jedes Segment mehrfach → nach (Start, Ende) entdoppeln.
    Die Summe entspricht dann Glookos eigenem `scheduledBasalsSum`.
    CamAPS-Auto-Mode-Basal steckt in `scheduledBasal`; Boli (inkl. Korrektur) in `deliveredBolus`.
    """
    basal: dict[tuple[str, str], tuple[datetime, datetime, float]] = {}
    for p in serien.get("scheduledBasal", []):
        ts, te, rate = p.get("timestamp"), p.get("endTimestamp"), p.get("rate")
        if ts and te and rate is not None and (ts, te) not in basal:
            basal[(ts, te)] = (glooko_zeit(ts, tz), glooko_zeit(te, tz), float(rate))
    boli: list[tuple[datetime, float]] = []
    for p in serien.get("deliveredBolus", []):
        menge = p.get("insulinDelivered")
        if menge is None:
            menge = p.get("y")
        if menge is None:
            continue
        if p.get("timestamp"):
            t = glooko_zeit(p["timestamp"], tz)
        elif p.get("x") is not None:  # Sekunden; wie "timestamp" Ortszeit als UTC kodiert
            t = datetime.fromtimestamp(p["x"], timezone.utc).replace(tzinfo=tz)
        else:
            continue
        boli.append((t, float(menge)))
    segmente = sorted(basal.values())
    zeiten = [e for _, e, _ in segmente] + [t for t, _ in boli]
    return segmente, sorted(boli), (max(zeiten) if zeiten else None)
