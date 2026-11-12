from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from t1d_material.const import (
    AMPULLE,
    CGM,
    LUECKE_1_BIS_3,
    LUECKE_KEINE,
    LUECKE_UEBER_3,
    LUECKE_UNTER_1,
    SET,
    STATUS_AKTIV,
    STATUS_KEIN,
    STATUS_TOLERANZ,
    STATUS_UEBERFAELLIG,
)
from t1d_material.model import (
    Modell,
    ModellFehler,
    TypConfig,
    dedupe_zeiten,
    groesste_luecke_min,
    leerer_zustand,
    luecke_stufe,
)

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=TZ)
H = timedelta(hours=1)
M = timedelta(minutes=1)
SEK = timedelta(seconds=1)
MERGE = 2 * H
DEDUPE = 30 * M


def neues_modell(einrichtung=T0 - 100 * H):
    cfg = {
        SET: TypConfig(72, 12, 10),
        CGM: TypConfig(240, 12, 1),
        AMPULLE: TypConfig(None, 0, 25),
    }
    return Modell(leerer_zustand(einrichtung), cfg)


def test_status_ohne_zyklus():
    m = neues_modell()
    s = m.status(SET, T0)
    assert s["status"] == STATUS_KEIN and s["restzeit_h"] is None


def test_wechsel_status_verlauf_und_bestand():
    m = neues_modell()
    m.bestand_setzen(SET, 5, T0)
    m.wechsel(SET, T0, T0)
    assert m.z["bestand"][SET] == 4
    assert m.status(SET, T0 + H)["status"] == STATUS_AKTIV
    assert m.status(SET, T0 + H)["restzeit_h"] == 71
    assert m.status(SET, T0 + 36 * H)["restzeit_prozent"] == 50
    assert m.status(SET, T0 + 73 * H)["status"] == STATUS_TOLERANZ
    s = m.status(SET, T0 + 90 * H)
    assert s["status"] == STATUS_UEBERFAELLIG and s["restzeit_h"] == -18
    assert s["restzeit_prozent"] == 0
    # Reichweite: 4 Sets * 3 d + Rest (0, da überfällig)
    assert s["reichweite_d"] == 12


def test_neuer_start_beendet_alten_mit_endgrund():
    m = neues_modell()
    a = m.wechsel(CGM, T0, T0)
    b = m.wechsel(CGM, T0 + 100 * H, T0 + 100 * H,
                  ende={"problemart": "Sensor ausgefallen", "ruecksendung": False})
    assert a["ende"] == b["start"]
    assert a["endgrund"] == "vorzeitig" and a["problemart"] == "Sensor ausgefallen"
    assert a["evidenz_status"] == "offen"
    assert m.aktiv(CGM)["id"] == b["id"]


def test_regulaeres_ende_ohne_problem():
    m = neues_modell()
    a = m.wechsel(SET, T0, T0)
    m.wechsel(SET, T0 + 72 * H, T0 + 72 * H)
    assert a["endgrund"] == "regulaer"


def test_rueckdatieren_zwischen_zwei_zyklen():
    m = neues_modell()
    a = m.wechsel(SET, T0, T0)
    c = m.wechsel(SET, T0 + 80 * H, T0 + 80 * H)
    # vergessener Wechsel dazwischen
    b = m.wechsel(SET, T0 + 40 * H, T0 + 81 * H)
    assert a["ende"] == b["start"]
    assert b["ende"] == c["start"]
    assert m.aktiv(SET)["id"] == c["id"]


def test_zukunft_abgelehnt():
    m = neues_modell()
    with pytest.raises(ModellFehler):
        m.wechsel(SET, T0 + H, T0)


def test_request_id_idempotent():
    m = neues_modell()
    m.bestand_setzen(SET, 3, T0)
    a = m.wechsel(SET, T0, T0, request_id="r1")
    b = m.wechsel(SET, T0, T0 + M, request_id="r1")
    assert a["id"] == b["id"]
    assert len(m.z["zyklen"]) == 1 and m.z["bestand"][SET] == 2


