"""Fachlogik ohne Home-Assistant-Abhängigkeit (testbar mit reinem pytest).

Der Zustand ist ein JSON-fähiges dict, damit er direkt im HA-Store liegt:

    {
      "version": 1,
      "einrichtung": iso,          # Bestand wird für Glooko-Events erst ab hier gebucht
      "zyklen": [ {...}, ... ],
      "bestand": {typ: int},
      "buchungen": [ {zeit, typ, delta, grund, zyklus_id}, ... ],
      "request_ids": {request_id: zyklus_id | None},
      "glooko": {"letzter_sync": iso|None, "fehler": str|None, "initialisiert": {typ: bool}},
      "insulin": {"zyklus_id", "verbrauch_u", "stand": iso, "tagesverbrauch_u"},  # aus Glooko
      "schlauch": [ {zeit, quelle, zyklus_id}, ... ],  # Schlauchfüllungen (App-Chip oder Glooko)
      "tir": {"stand": iso, "grenzen": [...], "im_ziel": %, "tief": %, ...}  # aus Glooko-CGM, gewichtet
    }
    Gelöschte Glooko-Wechsel stehen in glooko.geloescht ({typ, zeit}), damit der Abgleich sie nicht neu anlegt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .const import (
    AMPULLE,
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
    SCHLAUCH_FENSTER_MIN,
    SET,
    SETZSTELLE_ALT,
    STATUS_AKTIV,
    STATUS_KEIN,
    STATUS_TOLERANZ,
    STATUS_UEBERFAELLIG,
    TYPEN,
)

MAX_REQUEST_IDS = 1000
SCHLAUCH_FENSTER = timedelta(minutes=SCHLAUCH_FENSTER_MIN)
SCHLAUCH_AUFBEWAHREN = timedelta(days=120)
GELOESCHT_AUFBEWAHREN = timedelta(days=120)

# Felder, die beim Setzen eines Materials erfasst werden
START_FELDER = ("setzstelle", "seite", "charge", "seriennummer", "produkt", "foto", "schlauch_gewechselt")
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


@dataclass
class InsulinConfig:
    voll_u: float = 160.0      # Inhalt einer vollen Ampulle
    fuellen_u: float = 12.0    # Verlust beim Schlauchfüllen
    warnung_u: float = 20.0    # ab hier "bald wechseln" (CamAPS-Warnung)
    kanuele_u: float = 0.3     # Kanüle füllen bei jedem Setwechsel
    reservoir_u: float = 1.0   # Reservoirwechsel ohne Schlauch: Stempel anfahren

    @property
    def nutzbar_u(self) -> float:
        """Was eine neue Ampulle (mit Schlauchfüllen) tatsächlich liefert."""
        return max(self.voll_u - self.fuellen_u, 0.0)


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
        "insulin": {},
        "schlauch": [],
        "tir": {},
    }


def migrieren(z: dict[str, Any]) -> bool:
    """Ältere Zustände anheben. Gibt True zurück, wenn etwas geändert wurde."""
    geaendert = False
    if "insulin" not in z:
        z["insulin"] = {}
        geaendert = True
    if "tir" not in z:
        z["tir"] = {}
        geaendert = True
    if "schlauch" not in z:
        # v0.2: Feld schlauch_gefuellt am Reservoir-Zyklus (Standard: ja) → Liste der Schlauchfüllungen
        z["schlauch"] = []
        for c in z["zyklen"]:
            if c["typ"] == AMPULLE and c.pop("schlauch_gefuellt", True):
                z["schlauch"].append({"zeit": c["start"], "quelle": QUELLE_APP, "zyklus_id": c["id"]})
                c["schlauch_gewechselt"] = True
            c.pop("schlauch_gefuellt", None)
        geaendert = True
    for c in z["zyklen"]:
        alt = c.get("setzstelle")
        if alt in SETZSTELLE_ALT:
            c["setzstelle"] = SETZSTELLE_ALT[alt]
            geaendert = True
    return geaendert


class Modell:
    def __init__(
        self, zustand: dict[str, Any], config: dict[str, TypConfig], insulin: InsulinConfig | None = None
    ):
        self.z = zustand
        self.config = config
        self.insulin = insulin or InsulinConfig()
        migrieren(self.z)

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

    # ------------------------------------------------------- Schlauchfüllungen
    def schlauch_zeiten(self) -> list[datetime]:
        return sorted(parse(f["zeit"]) for f in self.z["schlauch"] if not f.get("aus"))

    def _schlauch_nahe(self, zeit: datetime, auch_aus: bool = False) -> list[dict]:
        return [
            f for f in self.z["schlauch"]
            if abs(parse(f["zeit"]) - zeit) <= SCHLAUCH_FENSTER and (auch_aus or not f.get("aus"))
        ]

    def mit_schlauch(self, c: dict) -> bool:
        """Wurde beim Start dieses Zyklus ein Schlauch gefüllt (App-Chip oder Glooko)?"""
        return c["typ"] in (SET, AMPULLE) and bool(self._schlauch_nahe(parse(c["start"])))

    def ansicht(self, c: dict | None) -> dict | None:
        """Zyklus für Ausgabe (App/Sensoren) inkl. berechnetem Schlauch-Flag."""
        if c is None:
            return None
        out = dict(c)
        if c["typ"] in (SET, AMPULLE):
            out["schlauch_gewechselt"] = self.mit_schlauch(c)
        return out

    def _schlauch_setzen(self, c: dict, an: bool, jetzt: datetime) -> None:
        nahe = self._schlauch_nahe(parse(c["start"]), auch_aus=True)
        if an:
            for f in nahe:
                f.pop("aus", None)
            if not nahe:
                self.z["schlauch"].append({"zeit": c["start"], "quelle": QUELLE_APP, "zyklus_id": c["id"]})
        else:
            for f in nahe:
                if f["quelle"] == QUELLE_APP:
                    self.z["schlauch"].remove(f)
                else:  # Glooko-Füllung merken, aber nicht zählen (sonst käme sie beim Abgleich wieder)
                    f["aus"] = True
        c["schlauch_gewechselt"] = an
        c["geaendert"] = iso(jetzt)

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
            return basis | leer | self._insulin_status(c, vorrat, jetzt)
        # Reichweite: tatsächlicher typischer Wechselabstand (ab 3 Wechseln), sonst Soll-Tragezeit
        median_h = self.typischer_abstand_h(typ)
        abstand_h = min(median_h, cfg.tragezeit_h + cfg.toleranz_h) if median_h else cfg.tragezeit_h
        reichweite_basis = "median" if median_h else "soll"
        if c is None:
            return basis | leer | {
                "status": STATUS_KEIN,
                "zyklus": None,
                "gesetzt_am": None,
                "abstand_h": abstand_h,
                "reichweite_basis": reichweite_basis,
                "reichweite_d": round(vorrat * abstand_h / 24, 1),
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
        # Aktuelles Material: Rest bis zum typischen Wechsel (nicht bis zum Soll-Ablauf)
        rest_typisch_h = (start + timedelta(hours=abstand_h) - jetzt).total_seconds() / 3600
        reichweite_h = vorrat * abstand_h + max(rest_typisch_h, 0)
        return basis | {
            "status": st,
            "zyklus": self.ansicht(c),
            "gesetzt_am": c["start"],
            "ablauf": iso(ablauf),
            "toleranz_ende": iso(tol_ende),
            "restzeit_h": round(rest_h, 2),
            "restzeit_prozent": round(prozent, 1),
            "abstand_h": abstand_h,
            "reichweite_basis": reichweite_basis,
            "reichweite_d": round(reichweite_h / 24, 1),
        }

    def _insulin_status(self, c: dict | None, vorrat: int, jetzt: datetime) -> dict[str, Any]:
        """Insulin: Füllstand aus Glooko-Verbrauch; ohne Daten Reichweite aus dem Wechselabstand."""
        ic = self.insulin
        info = self.z.get("insulin") or {}
        tages_u = info.get("tagesverbrauch_u")
        out: dict[str, Any] = {
            "status": STATUS_AKTIV if c else STATUS_KEIN,
            "zyklus": self.ansicht(c),
            "gesetzt_am": c["start"] if c else None,
            "fuellstand_u": None,
            "fuellstand_prozent": None,
            "insulin_stand": None,
            "tagesverbrauch_u": tages_u,
            "insulin_voll_u": ic.voll_u,
        }
        if c is not None and tages_u:
            # Abzüge seit dem Einsetzen: jede Schlauchfüllung, jedes Kanülenfüllen (Setwechsel)
            ab = parse(c["start"]) - SCHLAUCH_FENSTER
            n_schlauch = sum(1 for t in self.schlauch_zeiten() if ab <= t <= jetzt)
            n_kanuele = sum(1 for s in self.zyklen(SET) if ab <= parse(s["start"]) <= jetzt)
            # Ohne Schlauch beim Einsetzen kostet das Anfahren des Stempels etwas (mit Schlauch steckt es im Füllen)
            n_reservoir = 0 if self.mit_schlauch(c) else 1
            start_u = (ic.voll_u - n_reservoir * ic.reservoir_u
                       - n_schlauch * ic.fuellen_u - n_kanuele * ic.kanuele_u)
            if info.get("zyklus_id") == c["id"]:
                verbrauch = float(info.get("verbrauch_u") or 0.0)
                stand = info.get("stand")
            else:  # gerade gewechselt, Glooko hat noch nichts → voll
                verbrauch, stand = 0.0, None
            # Glooko hinkt ~90 min hinterher → ab Datenstand mit Ø-Verbrauch hochrechnen
            bezug = max(parse(stand), parse(c["start"])) if stand else parse(c["start"])
            seit_h = max((jetzt - bezug).total_seconds() / 3600, 0.0)
            fuell = max(start_u - verbrauch - tages_u * seit_h / 24, 0.0)
            rest_h = fuell / tages_u * 24
            prozent = round(min(100.0, fuell / ic.voll_u * 100), 1)
            out |= {
                "fuellstand_u": round(fuell, 1),
                "schlauch_fuellungen": n_schlauch,
                "kanuele_fuellungen": n_kanuele,
                "reservoir_fuellung_u": n_reservoir * ic.reservoir_u,
                "fuellstand_prozent": prozent,
                "insulin_stand": stand,
                "restzeit_h": round(rest_h, 1),
                "restzeit_prozent": prozent,
                "ablauf": iso(jetzt + timedelta(hours=rest_h)),
                "status": STATUS_TOLERANZ if fuell <= ic.warnung_u else STATUS_AKTIV,
                "reichweite_basis": "glooko",
                "reichweite_d": round((fuell + vorrat * ic.nutzbar_u) / tages_u, 1),
            }
            if fuell <= 0:
                out["status"] = STATUS_UEBERFAELLIG
            return out
        if tages_u:
            out |= {"reichweite_basis": "glooko", "reichweite_d": round(vorrat * ic.nutzbar_u / tages_u, 1)}
            return out
        abstand_h = self.typischer_abstand_h(AMPULLE)
        out |= {
            "abstand_h": abstand_h,
            "reichweite_basis": "median" if abstand_h else None,
            "reichweite_d": round(vorrat * abstand_h / 24, 1) if abstand_h else None,
        }
        return out

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
                    if start.get("schlauch_gewechselt"):
                        self._schlauch_setzen(c, True, jetzt)
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
        if start.get("schlauch_gewechselt") and typ in (SET, AMPULLE):
            self._schlauch_setzen(c, True, jetzt)

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
        daten = dict(daten)
        schlauch = daten.pop("schlauch_gewechselt", None)
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
        alt_start = c["start"]
        c.update(neu)
        c["geaendert"] = iso(jetzt)
        if c["start"] != alt_start:  # App-Schlauchfüllung wandert mit dem Start
            for f in self.z["schlauch"]:
                if f.get("zyklus_id") == c["id"] and f["quelle"] == QUELLE_APP:
                    f["zeit"] = c["start"]
        if schlauch is not None and c["typ"] in (SET, AMPULLE):
            self._schlauch_setzen(c, bool(schlauch), jetzt)
        if "ende" in daten and daten["ende"] is not None and c["typ"] == CGM:
            c["evidenz_status"] = "offen"
        return c

    def loeschen(self, zyklus_id: str, jetzt: datetime, merken: bool = True) -> None:
        """Zyklus löschen (z. B. Fehleingabe). Bestand wird zurückgebucht.

        Stammt der Zyklus (auch) aus Glooko, wird die Zeit gemerkt, damit der nächste Abgleich ihn nicht neu anlegt.
        """
        c = self.zyklus(zyklus_id)
        if merken and c["quelle"] in (QUELLE_GLOOKO, QUELLE_ZUSAMMEN):
            g = self.z["glooko"].setdefault("geloescht", [])
            g.append({"typ": c["typ"], "zeit": c["start"]})
            grenze = jetzt - GELOESCHT_AUFBEWAHREN
            g[:] = [x for x in g if parse(x["zeit"]) >= grenze]
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
        self.z["schlauch"] = [
            f for f in self.z["schlauch"] if not (f.get("zyklus_id") == c["id"] and f["quelle"] == QUELLE_APP)
        ]
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
        geloescht = [parse(x["zeit"]) for x in self.z["glooko"].get("geloescht", []) if x["typ"] == typ]
        geaendert = []
        for t in zeiten:
            if any(abs(t - g) <= max(dedupe, SCHLAUCH_FENSTER) for g in geloescht):
                continue  # vom Nutzer gelöscht
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

    def glooko_schlauch(
        self, zeiten: list[datetime], jetzt: datetime, reservoir: Iterable[datetime] = ()
    ) -> int:
        """„Schlauch füllen“ aus Glooko übernehmen. Gibt die Zahl neuer Füllungen zurück.

        `zeiten`: Füllungen mit Setwechsel daneben (= Schlauch gewechselt).
        `reservoir`: Füllungen ohne Setwechsel (= Reservoirwechsel ohne Schlauch). Bis v0.3 wurden auch sie als
        Schlauchfüllung gespeichert – solche Einträge werden hier entfernt.
        """
        for r in reservoir:
            for f in list(self.z["schlauch"]):
                if abs(parse(f["zeit"]) - r) > SCHLAUCH_FENSTER:
                    continue
                if f["quelle"] == QUELLE_GLOOKO:
                    self.z["schlauch"].remove(f)
                elif f["quelle"] == QUELLE_ZUSAMMEN:
                    f["quelle"] = QUELLE_APP  # Chip in der App gesetzt → bleibt
        neu = 0
        for t in dedupe_zeiten(zeiten, SCHLAUCH_FENSTER):
            nahe = self._schlauch_nahe(t, auch_aus=True)
            if nahe:
                for f in nahe:
                    if f["quelle"] == QUELLE_APP:
                        f["quelle"] = QUELLE_ZUSAMMEN
                continue
            self.z["schlauch"].append({"zeit": iso(t), "quelle": QUELLE_GLOOKO, "zyklus_id": None})
            neu += 1
        grenze = jetzt - SCHLAUCH_AUFBEWAHREN
        self.z["schlauch"] = [f for f in self.z["schlauch"] if parse(f["zeit"]) >= grenze]
        return neu

    def glooko_reservoir_zuruecknehmen(self, zeiten_mit_set: Iterable[datetime], jetzt: datetime) -> int:
        """Reservoir-Zyklen, die nur aus Glooko stammen, wieder entfernen, wenn sich die Füllung doch als
        Setwechsel mit Schlauch herausstellt (Kanüle füllen kam erst mit einem späteren Upload)."""
        n = 0
        for t in zeiten_mit_set:
            for c in self.zyklen(AMPULLE):
                if c["quelle"] == QUELLE_GLOOKO and abs(parse(c["start"]) - t) <= SCHLAUCH_FENSTER:
                    self.loeschen(c["id"], jetzt, merken=False)
                    n += 1
        return n

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


def insulin_summe(
    basal: list[tuple[datetime, datetime, float]], boli: list[tuple[datetime, float]],
    von: datetime, bis: datetime,
) -> float:
    """Abgegebenes Insulin in [von, bis): Basal anteilig (Rate U/h × Überlappung) + Boli."""
    u = 0.0
    for s, e, rate in basal:
        a, b = max(s, von), min(e, bis)
        if b > a:
            u += rate * (b - a).total_seconds() / 3600
    u += sum(menge for t, menge in boli if von <= t < bis)
    return u


def tir_statistik(
    werte: list[tuple[datetime, int]],
    bis: datetime,
    grenzen: tuple[float, float, float, float],
    halbwertszeit: timedelta = timedelta(days=7),
    schnitt: timedelta = timedelta(days=14),
) -> dict[str, Any] | None:
    """Anteile in den Glukose-Bereichen (Prozent), exponentiell gewichtet.

    Gewicht eines Werts = 0,5 ** (Alter / Halbwertszeit), Alter gemessen ab ``bis``;
    Werte älter als ``schnitt`` (oder nach ``bis``) zählen nicht.
    grenzen = (sehr_tief, tief, hoch, sehr_hoch) in mg/dl, z. B. (54, 70, 180, 250):
    sehr_tief < 54 ≤ tief < 70 ≤ im_ziel ≤ 180 < hoch ≤ 250 < sehr_hoch
    """
    sehr_tief, tief, hoch, sehr_hoch = grenzen
    hwz = halbwertszeit.total_seconds()
    summe = {"sehr_tief": 0.0, "tief": 0.0, "im_ziel": 0.0, "hoch": 0.0, "sehr_hoch": 0.0}
    gesamt = 0.0
    for t, w in werte:
        alter = (bis - t).total_seconds()
        if alter < 0 or alter > schnitt.total_seconds():
            continue
        g = 0.5 ** (alter / hwz)
        gesamt += g
        if w < sehr_tief:
            summe["sehr_tief"] += g
        elif w < tief:
            summe["tief"] += g
        elif w <= hoch:
            summe["im_ziel"] += g
        elif w <= sehr_hoch:
            summe["hoch"] += g
        else:
            summe["sehr_hoch"] += g
    if not gesamt:
        return None
    return {k: round(v / gesamt * 100, 1) for k, v in summe.items()}

