"""Konstanten der T1D-Material-Integration."""

from __future__ import annotations

DOMAIN = "t1d_material"
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1
DATA_DIR = "t1d_material"  # unter /config, NICHT www

# Materialtypen
SET = "set"
CGM = "cgm"
AMPULLE = "ampulle"
TYPEN = (SET, CGM, AMPULLE)
MIT_TRAGEZEIT = (SET, CGM)

TYP_NAMEN = {
    SET: "Infusionsset",
    CGM: "CGM-Sensor",
    AMPULLE: "Insulin",
}
# Bezeichnung im Bestand und auf dem Wechsel-Button (bewusst je Zweck unterschiedlich)
BESTAND_NAMEN = {SET: "Infusionssets", CGM: "Sensoren", AMPULLE: "Pump Carts"}
WECHSEL_NAMEN = {SET: "Set wechseln", CGM: "Sensor wechseln", AMPULLE: "Reservoir wechseln"}

# Glooko-Serien (CamAPS FX, geprüft 2026-11). Glooko liefert nur Art + Zeit, keine Füllmenge, keinen Rücklauf:
# - setSiteChange   = „Kanüle füllen“  → Setwechsel
# - reservoirChange = „Schlauch füllen“ (auch das kurze Füllen nach Reservoirwechsel, Stempel anfahren)
#   · zusammen mit setSiteChange (±15 min) → Setwechsel mit Schlauch (Schlauchfüllung)
#   · allein → (sehr wahrscheinlich) Reservoirwechsel ohne Schlauch – nur wenn Reservoir-Erkennung an
# CGM-Wechsel liefert Glooko nicht → nur per App.
GLOOKO_SERIE = {SET: "setSiteChange"}
GLOOKO_SCHLAUCH = "reservoirChange"
SCHLAUCH_FENSTER_MIN = 15  # Schlauchfüllung gehört zu einem Wechsel, wenn so nah am Start

# Config-Schlüssel
CONF_EMAIL = "glooko_email"
CONF_PASSWORD = "glooko_password"
CONF_INTERVALL_MIN = "glooko_intervall_min"
CONF_MERGE_H = "merge_fenster_h"
CONF_DEDUPE_MIN = "dedupe_min"
CONF_TRAGEZEIT_D = "tragezeit_d"
CONF_TOLERANZ_H = "toleranz_h"
CONF_PACKUNG = "packung"
CONF_PRODUKT = "produkt"
CONF_INSULIN_VOLL_U = "insulin_voll_u"
CONF_INSULIN_FUELLEN_U = "insulin_fuellen_u"
CONF_INSULIN_WARNUNG_U = "insulin_warnung_u"
CONF_INSULIN_KANUELE_U = "insulin_kanuele_u"
CONF_INSULIN_RESERVOIR_U = "insulin_reservoir_u"
CONF_RESERVOIR_ERKENNEN = "reservoir_erkennen"
CONF_TIR_SEHR_TIEF = "tir_sehr_tief"
CONF_TIR_TIEF = "tir_tief"
CONF_TIR_HOCH = "tir_hoch"
CONF_TIR_SEHR_HOCH = "tir_sehr_hoch"

DEFAULTS_GLOBAL = {
    CONF_INTERVALL_MIN: 60,
    CONF_MERGE_H: 2.0,
    CONF_DEDUPE_MIN: 30,
    CONF_INSULIN_VOLL_U: 160.0,     # volle Ampulle
    CONF_INSULIN_FUELLEN_U: 12.0,   # Schlauch füllen
    CONF_INSULIN_WARNUNG_U: 20.0,   # CamAPS FX warnt hier
    CONF_INSULIN_KANUELE_U: 0.3,    # Kanüle füllen (jeder Setwechsel)
    CONF_INSULIN_RESERVOIR_U: 1.0,  # Reservoirwechsel ohne Schlauch: Stempel anfahren
    CONF_RESERVOIR_ERKENNEN: True,  # Reservoirwechsel aus Glooko ableiten (Annahme, s. o.)
    # Glukose-Bereiche in mg/dl (internationaler Konsens): <54 | 54–69 | 70–180 | 181–250 | >250
    CONF_TIR_SEHR_TIEF: 54,
    CONF_TIR_TIEF: 70,
    CONF_TIR_HOCH: 180,
    CONF_TIR_SEHR_HOCH: 250,
}

# Zeit im Zielbereich: exponentiell gewichtet (Halbwertszeit 7 Tage), Werte älter als 14 Tage zählen nicht
TIR_HALBWERTSZEIT_TAGE = 7
TIR_SCHNITT_TAGE = 14