def test_loeschen_oeffnet_vorgaenger_und_bucht_zurueck():
    m = neues_modell()
    m.bestand_setzen(SET, 3, T0)
    a = m.wechsel(SET, T0, T0)
    b = m.wechsel(SET, T0 + 10 * H, T0 + 10 * H, ende={"problemart": "Okklusion"})
    assert m.z["bestand"][SET] == 1
    m.loeschen(b["id"], T0 + 11 * H)
    assert a["ende"] is None and "problemart" not in a and "endgrund" not in a
    assert m.z["bestand"][SET] == 2
    assert m.aktiv(SET)["id"] == a["id"]


def test_loeschen_mittlerer_zyklus():
    m = neues_modell()
    a = m.wechsel(SET, T0, T0)
    b = m.wechsel(SET, T0 + 10 * H, T0 + 10 * H)
    c = m.wechsel(SET, T0 + 20 * H, T0 + 20 * H)
    m.loeschen(b["id"], T0 + 21 * H)
    assert a["ende"] == c["start"]


def test_beenden_ohne_ersatz():
    m = neues_modell()
    a = m.wechsel(CGM, T0, T0)
    m.beenden(CGM, T0 + 50 * H, T0 + 50 * H, ende={"problemart": "Sensor ist abgefallen"})
    assert a["ende"] and m.aktiv(CGM) is None
    assert m.status(CGM, T0 + 51 * H)["status"] == STATUS_KEIN
    with pytest.raises(ModellFehler):
        m.beenden(CGM, T0 + 52 * H, T0 + 52 * H)


def test_bearbeiten_und_ueberlappung():
    m = neues_modell()
    a = m.wechsel(SET, T0, T0)
    b = m.wechsel(SET, T0 + 10 * H, T0 + 10 * H)
    m.bearbeiten(b["id"], {"setzstelle": "Bauch", "seite": "links"}, T0 + 11 * H)
    assert b["setzstelle"] == "Bauch"
    with pytest.raises(ModellFehler):
        m.bearbeiten(b["id"], {"start": (T0 + 5 * H).isoformat()}, T0 + 11 * H)
    with pytest.raises(ModellFehler):
        m.bearbeiten(a["id"], {"foo": 1}, T0)


def test_inventar():
    m = neues_modell()
    assert m.packung_einbuchen(SET, 2, T0) == 20
    assert m.bestand_setzen(SET, 7, T0) == 7
    with pytest.raises(ModellFehler):
        m.bestand_setzen(SET, -1, T0)
    assert [b["grund"] for b in m.z["buchungen"]] == ["packung", "korrektur"]


def test_dedupe():
    z = [T0, T0 + M, T0 + 40 * M, T0 + 10 * H]
    assert dedupe_zeiten(z, DEDUPE) == [T0, T0 + 40 * M, T0 + 10 * H]


def test_glooko_erster_abruf_nur_juengstes_ohne_buchung():
    m = neues_modell(einrichtung=T0 + 200 * H)
    m.bestand_setzen(SET, 5, T0)
    ev = [T0, T0 + 80 * H, T0 + 160 * H, T0 + 160 * H + M]
    neu = m.glooko_events(SET, ev, T0 + 200 * H, DEDUPE, MERGE)
    assert len(neu) == 1 and neu[0]["start"] == (T0 + 160 * H).isoformat()
    assert m.z["bestand"][SET] == 5  # vor Einrichtung → keine Buchung
    # zweiter Abruf mit gleichen Daten: nichts Neues
    assert m.glooko_events(SET, ev, T0 + 201 * H, DEDUPE, MERGE) == []
    # neues Event nach Einrichtung → wird gebucht
    neu = m.glooko_events(SET, ev + [T0 + 240 * H], T0 + 241 * H, DEDUPE, MERGE)
    assert len(neu) == 1 and m.z["bestand"][SET] == 4


def test_glooko_dann_app_wird_zusammengefuehrt():
    m = neues_modell(einrichtung=T0 + M)  # erstes Event vor Einrichtung
    m.bestand_setzen(SET, 5, T0)
    m.glooko_events(SET, [T0], T0, DEDUPE, MERGE)
    m.glooko_events(SET, [T0, T0 + 80 * H], T0 + 81 * H, DEDUPE, MERGE)
    assert m.z["bestand"][SET] == 4
    c = m.wechsel(SET, T0 + 81 * H, T0 + 81 * H, start={"setzstelle": "Bauch", "seite": "rechts"},
                  ende={"problemart": "Okklusion"})
    assert c["quelle"] == "zusammengefuehrt"
    assert c["start"] == (T0 + 80 * H).isoformat()  # Zeit aus Glooko
    assert c["setzstelle"] == "Bauch"
    assert len(m.zyklen(SET)) == 2 and m.z["bestand"][SET] == 4  # nur einmal gebucht
    vorher = m.zyklen(SET)[0]
    assert vorher["problemart"] == "Okklusion" and vorher["endgrund"] == "vorzeitig"


