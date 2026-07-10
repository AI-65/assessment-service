# PoC: Externe KI-Korrektur-Provider für EDUTIEK

Dieser Fork ergänzt den assessment-service um eine minimale, generische
**External-Correction-Provider-Schnittstelle**: Ein externer Dienst (z. B. ein
KI-Korrekturservice) kann Klausurbearbeitungen exportieren und strukturierte
Korrekturvorschläge (Randbemerkungen, Punkte pro Kriterium, Gesamtvotum)
zurückschreiben. Die Vorschläge erscheinen im Corrector als **eigener
Korrekturlayer eines Pseudo-Korrektors** und bleiben durch die menschlichen
Korrektoren prüf-, änder- und ignorierbar.

Das ist ein **Proof of Concept** — kein offizieller EDUTIEK-Bestandteil, keine
Produktarchitektur, Auth nur PoC-tauglich. Technische Analyse der Codepfade:
[ANALYSE.md](ANALYSE.md).

## Ansatz

Kombination der Varianten A + B aus dem Auftrag:

- **Pseudo-Korrektor (Variante B):** Der Provider bekommt einen ILIAS-
  Service-Account, der als regulärer Korrektor der Klausur zugewiesen wird.
  Alle Scope-, Workflow- und Sichtbarkeitsregeln von EDUTIEK greifen
  unverändert.
- **REST-Endpunkte (Variante A):** Neue Route `provider` neben `writer` und
  `corrector` im bestehenden REST-Einstieg (`endpoints/xlas_rest.php` des
  Plugins). `AppProvider` erbt die komplette Corrector-Logik; es gibt keine
  anbieterspezifische Persistenz.
- **Export/Import per JSON (Variante C):** Zwei kleine Skripte übertragen die
  Daten dateibasiert — für den ersten Freiburg-Test genügt das, eine
  Live-Anbindung nutzt dieselben Endpunkte direkt.

### Geänderte/neue Dateien im Fork

| Datei | Änderung |
|---|---|
| `src/System/Config/Frontend.php` | Enum-Case `PROVIDER = 'provider'` |
| `src/Assessment/Apps/AppProvider.php` | **neu** — Provider-Routen + Key-Auth |
| `src/Assessment/Api/Internal.php` | Factory `appProvider()` |
| `src/Assessment/Apps/Service.php` | Dispatch der `provider`-Route |

Im ILIAS-Plugin (LongEssayAssessment) ist **keine Code-Änderung** nötig, nur
der Composer-Verweis auf diesen Fork.

## Endpunkte

Basis-URL ist der REST-Einstieg des Plugins, z. B.
`https://ilias.example/Customizing/global/plugins/.../LongEssayAssessment/endpoints/xlas_rest.php`.
Alle Calls brauchen die Query-Parameter `ass_id`, `context_id`, `user_id`
(Service-Account) und den Provider-Key (Header `X-Provider-Key` oder
Query-Param `provider_key`).

| Methode/Route | Zweck |
|---|---|
| `GET /provider/data` | Einstellungen, zugewiesene Items, Aufgaben (inkl. Aufgabenstellung + Musterlösung) |
| `GET /provider/item/{task_id}/{writer_id}` | Bearbeitungstext, Kriterien, eigene bisherige Korrektur |
| `PUT /provider/changes` | Kommentare, Punkte, Summary schreiben (Change-Format der Corrector-App) |
| `GET /provider/file/...` | Dateien (z. B. PDF der Bearbeitung) |

## JSON-Schemas

- Input an den Korrekturservice: [schema/correction-request.schema.json](schema/correction-request.schema.json)
- Output zurück an EDUTIEK: [schema/correction-suggestions.schema.json](schema/correction-suggestions.schema.json), Beispiel: [examples/suggestions.example.json](examples/suggestions.example.json)

Kommentar-Anker sind **Wort-Positionen** (`start_position`/`end_position`)
im exportierten Essay-Text — dieselbe Zählung, die die Corrector-App nutzt.

## Smoke-Tests (ohne ILIAS)

`tests/` enthält einen Harness, der die echten Provider-Codepfade gegen
Reflection-generierte Stubs des Host-Systems ausführt — Key-Auth,
`GET /provider/data` und der komplette Weg
`import_suggestions.py → PUT /provider/changes → Bridge-ChangeRequests`:

```bash
composer install          # einmalig (PHP >= 8.2)
python3 poc/external-correction-provider/tests/run_smoke.py
```

