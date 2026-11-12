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
| Gewechselt am, Ablauf, Toleranz-Ende | Zeitstempel |
| Restzeit, Restzeit (Prozent) | negativ, wenn überfällig |
| Status | `aktiv`, `toleranz`, `ueberfaellig`, `kein_zyklus` |
| Reichweite | Tage: Bestand × typischer Wechselabstand (Median, ab 3 Wechseln; sonst Soll-Tragezeit) + Rest des laufenden Materials. Insulin: (Füllstand + Bestand × nutzbare Menge) ÷ Ø-Tagesverbrauch |
| Füllstand, Tagesverbrauch (Insulin) | aus Glooko-Basal + Bolus seit Reservoirwechsel; ab Datenstand mit Ø-Verbrauch hochgerechnet |
| Setzstelle (+ Attribut `seite`), Charge (CGM), Quelle | aktueller Zyklus |
| Bestand | änderbar |
| Wechsel jetzt | Button |

Gerät *Glooko*: letzter Abruf und *Zeit im Zielbereich* (Prozent der CGM-Werte über die letzten **14 Tage**,
exponentiell gewichtet: Halbwertszeit 7 Tage, ältere Werte zählen weniger, Werte über 14 Tage gar nicht). Die übrigen
Bereiche stehen als Attribute daran: `stark_darunter`, `leicht_darunter`, `leicht_darueber`, `stark_darueber`.
Standardgrenzen (internationaler Konsens, in den Optionen änderbar):
stark darunter < 54 ≤ leicht darunter < 70 ≤ **Ziel ≤ 180** < leicht darüber ≤ 250 < stark darüber (mg/dl).
Gerechnet wird ab dem letzten Glooko-Wert.

## CGM-Evidenz bei Sensorende

Nach jedem Sensorende holt die Integration die CGM-Werte der ganzen Laufzeit aus Glooko und bestimmt:

- **Längste Zeit ohne Werte** (ab dem ersten Wert, wie im Dexcom-Formular abgefragt).
- **Unzuverlässige Zeiträume** – in der Praxis sind die Lücken oft kurz, aber häufig, und die Werte dazwischen
  rauschen. Gezählt werden einzelne Störungen:
  - *Lücke*: mehr als 10 min ohne Wert (mehr als 60 min heißt *Ausfall*),
  - *Sprung*: |w(i+1) − 2·w(i) + w(i−1)| ≥ 30 mg/dl (Zickzack), Nachbarwerte höchstens 11 min auseinander.

  Störungen mit höchstens 30 min Abstand bilden eine Gruppe; ab 4 Störungen ist sie ein unzuverlässiger Zeitraum
  (von der ersten bis zur letzten Störung).
- **Evidenz-Fenster** für Kurve und CSV: mindestens 12 h vor Sensorende; beginnt ein unzuverlässiger Zeitraum
  der letzten 72 h früher, beginnt das Fenster 1 h vor ihm (höchstens 72 h).

Die Zeiträume stehen im Zyklus (`unzuverlaessig`, `unzuverlaessig_min`), im Reklamationstext und als
`unzuverlaessig.json` im Reklamationspaket.

Benachrichtigungen baut man als Automation, z. B. Trigger „Restzeit unter 12 h“.

## Services

`t1d_material.wechsel`, `beenden`, `bearbeiten`, `loeschen`, `bestand_setzen`, `packung_einbuchen`, `einstellungen_setzen`, `glooko_abrufen`, `daten` (liefert Status, Historie und Auswahllisten als Antwort).

## HTTP-API (Bearer-Token)

| Pfad | Zweck |
|---|---|
| `POST/GET /api/t1d_material/foto/{zyklus_id}` | Verpackungsfoto (JPEG) |
| `GET /api/t1d_material/evidenz/{zyklus_id}` | CGM-Werte vor Sensorende |
| `GET /api/t1d_material/reklamation/{zyklus_id}` | ZIP mit Angaben fürs Dexcom-Formular, Foto, CGM-CSV, unzuverlässige Zeiträume |
| `GET /api/t1d_material/export.csv` | Alle Zyklen |

## Daten

Zyklen und Bestand liegen im HA-Store (`.storage/t1d_material.*`), Fotos und Evidenz unter `/config/t1d_material/` (nicht öffentlich erreichbar). Beides ist im HA-Backup enthalten.

## Glooko

Glooko hat keine offizielle API; der Login folgt [glooko-reader](https://github.com/spamsch/glooko-reader). Glooko-Zeitstempel tragen ein `Z`, sind aber Ortszeit. CamAPS FX lädt etwa alle 90 Minuten hoch.

Wechsel-Ereignisse (CamAPS FX):

| Glooko-Serie | Aktion in CamAPS | Bedeutung hier |
|---|---|---|
| `setSiteChange` | Kanüle füllen | Setwechsel (wird automatisch angelegt) |
| `reservoirChange` + `setSiteChange` (±15 min) | Schlauch + Kanüle füllen | Setwechsel mit Schlauch (Schlauchfüllung) |
| `reservoirChange` allein | Schlauch füllen (Stempel anfahren) | Reservoirwechsel ohne Schlauch – nur mit Option „Reservoirwechsel aus Glooko ableiten“ |
| – | Rücklauf | liefert Glooko nicht |

Glooko meldet keine Füllmengen. Die Deutung von `reservoirChange` allein beruht auf zwei Annahmen: Kanüle füllen wird
nicht vergessen, und ein Schlauch wird praktisch nur zusammen mit dem Set gewechselt. Set + Reservoir gleichzeitig
erkennt Glooko nicht (→ App). Gelöschte Glooko-Wechsel werden beim nächsten Abruf nicht neu angelegt.
CGM-Wechsel werden nur in der App (oder per Service `wechsel`) erfasst.

Insulin: `scheduledBasal` (enthält jedes Segment mehrfach → entdoppelt; Summe = Glookos `scheduledBasalsSum`) und `deliveredBolus`. Füllstand = Ampulleninhalt − Anfahren des Stempels beim Reservoirwechsel ohne Schlauch (1 U) − je Schlauchfüllung (12 U; App-Auswahl „Schlauch gewechselt“ oder Glooko) − je Setwechsel das Kanülenfüllen (0,3 U) − Verbrauch. Alle Mengen sind in den Optionen einstellbar.