def test_app_dann_glooko_wird_zusammengefuehrt():
    m = neues_modell(einrichtung=T0 + M)  # erstes Event vor Einrichtung
    m.bestand_setzen(SET, 5, T0)
    m.glooko_events(SET, [T0], T0, DEDUPE, MERGE)
    a = m.zyklen(SET)[0]
    b = m.wechsel(SET, T0 + 81 * H, T0 + 81 * H, start={"setzstelle": "Bauch"})
    assert m.z["bestand"][SET] == 4
    m.glooko_events(SET, [T0, T0 + 80 * H + 30 * M], T0 + 82 * H, DEDUPE, MERGE)
    assert len(m.zyklen(SET)) == 2 and m.z["bestand"][SET] == 4
    assert b["quelle"] == "zusammengefuehrt"
    assert b["start"] == (T0 + 80 * H + 30 * M).isoformat()
    assert a["ende"] == b["start"]


def test_luecke():
    mess = [T0 + i * 5 * M for i in range(1, 100)]  # Warm-up 5 min am Anfang
    ende = mess[-1] + 5 * M
    assert groesste_luecke_min(mess, T0, ende) == 0.0
    mit_luecke = mess[:40] + mess[60:]  # 100 min Lücke
    assert groesste_luecke_min(mit_luecke, T0, ende) == 105.0
    # Sensor fällt aus, Ende 4 h nach letztem Wert
    assert groesste_luecke_min(mess, T0, mess[-1] + 240 * M) == 240.0
    assert groesste_luecke_min([], T0, ende) is None
    assert luecke_stufe(None) == LUECKE_KEINE
    assert luecke_stufe(0) == LUECKE_UNTER_1
    assert luecke_stufe(59.9) == LUECKE_UNTER_1
    assert luecke_stufe(60) == LUECKE_1_BIS_3
    assert luecke_stufe(180) == LUECKE_1_BIS_3
    assert luecke_stufe(181) == LUECKE_UEBER_3


def test_sortierung_ueber_zeitzonen_offsets():
    from datetime import timezone
    m = neues_modell()
    # 23:30 UTC liegt NACH 00:15+01:00 (= 23:15 UTC), als Text aber davor
    a = m.wechsel(SET, datetime(2026, 11, 10, 0, 15, tzinfo=TZ), T0 + 400 * H)
    b = m.wechsel(SET, datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc), T0 + 400 * H)
    assert [c["id"] for c in m.zyklen(SET)] == [a["id"], b["id"]]
    assert m.aktiv(SET)["id"] == b["id"] and a["ende"] == b["start"]


def test_ampulle_ohne_tragezeit_reichweite_aus_median():
    m = neues_modell()
    m.packung_einbuchen(AMPULLE, 1, T0)
    s = m.status(AMPULLE, T0)
    assert s["status"] == STATUS_KEIN and s["reichweite_d"] is None
    for i, h in enumerate([0, 50, 100, 300, 350]):  # Abstände 50, 50, 200(Ausreißer), 50
        m.wechsel(AMPULLE, T0 + h * H, T0 + h * H)
    s = m.status(AMPULLE, T0 + 360 * H)
    assert s["status"] == STATUS_AKTIV and s["restzeit_h"] is None and s["ablauf"] is None
    assert s["abstand_h"] == 50  # Median, nicht Mittelwert
    assert s["bestand"] == 20 and s["reichweite_d"] == round(20 * 50 / 24, 1)


# ------------------------------------------------------------- v0.2: Reichweite, Insulin, Migration
from t1d_material.const import SETZSTELLEN_CGM, SETZSTELLE_DEXCOM  # noqa: E402
from t1d_material.model import InsulinConfig, insulin_summe, migrieren  # noqa: E402


def _zyklen(m, typ, dauern_h, start=T0):
    t = start
    for d in dauern_h:
        m.wechsel(typ, t, t)
        t += d * H
    return t