Geprüft wird u. a., dass falsche/fehlende Keys mit 401 abgelehnt werden und
dass Kommentare, Punkte (inkl. Kommentar-Verknüpfung und generelle Kriterien)
und Summary (inkl. Disclaimer, Status `pre_graded`) korrekt beim
Corrector-Bridge-Aufruf ankommen.

## Lokaler Test

### 1. Umgebung

1. ILIAS 10 mit LongEssayAssessment-Plugin (Branch `release_10`), dessen
   `composer.json` auf diesen Fork zeigt (Branch
   `poc/external-correction-provider`), dann `composer update` im Plugin.
2. Provider-Key als Umgebungsvariable des PHP-Prozesses setzen, z. B. in der
   Apache-/FPM-Konfiguration:
   `XLAS_PROVIDER_KEY=<langer zufälliger String>`.
   Ohne gesetzte Variable werden alle `/provider`-Calls abgelehnt.

### 2. Testdaten in ILIAS

1. Klausur-Objekt anlegen, online schalten, Aufgabe mit Aufgabenstellung,
   Musterlösung und Bewertungskriterien konfigurieren.
2. Test-Teilnehmer schreiben lassen (oder Abgabe importieren) und die Abgabe
   **autorisieren** — nicht autorisierte Abgaben werden nicht exportiert.
3. Service-Account anlegen (z. B. `ki_korrektor`, Klarname „KI-Vorschlag"),
   dem Objekt mit Leserechten zuweisen und im Korrektoren-Admin als
   Korrektor der Testabgabe zuweisen (z. B. als Erstkorrektur-Position).
   Die `user_id` des Accounts notieren.

### 3. Export → Korrektur → Import

```bash
BASE="https://ilias.example/.../endpoints/xlas_rest.php"

# Export der Bearbeitung als correction-request JSON
python3 scripts/export_item.py --base "$BASE" \
  --ass-id 1 --context-id 123 --user-id 6 \
  --provider-key "$XLAS_PROVIDER_KEY" \
  --task-id 1 --writer-id 5 -o request.json

# request.json an den Korrekturservice geben; dieser liefert
# suggestions.json im Format correction-suggestions/v0
# (zum Testen: examples/suggestions.example.json anpassen —
#  criterion_id-Werte und item aus request.json übernehmen)

# Import der Vorschläge (corrector_id steht in request.json unter provider)
python3 scripts/import_suggestions.py --base "$BASE" \
  --ass-id 1 --context-id 123 --user-id 6 --corrector-id 4 \
  --provider-key "$XLAS_PROVIDER_KEY" suggestions.json
```

### 4. Ergebnis prüfen

Als menschlicher Korrektor (Zweitposition) die Korrektur im Corrector öffnen:

- Mit `status: "pre_graded"` bleiben die KI-Vorschläge zunächst nur im
  Layer des KI-Korrektors und sind für andere erst nach dessen
  Autorisierung sichtbar (EDUTIEK-Standardverhalten: fremde Korrekturen
  zeigen sich erst ab Status „autorisiert" und bei aktivierter gegenseitiger
  Sichtbarkeit). Für die Demo daher entweder
  `status: "authorized"` importieren **oder** als Admin/Korrektur-Admin die
  Ansicht öffnen — dort sind alle Layer sichtbar.
- Erwartung: Randbemerkungen mit Markierung im Text, Teilpunkte an den
  Kriterien, Gesamtpunkte und Votum (mit Disclaimer) am KI-Korrektor-Layer;
  der menschliche Korrektor korrigiert unabhängig davon in seinem Layer.

## Bewusste Grenzen des PoC

- **Auth:** statischer Key aus einer Umgebungsvariable, kein Token-Rollover,
  kein Rate-Limit. Für eine echte Schnittstelle wäre z. B. OAuth2
  Client-Credentials pro Provider angemessen.
- **Push statt Pull:** EDUTIEK stößt den Export nicht selbst an; der
  Provider (oder ein Skript) pollt bzw. wird manuell gestartet.
- **PDF-Abgaben:** Export liefert nur den Hinweis `pdf_available`; Kommentar-
  Anker auf Seitenmarkierungen (`marks`) werden durchgereicht, aber nicht
  vom Beispiel-Workflow erzeugt.
- Keine UI-Kennzeichnung „KI" über den Namen des Service-Accounts und den
  Disclaimer im Votum hinaus.
