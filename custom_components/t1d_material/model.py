"""Fachlogik ohne Home-Assistant-Abhängigkeit (testbar mit reinem pytest).

Der Zustand ist ein JSON-fähiges dict, damit er direkt im HA-Store liegt:

    {
      "version": 1,
      "einrichtung": iso,          # Bestand wird für Glooko-Events erst ab hier gebucht
      "zyklen": [ {...}, ... ],
      "bestand": {typ: int},
      "buchungen": [ {zeit, typ, delta, grund, zyklus_id}, ... ],
      "request_ids": {request_id: zyklus_id | None},
      "glooko": {"letzter_sync": iso|None, "fehler": str|None, "initialisiert": {typ: bool}}
    }
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .const import (
    CGM,
    ENDGRUND_REGULAER,
    ENDGRUND_VORZEITIG,
    LUECKE_1_BIS_3,
    LUECKE_KEINE,
    LUECKE_UEBER_3,
    LUECKE_UNTER_1,
    QUELLE_APP,
    QUELLE_GLOOKO,
    QUELLE_ZUSAMMEN,
    STATUS_AKTIV,
    STATUS_KEIN,
    STATUS_TOLERANZ,
    STATUS_UEBERFAELLIG,
    TYPEN,
)

MAX_REQUEST_IDS = 1000

# Felder, die beim Setzen eines Materials erfasst werden
START_FELDER = ("setzstelle", "seite", "charge", "seriennummer", "produkt", "foto")
# Felder, die das Ende eines Zyklus beschreiben (Reklamation)
ENDE_FELDER = (
    "endgrund",
    "problemart",
    "problem_zeit",
    "problem_text",
    "ruecksendung",
)
EDITIERBAR = ("start", "ende") + START_FELDER + ENDE_FELDER


class ModellFehler(ValueError):
    """Ungültige Eingabe (wird als ServiceValidationError gemeldet)."""


@dataclass
class TypConfig:
    tragezeit_h: float | None  # None = kein Ablauf (Ampulle)
    toleranz_h: float
    packung: int


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ModellFehler(f"Zeit ohne Zeitzone: {s}")
    return dt


def leerer_zustand(jetzt: datetime) -> dict[str, Any]:
    return {
        "version": 1,
        "einrichtung": iso(jetzt),
        "zyklen": [],
        "bestand": {t: 0 for t in TYPEN},
        "buchungen": [],
        "request_ids": {},
        "glooko": {"letzter_sync": None, "fehler": None, "initialisiert": {}},
    }


class Modell:
    def __init__(self, zustand: dict[str, Any], config: dict[str, TypConfig]):
        self.z = zustand
        self.config = config

    # ------------------------------------------------------------------ lesen
    def zyklen(self, typ: str) -> list[dict]:
        return sorted(
            (c for c in self.z["zyklen"] if c["typ"] == typ), key=lambda c: parse(c["start"])
        )

    def zyklus(self, zyklus_id: str) -> dict:
        for c in self.z["zyklen"]:
            if c["id"] == zyklus_id:
                return c
        raise ModellFehler(f"Zyklus {zyklus_id} nicht gefunden")

    def aktiv(self, typ: str) -> dict | None:
        offen = [c for c in self.zyklen(typ) if c["ende"] is None]
        return offen[-1] if offen else None

    def status(self, typ: str, jetzt: datetime) -> dict[str, Any]:
        cfg = self.config[typ]
        c = self.aktiv(typ)
        bestand = self.z["bestand"].get(typ, 0)
        vorrat = max(bestand, 0)  # negativer Bestand = vergessene Buchung, keine negative Reichweite
        basis = {
            "typ": typ,
            "bestand": bestand,
            "tragezeit_h": cfg.tragezeit_h,
            "toleranz_h": cfg.toleranz_h,
        }
        leer = {
            "ablauf": None,
            "toleranz_ende": None,
            "restzeit_h": None,
            "restzeit_prozent": None,
        }
        if cfg.tragezeit_h is None:
            # Bestandstyp: Reichweite aus dem typischen Wechselabstand
            abstand_h = self.typischer_abstand_h(typ)
            return basis | leer | {
                "status": STATUS_AKTIV if c else STATUS_KEIN,
                "zyklus": c,
                "gesetzt_am": c["start"] if c else None,
                "abstand_h": abstand_h,
                "reichweite_d": round(vorrat * abstand_h / 24, 1) if abstand_h else None,
            }
        if c is None:
            return basis | leer | {
                "status": STATUS_KEIN,
                "zyklus": None,
                "gesetzt_am": None,
                "reichweite_d": round(vorrat * cfg.tragezeit_h / 24, 1),
            }
        start = parse(c["start"])
        ablauf = start + timedelta(hours=cfg.tragezeit_h)
        tol_ende = ablauf + timedelta(hours=cfg.toleranz_h)
        rest_h = (ablauf - jetzt).total_seconds() / 3600
        if jetzt < ablauf:
            st = STATUS_AKTIV
        elif jetzt < tol_ende:
            st = STATUS_TOLERANZ
        else:
            st = STATUS_UEBERFAELLIG
        prozent = max(0.0, min(100.0, rest_h / cfg.tragezeit_h * 100)) if cfg.tragezeit_h else 0.0
        reichweite_h = vorrat * cfg.tragezeit_h + max(rest_h, 0)
        return basis | {
            "status": st,
            "zyklus": c,
            "gesetzt_am": c["start"],
            "ablauf": iso(ablauf),
            "toleranz_ende": iso(tol_ende),
            "restzeit_h": round(rest_h, 2),
            "restzeit_prozent": round(prozent, 1),
            "reichweite_d": round(reichweite_h / 24, 1),
        }

    def typischer_abstand_h(self, typ: str, anzahl: int = 10) -> float | None:
        """Median der letzten abgeschlossenen Zyklusdauern (mind. 3), robust gegen Ausreißer."""
        dauern = sorted(
            (parse(c["ende"]) - parse(c["start"])).total_seconds() / 3600
            for c in self.zyklen(typ)[-anzahl - 1:]
            if c["ende"]
        )
        if len(dauern) < 3:
            return None
        mitte = len(dauern) // 2
        median = dauern[mitte] if len(dauern) % 2 else (dauern[mitte - 1] + dauern[mitte]) / 2
        return round(median, 1)

    # -------------------------------------------------------------- schreiben
    def _buchen(self, typ: str, delta: int, grund: str, zeit: datetime, zyklus_id: str | None):
        self.z["bestand"][typ] = self.z["bestand"].get(typ, 0) + delta
        self.z["buchungen"].append(
            {"zeit": iso(zeit), "typ": typ, "delta": delta, "grund": grund, "zyklus_id": zyklus_id}
        )

    def _merke_request(self, request_id: str | None, zyklus_id: str | None):
        if not request_id:
            return
        r = self.z["request_ids"]
        r[request_id] = zyklus_id
        while len(r) > MAX_REQUEST_IDS:
            r.pop(next(iter(r)))

    def bekannte_anfrage(self, request_id: str | None) -> bool:
        return bool(request_id) and request_id in self.z["request_ids"]

    @staticmethod
    def _pruefe_typ(typ: str):
        if typ not in TYPEN:
            raise ModellFehler(f"Unbekannter Materialtyp: {typ}")

    @staticmethod
    def _uebernimm(ziel: dict, daten: dict, felder: Iterable[str]):
        for f in felder:
            if f in daten and daten[f] is not None:
                ziel[f] = daten[f]

    def _beende(self, c: dict, zeit: datetime, ende: dict | None):
        c["ende"] = iso(zeit)
        ende = dict(ende or {})
        if not ende.get("endgrund"):
            ende["endgrund"] = (
                ENDGRUND_VORZEITIG if ende.get("problemart") else ENDGRUND_REGULAER
            )
        self._uebernimm(c, ende, ENDE_FELDER)
        if c["typ"] == CGM:
            c["evidenz_status"] = "offen"

    def wechsel(
        self,
        typ: str,
        zeit: datetime,
        jetzt: datetime,
        start: dict | None = None,
        ende: dict | None = None,
        quelle: str = QUELLE_APP,
        request_id: str | None = None,
        merge_fenster: timedelta = timedelta(hours=2),
    ) -> dict:
        """Neues Material gesetzt. Beendet den Zyklus, der zu `zeit` lief."""
        self._pruefe_typ(typ)
        if self.bekannte_anfrage(request_id):
            zid = self.z["request_ids"][request_id]
            return self.zyklus(zid) if zid else {}
        if zeit > jetzt + timedelta(minutes=5):
            raise ModellFehler("Wechselzeit liegt in der Zukunft")
        start = start or {}

        # Zusammenführen: App-Bestätigung zu einem schon von Glooko erkannten Wechsel
        if quelle != QUELLE_GLOOKO:
            for c in self.zyklen(typ):
                if c["quelle"] == QUELLE_GLOOKO and abs(parse(c["start"]) - zeit) <= merge_fenster:
                    self._uebernimm(c, start, START_FELDER)
                    c["quelle"] = QUELLE_ZUSAMMEN
                    c["geaendert"] = iso(jetzt)
                    vorher = self._vorgaenger(typ, parse(c["start"]))
                    if vorher is not None and ende:
                        self._uebernimm(vorher, ende, ENDE_FELDER)
                        if ende.get("problemart") and not ende.get("endgrund"):
                            vorher["endgrund"] = ENDGRUND_VORZEITIG
                    self._merke_request(request_id, c["id"])
                    return c

        vorher = self._vorgaenger(typ, zeit)
        nachher = self._nachfolger(typ, zeit)
        if vorher is not None and (vorher["ende"] is None or parse(vorher["ende"]) > zeit):
            self._beende(vorher, zeit, ende)

        c = {
            "id": uuid.uuid4().hex,
            "typ": typ,
            "start": iso(zeit),
            "ende": nachher["start"] if nachher else None,
            "quelle": quelle,
            "erstellt": iso(jetzt),
            "geaendert": iso(jetzt),
            "gebucht": False,
        }
        self._uebernimm(c, start, START_FELDER)
        self.z["zyklen"].append(c)

        einrichtung = parse(self.z["einrichtung"])
        if quelle != QUELLE_GLOOKO or zeit >= einrichtung:
            self._buchen(typ, -1, "wechsel", jetzt, c["id"])
            c["gebucht"] = True
        self._merke_request(request_id, c["id"])
        return c

    def _vorgaenger(self, typ: str, zeit: datetime) -> dict | None:
        vor = [c for c in self.zyklen(typ) if parse(c["start"]) < zeit]
        return vor[-1] if vor else None

    def _nachfolger(self, typ: str, zeit: datetime) -> dict | None:
        nach = [c for c in self.zyklen(typ) if parse(c["start"]) > zeit]
        return nach[0] if nach else None

    def beenden(
        self, typ: str, zeit: datetime, jetzt: datetime, ende: dict | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Laufenden Zyklus ohne Nachfolger beenden."""
        self._pruefe_typ(typ)
        if self.bekannte_anfrage(request_id):
            return self.zyklus(self.z["request_ids"][request_id])
        c = self.aktiv(typ)
        if c is None:
            raise ModellFehler("Kein laufender Zyklus")
        if zeit < parse(c["start"]):
            raise ModellFehler("Ende liegt vor dem Start")
        self._beende(c, zeit, ende)
        c["geaendert"] = iso(jetzt)
        self._merke_request(request_id, c["id"])
        return c

    def bearbeiten(self, zyklus_id: str, daten: dict, jetzt: datetime) -> dict:
        c = self.zyklus(zyklus_id)
        unbekannt = set(daten) - set(EDITIERBAR)
        if unbekannt:
            raise ModellFehler(f"Nicht editierbar: {', '.join(sorted(unbekannt))}")
        neu = dict(c)
        for k, v in daten.items():
            neu[k] = v
        s, e = parse(neu["start"]), parse(neu.get("ende"))
        if e is not None and e <= s:
            raise ModellFehler("Ende muss nach dem Start liegen")
        for andere in self.zyklen(c["typ"]):
            if andere["id"] == c["id"]:
                continue
            a_s, a_e = parse(andere["start"]), parse(andere["ende"])
            if _ueberlappt(s, e, a_s, a_e):
                raise ModellFehler("Zeitraum überschneidet sich mit einem anderen Zyklus")
        c.update(neu)
        c["geaendert"] = iso(jetzt)
        if "ende" in daten and daten["ende"] is not None and c["typ"] == CGM:
            c["evidenz_status"] = "offen"
        return c

    def loeschen(self, zyklus_id: str, jetzt: datetime) -> None:
        """Zyklus löschen (z. B. Fehleingabe). Bestand wird zurückgebucht."""
        c = self.zyklus(zyklus_id)
        typ = c["typ"]
        vorher = self._vorgaenger(typ, parse(c["start"]))
        if vorher is not None and vorher["ende"] == c["start"]:
            # Vorgänger läuft weiter bis zum Ende des gelöschten Zyklus (bzw. wieder offen)
            vorher["ende"] = c["ende"]
            if c["ende"] is None:
                for f in ENDE_FELDER:
                    vorher.pop(f, None)
                vorher.pop("evidenz_status", None)
            vorher["geaendert"] = iso(jetzt)
        self.z["zyklen"].remove(c)
        if c.get("gebucht"):
            self._buchen(typ, +1, "loeschen", jetzt, c["id"])

    def bestand_setzen(self, typ: str, anzahl: int, jetzt: datetime) -> int:
        self._pruefe_typ(typ)
        if anzahl < 0:
            raise ModellFehler("Bestand kann nicht negativ sein")
        delta = anzahl - self.z["bestand"].get(typ, 0)
        if delta:
            self._buchen(typ, delta, "korrektur", jetzt, None)
        return self.z["bestand"][typ]

    def packung_einbuchen(self, typ: str, packungen: int, jetzt: datetime) -> int:
        self._pruefe_typ(typ)
        if packungen < 1:
            raise ModellFehler("Mindestens eine Packung")
        self._buchen(typ, packungen * self.config[typ].packung, "packung", jetzt, None)
        return self.z["bestand"][typ]

    # ----------------------------------------------------------------- Glooko
    def glooko_events(
        self,
        typ: str,
        zeiten: list[datetime],
        jetzt: datetime,
        dedupe: timedelta,
        merge_fenster: timedelta,
    ) -> list[dict]:
        """Wechsel-Events aus Glooko übernehmen. Gibt neu angelegte/geänderte Zyklen zurück."""
        zeiten = dedupe_zeiten(zeiten, dedupe)
        init = self.z["glooko"]["initialisiert"]
        if not init.get(typ):
            # Erster Abruf: nur das jüngste Event (laufendes Material), keine Historie
            zeiten = zeiten[-1:]
            init[typ] = True
        geaendert = []
        for t in zeiten:
            treffer = [
                c for c in self.zyklen(typ) if abs(parse(c["start"]) - t) <= max(merge_fenster, dedupe)
            ]
            if treffer:
                c = min(treffer, key=lambda c: abs(parse(c["start"]) - t))
                if c["quelle"] not in (QUELLE_GLOOKO, QUELLE_ZUSAMMEN):
                    alt = c["start"]
                    neu_start = iso(t)
                    vorher = self._vorgaenger(typ, parse(alt))
                    if vorher is not None and vorher["ende"] == alt:
                        vorher["ende"] = neu_start
                    c["start"] = neu_start
                    c["quelle"] = QUELLE_ZUSAMMEN
                    c["geaendert"] = iso(jetzt)
                    geaendert.append(c)
                continue
            # Ältere Events vor dem ersten bekannten Zyklus sind Historie – ignorieren
            vorhandene = self.zyklen(typ)
            if vorhandene and t < parse(vorhandene[0]["start"]) and init.get(typ):
                continue
            c = self.wechsel(typ, t, jetzt, quelle=QUELLE_GLOOKO, merge_fenster=merge_fenster)
            geaendert.append(c)
        return geaendert

    def offene_evidenz(self) -> list[dict]:
        return [
            c for c in self.z["zyklen"]
            if c["typ"] == CGM and c.get("ende") and c.get("evidenz_status") == "offen"
        ]