def test_reichweite_set_nutzt_median_ab_drei_wechseln():
    m = neues_modell()
    m.bestand_setzen(SET, 10, T0)
    jetzt = _zyklen(m, SET, [48, 48, 48, 48])  # 4 abgeschlossene à 48 h, 5. läuft nicht
    m.wechsel(SET, jetzt, jetzt)
    s = m.status(SET, jetzt)
    assert s["reichweite_basis"] == "median" and s["abstand_h"] == 48
    # Bestand 5 × 48 h + Rest des laufenden bis zum typischen Wechsel (48 h)
    assert s["reichweite_d"] == round((5 * 48 + 48) / 24, 1)


def test_reichweite_set_soll_bei_wenig_historie():
    m = neues_modell()
    m.bestand_setzen(SET, 3, T0)
    m.wechsel(SET, T0, T0)
    s = m.status(SET, T0 + 12 * H)
    assert s["reichweite_basis"] == "soll"
    assert s["reichweite_d"] == round((2 * 72 + 60) / 24, 1)


def test_insulin_summe_anteilig():
    basal = [(T0, T0 + 2 * H, 1.0), (T0 + 2 * H, T0 + 4 * H, 2.0)]
    boli = [(T0 + 30 * M, 3.0), (T0 + 5 * H, 9.0)]
    assert insulin_summe(basal, boli, T0 + H, T0 + 3 * H) == pytest.approx(1.0 + 2.0)
    assert insulin_summe(basal, boli, T0, T0 + 4 * H) == pytest.approx(2 + 4 + 3)


def test_insulin_fuellstand_aus_glooko():
    m = neues_modell()
    m.insulin = InsulinConfig(160, 12, 20)
    m.bestand_setzen(AMPULLE, 5, T0)
    c = m.wechsel(AMPULLE, T0, T0, start={"schlauch_gewechselt": True})
    stand = T0 + 24 * H
    m.z["insulin"] = {"zyklus_id": c["id"], "verbrauch_u": 40.0, "stand": stand.isoformat(),
                      "tagesverbrauch_u": 48.0}
    s = m.status(AMPULLE, stand)
    assert s["fuellstand_u"] == 160 - 12 - 40
    assert s["status"] == STATUS_AKTIV
    assert s["reichweite_d"] == round((108 + 4 * 148) / 48, 1)  # Bestand nach Wechsel: 4
    # 1,5 h nach Datenstand: hochgerechnet mit 2 U/h
    s2 = m.status(AMPULLE, stand + 90 * M)
    assert s2["fuellstand_u"] == 105.0
    # ohne Schlauch: statt 12 U nur 1 U fürs Anfahren des Stempels
    m.bearbeiten(c["id"], {"schlauch_gewechselt": False}, stand)
    assert m.status(AMPULLE, stand)["fuellstand_u"] == 119
    assert m.status(AMPULLE, stand)["zyklus"]["schlauch_gewechselt"] is False


def test_insulin_warnung_und_leer():
    m = neues_modell()
    c = m.wechsel(AMPULLE, T0, T0)
    m.z["insulin"] = {"zyklus_id": c["id"], "verbrauch_u": 145.0, "stand": (T0 + 70 * H).isoformat(),
                      "tagesverbrauch_u": 48.0}
    assert m.status(AMPULLE, T0 + 70 * H)["status"] == STATUS_TOLERANZ  # 13 U ≤ 20 U
    m.z["insulin"]["verbrauch_u"] = 165.0
    assert m.status(AMPULLE, T0 + 70 * H)["status"] == STATUS_UEBERFAELLIG


def test_insulin_neuer_wechsel_vor_glooko_daten_gilt_als_voll():
    m = neues_modell()
    alt = m.wechsel(AMPULLE, T0, T0)
    m.z["insulin"] = {"zyklus_id": alt["id"], "verbrauch_u": 100.0, "stand": (T0 + 60 * H).isoformat(),
                      "tagesverbrauch_u": 48.0}
    m.wechsel(AMPULLE, T0 + 61 * H, T0 + 61 * H, start={"schlauch_gewechselt": True})
    assert m.status(AMPULLE, T0 + 61 * H)["fuellstand_u"] == 148


