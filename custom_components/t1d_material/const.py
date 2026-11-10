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

# Glooko-Serien (CamAPS FX, geprüft 2026-11):
# - setSiteChange   = „Kanüle füllen“  → Setwechsel
# - reservoirChange = „Schlauch füllen“ → KEIN Reservoirwechsel, nur Schlauchfüllung (kostet Insulin)
# Den Rücklauf (echter Reservoirwechsel) und CGM-Wechsel liefert Glooko nicht → nur per App.
GLOOKO_SERIE = {SET: "setSiteChange"}
GLOOKO_SCHLAUCH = "reservoirChange"
SCHLAUCH_FENSTER_MIN = 15  # Schlauchfüllung gehört zu einem Wechsel, wenn so nah am Start

# Config-Schlüssel
CONF_EMAIL = "glooko_email"
CONF_PASSWORD = "glooko_password"
CONF_INTERVALL_MIN = "glooko_intervall_min"
CONF_MERGE_H = "merge_fenster_h"
CONF_DEDUPE_MIN = "dedupe_min"
CONF_EVIDENZ_H = "evidenz_h"
CONF_TRAGEZEIT_D = "tragezeit_d"
CONF_TOLERANZ_H = "toleranz_h"
CONF_PACKUNG = "packung"
CONF_PRODUKT = "produkt"
CONF_INSULIN_VOLL_U = "insulin_voll_u"
CONF_INSULIN_FUELLEN_U = "insulin_fuellen_u"
CONF_INSULIN_WARNUNG_U = "insulin_warnung_u"
CONF_INSULIN_KANUELE_U = "insulin_kanuele_u"

DEFAULTS_GLOBAL = {
    CONF_INTERVALL_MIN: 60,
    CONF_MERGE_H: 2.0,
    CONF_DEDUPE_MIN: 30,
    CONF_EVIDENZ_H: 24,
    CONF_INSULIN_VOLL_U: 160.0,     # volle Ampulle
    CONF_INSULIN_FUELLEN_U: 12.0,   # Schlauch füllen
    CONF_INSULIN_WARNUNG_U: 20.0,   # CamAPS FX warnt hier
    CONF_INSULIN_KANUELE_U: 0.3,    # Kanüle füllen (jeder Setwechsel)
}


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