# Standard-Packungsgrößen (in den Optionen änderbar).
# Ampulle hat keine Tragezeit; Füllstand wird (mit Glooko) aus Basal + Bolus berechnet.
DEFAULTS_TYP = {
    SET: {CONF_TRAGEZEIT_D: 3.0, CONF_TOLERANZ_H: 12.0, CONF_PACKUNG: 10},
    CGM: {CONF_TRAGEZEIT_D: 10.0, CONF_TOLERANZ_H: 12.0, CONF_PACKUNG: 1, CONF_PRODUKT: "G7"},
    AMPULLE: {CONF_PACKUNG: 25},
}

# Quellen
QUELLE_APP = "app"
QUELLE_GLOOKO = "glooko"
QUELLE_ZUSAMMEN = "zusammengefuehrt"
QUELLE_HA = "ha"

# Status
STATUS_AKTIV = "aktiv"
STATUS_TOLERANZ = "toleranz"
STATUS_UEBERFAELLIG = "ueberfaellig"
STATUS_KEIN = "kein_zyklus"

ENDGRUND_REGULAER = "regulaer"
ENDGRUND_VORZEITIG = "vorzeitig"

# Dexcom-Formular (Stand 2026-11), Texte 1:1
PRODUKTE = ["G6", "G7", "Flex"]
PROBLEMARTEN_CGM = [
    "Kopplung des Sensors fehlgeschlagen",
    "Kurzes Sensorproblem oder Sensorfehler",
    "Ungenaue Messwerte",
    "Signalverlust",
    "Blutung oder Bluterguss",
    "Problem beim Anbringen des Sensors",
    "Sensor ausgefallen",
    "Sensor ist abgefallen",
    "Ein anderes Problem",
]
PROBLEMARTEN_SET = [
    "Okklusion",
    "Abgefallen",
    "Schmerz/Entzündung",
    "Blutung",
    "Sonstiges",
]
PROBLEMARTEN_AMPULLE = ["Sonstiges"]
PROBLEMARTEN = {SET: PROBLEMARTEN_SET, CGM: PROBLEMARTEN_CGM, AMPULLE: PROBLEMARTEN_AMPULLE}

SETZSTELLEN_CGM = [
    "Oberarm",
    "Bauch",
    "Oberes Gesäß",
    "Rücken (Off-Label-Stelle)",
    "Bein (Off-Label-Stelle)",
    "Oberschenkel (Off-Label-Stelle)",
    "Brust (Off-Label-Stelle)",
    "Sonstige (Off-Label-Stelle)",
    "Kann/will nicht antworten",
]
SETZSTELLEN_SET = [s.replace(" (Off-Label-Stelle)", "") for s in SETZSTELLEN_CGM]
SETZSTELLEN = {SET: SETZSTELLEN_SET, CGM: SETZSTELLEN_CGM, AMPULLE: []}
# Anzeige → Originalwortlaut im Dexcom-Formular (Reklamationspaket)
SETZSTELLE_DEXCOM = {"Oberes Gesäß": "Oberes Gesäß (Alter 2–6)"}
# Früher gespeicherte Werte → aktuelle Anzeige
SETZSTELLE_ALT = {v: k for k, v in SETZSTELLE_DEXCOM.items()}
SEITEN = ["links", "rechts"]

LUECKE_UNTER_1 = "Unter 1 Stunde"
LUECKE_1_BIS_3 = "Zwischen 1 und 3 Stunden"
LUECKE_UEBER_3 = "Über 3 Stunden"
LUECKE_KEINE = "Keine Angabe"

# Unzuverlässige Zeiträume eines CGM-Sensors (kurze, häufige Lücken und rauschende Werte).
# Eine Störung ist eine Lücke (> 10 min ohne Wert; > 60 min heißt Ausfall) oder ein Sprung
# (|w(i+1) − 2·w(i) + w(i−1)| ≥ 30 mg/dl bei Nachbarwerten höchstens 11 min auseinander).
# Störungen mit höchstens 30 min Abstand bilden eine Gruppe; ab 4 Störungen ist sie eine unzuverlässige Spanne.
STOERUNG_LUECKE_MIN = 10
STOERUNG_AUSFALL_MIN = 60
STOERUNG_SPRUNG_MG_DL = 30
STOERUNG_NACHBAR_MAX_S = 660
SPANNE_ABSTAND_MIN = 30
SPANNE_MIN_STOERUNGEN = 4

# Evidenz-Fenster vor Sensorende: mindestens 12 h, höchstens 72 h; beginnt eine unzuverlässige Spanne früher,
# beginnt das Fenster 1 h vor ihr.
EVIDENZ_MIN_H = 12
EVIDENZ_MAX_H = 72
EVIDENZ_VORLAUF_H = 1