def test_setzstelle_ohne_altersangabe_und_migration():
    assert "Oberes Gesäß" in SETZSTELLEN_CGM
    assert not any("Alter" in s for s in SETZSTELLEN_CGM)
    assert SETZSTELLE_DEXCOM["Oberes Gesäß"] == "Oberes Gesäß (Alter 2–6)"
    z = leerer_zustand(T0)
    z.pop("insulin")
    z["zyklen"].append({"id": "x", "typ": CGM, "start": T0.isoformat(), "ende": None,
                        "setzstelle": "Oberes Gesäß (Alter 2–6)"})
    assert migrieren(z) is True
    assert z["zyklen"][0]["setzstelle"] == "Oberes Gesäß" and z["insulin"] == {}
    assert migrieren(z) is False


def _insulin_modell():
    m = neues_modell()
    m.insulin = InsulinConfig(160, 12, 20, 0.3, 0.0)  # ohne Reservoir-Anfahren, damit die Zahlen klar bleiben
    c = m.wechsel(AMPULLE, T0, T0)  # Reservoir per App, Standard ohne Schlauch
    m.z["insulin"] = {"zyklus_id": c["id"], "verbrauch_u": 0.0, "stand": (T0 + 100 * H).isoformat(),
                      "tagesverbrauch_u": 48.0}
    return m, c


def test_glooko_schlauch_mit_setwechsel_kein_neues_reservoir():
    m, c = _insulin_modell()
    assert m.glooko_schlauch([T0 + 50 * H], T0 + 51 * H) == 1
    assert m.aktiv(AMPULLE)["id"] == c["id"]  # kein neuer Reservoir-Zyklus
    assert m.status(AMPULLE, T0 + 100 * H)["fuellstand_u"] == 160 - 12
    # erneuter Abgleich mit demselben Event: nichts doppelt
    assert m.glooko_schlauch([T0 + 50 * H, T0 + 50 * H + 5 * M], T0 + 52 * H) == 0


def test_glooko_setwechsel_mit_schlauch_kostet_schlauch_und_kanuele():
    m, c = _insulin_modell()
    t = T0 + 70 * H
    m.glooko_events(SET, [t + 25 * SEK], t + H, DEDUPE, MERGE)
    m.glooko_schlauch([t], t + H)
    s = m.status(SET, t + H)
    assert s["zyklus"]["schlauch_gewechselt"] is True
    ins = m.status(AMPULLE, T0 + 100 * H)
    assert ins["fuellstand_u"] == pytest.approx(160 - 12 - 0.3)
    assert ins["schlauch_fuellungen"] == 1 and ins["kanuele_fuellungen"] == 1


def test_app_setwechsel_ohne_schlauch_nur_kanuele():
    m, c = _insulin_modell()
    sc = m.wechsel(SET, T0 + 70 * H, T0 + 70 * H)
    assert m.ansicht(sc)["schlauch_gewechselt"] is False
    assert m.status(AMPULLE, T0 + 100 * H)["fuellstand_u"] == pytest.approx(160 - 0.3)
    sc2 = m.wechsel(SET, T0 + 80 * H, T0 + 80 * H, start={"schlauch_gewechselt": True})
    assert m.status(AMPULLE, T0 + 100 * H)["fuellstand_u"] == pytest.approx(160 - 12 - 0.6)
    # App-Chip + späteres Glooko-Event am selben Wechsel: nur einmal
    m.glooko_schlauch([T0 + 80 * H + 3 * M], T0 + 90 * H)
    assert m.status(AMPULLE, T0 + 100 * H)["schlauch_fuellungen"] == 1
    # Löschen des Setwechsels nimmt die App-Füllung mit... die Glooko-Bestätigung bleibt
    m.loeschen(sc2["id"], T0 + 95 * H)
    assert m.status(AMPULLE, T0 + 100 * H)["schlauch_fuellungen"] == 1


def test_schlauch_abgewaehlt_bleibt_aus_trotz_glooko():
    m, c = _insulin_modell()
    t = T0 + 70 * H
    m.glooko_events(SET, [t], t + H, DEDUPE, MERGE)
    m.glooko_schlauch([t], t + H)
    sc = m.aktiv(SET)
    m.bearbeiten(sc["id"], {"schlauch_gewechselt": False}, t + 2 * H)
    assert m.glooko_schlauch([t], t + 3 * H) == 0
    assert m.status(SET, t + 3 * H)["zyklus"]["schlauch_gewechselt"] is False
    assert m.status(AMPULLE, T0 + 100 * H)["fuellstand_u"] == pytest.approx(160 - 0.3)


