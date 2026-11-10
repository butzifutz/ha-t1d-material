# T1D-Material für Home Assistant

Verwaltet Infusionssets, CGM-Sensoren und Insulin (Pump Carts): wann gewechselt werden muss, wie viel Material noch da ist, und Evidenz für Reklamationen (Dexcom). Optionaler Abgleich mit Glooko (CamAPS FX).

> Kein Medizinprodukt. Ersetzt weder die Warnungen der Pumpe/des CGM noch ärztlichen Rat.

## Installation (HACS)

1. HACS → ⋮ → Benutzerdefinierte Repositories → `https://github.com/butzifutz/ha-t1d-material`, Typ *Integration*.
2. „T1D-Material“ installieren, Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → *T1D-Material*. Der Glooko-Zugang ist optional.
4. Tragezeiten, Toleranzen, Packungsgrößen und Insulin-Werte unter *Konfigurieren* (oder per Service `einstellungen_setzen`).

## Geräte und Entitäten

Je Gerät *Infusionsset*, *CGM-Sensor* (mit Tragezeit) und *Insulin* (Füllstand aus Glooko, Bestand = Pump Carts):

| Entität | Inhalt |
|---|---|
| Gesetzt am, Ablauf, Toleranz-Ende | Zeitstempel |
| Restzeit, Restzeit (Prozent) | negativ, wenn überfällig |
| Status | `aktiv`, `toleranz`, `ueberfaellig`, `kein_zyklus` |
| Reichweite | Tage: Bestand × typischer Wechselabstand (Median, ab 3 Wechseln; sonst Soll-Tragezeit) + Rest des laufenden Materials. Insulin: (Füllstand + Bestand × nutzbare Menge) ÷ Ø-Tagesverbrauch |
| Füllstand, Tagesverbrauch (Insulin) | aus Glooko-Basal + Bolus seit Reservoirwechsel; ab Datenstand mit Ø-Verbrauch hochgerechnet |
| Setzstelle (+ Attribut `seite`), Charge (CGM), Quelle | aktueller Zyklus |
| Bestand | änderbar |
| Wechsel jetzt | Button |

Glooko: letzter Abruf, Verbindung.

Benachrichtigungen baut man als Automation, z. B. Trigger „Restzeit unter 12 h“.

## Services

`t1d_material.wechsel`, `beenden`, `bearbeiten`, `loeschen`, `bestand_setzen`, `packung_einbuchen`, `einstellungen_setzen`, `glooko_abrufen`, `daten` (liefert Status, Historie und Auswahllisten als Antwort).

## HTTP-API (Bearer-Token)

| Pfad | Zweck |
|---|---|
| `POST/GET /api/t1d_material/foto/{zyklus_id}` | Verpackungsfoto (JPEG) |
| `GET /api/t1d_material/evidenz/{zyklus_id}` | CGM-Werte vor Sensorende |
| `GET /api/t1d_material/reklamation/{zyklus_id}` | ZIP mit Angaben fürs Dexcom-Formular, Foto, CGM-CSV |
| `GET /api/t1d_material/export.csv` | Alle Zyklen |

## Daten

Zyklen und Bestand liegen im HA-Store (`.storage/t1d_material.*`), Fotos und Evidenz unter `/config/t1d_material/` (nicht öffentlich erreichbar). Beides ist im HA-Backup enthalten.

## Glooko

Glooko hat keine offizielle API; der Login folgt [glooko-reader](https://github.com/spamsch/glooko-reader). Glooko-Zeitstempel tragen ein `Z`, sind aber Ortszeit. CamAPS FX lädt etwa alle 90 Minuten hoch.

Insulin: `scheduledBasal` (enthält jedes Segment mehrfach → entdoppelt; Summe = Glookos `scheduledBasalsSum`) und `deliveredBolus`. Füllstand = Ampulleninhalt − Schlauchfüllen (falls beim Wechsel angegeben) − Verbrauch.