def _ueberlappt(s1, e1, s2, e2) -> bool:
    far = datetime.max.replace(tzinfo=s1.tzinfo)
    return s1 < (e2 or far) and s2 < (e1 or far)


def dedupe_zeiten(zeiten: list[datetime], fenster: timedelta) -> list[datetime]:
    """Events, die näher als `fenster` am vorherigen liegen, zählen als eines (das erste)."""
    out: list[datetime] = []
    for t in sorted(zeiten):
        if out and t - out[-1] < fenster:
            continue
        out.append(t)
    return out


def groesste_luecke_min(
    messzeiten: list[datetime], start: datetime, ende: datetime, normal_min: float = 10
) -> float | None:
    """Längste Zeitspanne ohne Messwerte in der Sitzung.

    Zählt ab dem ersten Messwert (Warm-up wird nicht gewertet) bis zum Ende des Zyklus.
    Abstände bis `normal_min` (regulärer 5-min-Takt + Jitter) gelten nicht als Lücke.
    """
    t = sorted(x for x in messzeiten if start <= x <= ende)
    if not t:
        return None
    luecken = [(b - a).total_seconds() / 60 for a, b in zip(t, t[1:])]
    luecken.append((ende - t[-1]).total_seconds() / 60)
    groesste = max(luecken)
    return round(groesste, 1) if groesste > normal_min else 0.0


def luecke_stufe(minuten: float | None) -> str:
    if minuten is None:
        return LUECKE_KEINE
    if minuten < 60:
        return LUECKE_UNTER_1
    if minuten <= 180:
        return LUECKE_1_BIS_3
    return LUECKE_UEBER_3