def test_migration_schlauch_gefuellt():
    z = leerer_zustand(T0)
    del z["schlauch"]
    z["zyklen"] = [
        {"id": "a", "typ": AMPULLE, "start": T0.isoformat(), "ende": None, "quelle": "app"},
        {"id": "b", "typ": AMPULLE, "start": (T0 - 80 * H).isoformat(), "ende": T0.isoformat(), "quelle": "app",
         "schlauch_gefuellt": False},
    ]
    m = Modell(z, neues_modell().config)
    assert [f["zyklus_id"] for f in z["schlauch"]] == ["a"]  # Standard war „ja“
    assert "schlauch_gefuellt" not in z["zyklen"][1]
    assert m.ansicht(z["zyklen"][0])["schlauch_gewechselt"] is True


# ------------------------------------------------------------------ v0.4: Reservoir aus Glooko, TIR
from t1d_material.model import tir_statistik  # noqa: E402


def test_reservoir_ohne_schlauch_kostet_anfahren():
    m = neues_modell()
    m.insulin = InsulinConfig(160, 12, 20, 0.3, 1.0)
    c = m.wechsel(AMPULLE, T0, T0)
    m.z["insulin"] = {"zyklus_id": c["id"], "verbrauch_u": 0.0, "stand": T0.isoformat(), "tagesverbrauch_u": 48.0}
    s = m.status(AMPULLE, T0)
    assert s["fuellstand_u"] == 159 and s["reservoir_fuellung_u"] == 1.0
    m.bearbeiten(c["id"], {"schlauch_gewechselt": True}, T0)
    s = m.status(AMPULLE, T0)
    assert s["fuellstand_u"] == 148 and s["reservoir_fuellung_u"] == 0  # 12 U, nicht 13 U


def test_glooko_reservoir_allein_fuehrt_mit_app_zusammen_und_raeumt_v03_auf():
    m = neues_modell()
    m.insulin = InsulinConfig(160, 12, 20, 0.3, 1.0)
    m.bestand_setzen(AMPULLE, 5, T0)
    c = m.wechsel(AMPULLE, T0, T0)  # App, ohne Chip
    t = T0 + 3 * M  # Glooko „Schlauch füllen“ ohne Kanüle
    m.z["schlauch"].append({"zeit": t.isoformat(), "quelle": "glooko", "zyklus_id": None})  # Altlast v0.3
    m.z["insulin"] = {"zyklus_id": c["id"], "verbrauch_u": 0.0, "stand": t.isoformat(), "tagesverbrauch_u": 48.0}
    assert m.glooko_schlauch([], t + H, reservoir=[t]) == 0
    neu = m.glooko_events(AMPULLE, [t], t + H, DEDUPE, MERGE)
    assert len(m.zyklen(AMPULLE)) == 1 and neu[0]["quelle"] == "zusammengefuehrt"
    assert m.z["bestand"][AMPULLE] == 4  # nicht doppelt gebucht
    s = m.status(AMPULLE, t)
    assert s["zyklus"]["schlauch_gewechselt"] is False and s["fuellstand_u"] == 159


def test_glooko_reservoir_ohne_app_wird_angelegt_und_bleibt_geloescht():
    m = neues_modell()
    m.bestand_setzen(AMPULLE, 5, T0)
    m.wechsel(AMPULLE, T0, T0)
    m.z["glooko"]["initialisiert"][AMPULLE] = True
    t = T0 + 80 * H
    neu = m.glooko_events(AMPULLE, [t], t + H, DEDUPE, MERGE)
    assert neu[0]["quelle"] == "glooko" and m.aktiv(AMPULLE)["id"] == neu[0]["id"]
    assert m.z["bestand"][AMPULLE] == 3
    m.loeschen(neu[0]["id"], t + 2 * H)
    assert m.z["bestand"][AMPULLE] == 4
    assert m.glooko_events(AMPULLE, [t], t + 3 * H, DEDUPE, MERGE) == []
    assert len(m.zyklen(AMPULLE)) == 1


def test_glooko_reservoir_wird_zurueckgenommen_wenn_kanuele_nachkommt():
    m = neues_modell()
    m.bestand_setzen(AMPULLE, 5, T0)
    m.wechsel(AMPULLE, T0, T0)
    m.z["glooko"]["initialisiert"][AMPULLE] = True
    t = T0 + 80 * H
    m.glooko_events(AMPULLE, [t], t + H, DEDUPE, MERGE)
    assert len(m.zyklen(AMPULLE)) == 2
    # nächster Upload: Kanüle füllen 30 s später → war ein Setwechsel mit Schlauch
    assert m.glooko_reservoir_zuruecknehmen([t], t + 2 * H) == 1
    m.glooko_schlauch([t], t + 2 * H)
    assert len(m.zyklen(AMPULLE)) == 1 and m.z["bestand"][AMPULLE] == 4
    assert m.schlauch_zeiten() == [t]
    # gemerkt wird nichts – ein späteres echtes Reservoir-Event zur selben Zeit gibt es nicht, aber auch keine Sperre
    assert m.z["glooko"].get("geloescht", []) == []


def test_tir_statistik_bereiche_und_grenzen():
    # alle Werte gleich alt (≈ gleiches Gewicht): Anteile wie gezählt
    werte = [(T0 + i * M, v) for i, v in enumerate([53, 54, 69, 70, 180, 181, 250, 251, 120, 120])]
    bis = T0 + 9 * M
    s = tir_statistik(werte, bis, (54, 70, 180, 250))
    assert set(s) == {"sehr_tief", "tief", "im_ziel", "hoch", "sehr_hoch"}
    assert abs(s["sehr_tief"] - 10) < 0.1 and abs(s["tief"] - 20) < 0.1 and abs(s["im_ziel"] - 40) < 0.1
    assert abs(s["hoch"] - 20) < 0.1 and abs(s["sehr_hoch"] - 10) < 0.1
    assert abs(sum(s.values()) - 100) < 0.5
    assert tir_statistik(werte, T0 + 15 * 24 * H, (54, 70, 180, 250)) is None  # alles älter als 14 Tage
    assert tir_statistik(werte, T0 - H, (54, 70, 180, 250)) is None  # Werte nach „bis“ zählen nicht
    # eigener Zielbereich
    assert abs(tir_statistik(werte, bis, (54, 70, 140, 250))["im_ziel"] - 30) < 0.1


def test_tir_statistik_vergisst_exponentiell():
    tag = 24 * H
    bis = T0 + 14 * tag
    # vor 7 Tagen 100 (im Ziel), jetzt 300 (stark darüber): Gewicht 0,5 zu 1
    s = tir_statistik([(bis - 7 * tag, 100), (bis, 300)], bis, (54, 70, 180, 250))
    assert abs(s["im_ziel"] - 100 / 3) < 0.1 and abs(s["sehr_hoch"] - 200 / 3) < 0.1
    # genau 14 Tage alt zählt noch (Gewicht 0,25), älter nicht
    s = tir_statistik([(bis - 14 * tag, 100), (bis, 300)], bis, (54, 70, 180, 250))
    assert abs(s["im_ziel"] - 20) < 0.1
    s = tir_statistik([(bis - 14 * tag - M, 100), (bis, 300)], bis, (54, 70, 180, 250))
    assert s["im_ziel"] == 0 and s["sehr_hoch"] == 100


# ------------------------------------------------------------------ v0.5: unzuverlässige Spannen, Evidenz-Fenster
from t1d_material.model import evidenz_fenster_start, parse, stoerungen, unzuverlaessige_spannen  # noqa: E402


def _reihe(start, werte, takt=5):
    """Synthetische CGM-Reihe; None = kein Wert (Lücke)."""
    return [(start + i * takt * M, v) for i, v in enumerate(werte) if v is not None]


def test_ruhige_reihe_hat_keine_stoerungen():
    w = _reihe(T0, [120 + (i % 3) for i in range(100)])
    assert stoerungen(w) == [] and unzuverlaessige_spannen(w) == []


def test_einzelne_luecken_ergeben_keine_spanne():
    # drei kurze Lücken weit auseinander
    v = [120] * 200
    for i in (20, 21, 100, 101, 180, 181):
        v[i] = None
    w = _reihe(T0, v)
    assert [e["art"] for e in stoerungen(w)] == ["luecke"] * 3
    assert unzuverlaessige_spannen(w) == []


def test_gehaeufte_luecken_und_spruenge_bilden_spanne():
    v = [120] * 120
    for i in (30, 31, 36, 37, 42, 43):  # drei Lücken à 15 min dicht beieinander
        v[i] = None
    v[45] = 170  # Sprung: Zickzack
    v[60] = 60
    w = _reihe(T0, v)
    arten = [e["art"] for e in stoerungen(w)]
    assert arten.count("luecke") == 3 and arten.count("sprung") >= 2
    sp = unzuverlaessige_spannen(w)
    assert len(sp) == 1
    s = sp[0]
    assert s["luecken"] == 3 and s["spruenge"] >= 2 and s["ausfaelle"] == 0
    assert parse(s["von"]) == T0 + 29 * 5 * M  # letzter Wert vor der ersten Lücke
    assert s["dauer_min"] == (parse(s["bis"]) - parse(s["von"])).total_seconds() / 60


def test_ausfall_zaehlt_als_stoerung():
    v = [120] * 100
    for i in range(30, 45):  # 80 min ohne Wert
        v[i] = None
    for i in (50, 51, 56, 57, 62, 63):  # drei Lücken à 15 min
        v[i] = None
    w = _reihe(T0, v)
    sp = unzuverlaessige_spannen(w)
    assert len(sp) == 1 and sp[0]["ausfaelle"] == 1 and sp[0]["luecken"] == 3


def test_stoerungen_weiter_als_30_min_auseinander_trennen():
    v = [120] * 200
    for i in (20, 21, 28, 29, 36, 37, 100, 101, 108, 109, 116, 117):  # je 3 Lücken, Gruppen weit auseinander
        v[i] = None
    assert unzuverlaessige_spannen(_reihe(T0, v)) == []  # je Gruppe nur 3 Störungen


def test_evidenz_fenster():
    ende = T0 + 10 * 24 * H
    assert evidenz_fenster_start(ende, []) == ende - 12 * H
    spanne = lambda von, bis: {"von": von.isoformat(), "bis": bis.isoformat()}  # noqa: E731
    # Spanne beginnt 20 h vor Ende → Fenster 21 h
    assert evidenz_fenster_start(ende, [spanne(ende - 20 * H, ende - 18 * H)]) == ende - 21 * H
    # Spanne kurz vor Ende ändert nichts am Mindestfenster
    assert evidenz_fenster_start(ende, [spanne(ende - 3 * H, ende - H)]) == ende - 12 * H
    # sehr frühe Spanne, die noch in die letzten 72 h reicht → höchstens 72 h
    assert evidenz_fenster_start(ende, [spanne(ende - 100 * H, ende - 70 * H)]) == ende - 72 * H
    # Spanne komplett älter als 72 h zählt nicht
    assert evidenz_fenster_start(ende, [spanne(ende - 100 * H, ende - 80 * H)]) == ende - 12 * H


def test_zeitumstellung_trennt_doppelte_stunde():
    pytest.importorskip("aiohttp")
    from t1d_material.glooko import cgm_werte

    # Umstellung 25.10.2026 03:00 MESZ → 02:00 MEZ; Ortszeit 02:00–02:59 kommt zweimal
    def p(h, mi, y):
        return {"timestamp": f"2026-10-25T{h:02d}:{mi:02d}:00.000Z", "y": y}

    vorher = [p(1, m, 150 - m // 5) for m in range(30, 60, 5)]           # 150 … 145, fällt langsam
    erste = [p(2, m, 144 - 2 * (m // 5)) for m in range(0, 60, 5)]       # Sommerzeit: 144 … 122
    zweite = [p(2, m, 120 - 2 * (m // 5)) for m in range(1, 60, 5)]      # Winterzeit: 120 … 98
    nachher = [p(3, m, 96 - 2 * (m // 5)) for m in range(1, 30, 5)]
    w = cgm_werte({"cgmNormal": vorher + erste + zweite + nachher}, TZ)
    assert len(w) == 6 + 12 + 12 + 6
    zeiten = [t for t, _ in w]
    assert zeiten == sorted(zeiten)
    assert all(5 * M - M <= b - a <= 5 * M + M for a, b in zip(zeiten, zeiten[1:]))  # durchgehend 5-min-Takt
    assert [v for _, v in w] == [v["y"] for v in vorher + erste + zweite + nachher]
    assert stoerungen(w) == []
