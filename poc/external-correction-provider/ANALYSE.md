# Technische Analyse: EDUTIEK-Codepfade für externe Korrektur-Provider

Stand: Juli 2026, `assessment-service` Branch `main`, Plugin `LongEssayAssessment` Branch `release_10` (Version 10.1, ILIAS 10). Das Plugin bindet den Service über Composer ein (`edutiek/assessment-service: dev-main`).

## Architektur in Kürze

```
ILIAS-Plugin (LongEssayAssessment)          assessment-service (Library)
┌─────────────────────────────┐             ┌──────────────────────────────┐
│ endpoints/xlas_rest.php     │──handle()──▶│ Assessment/Apps/Service      │
│ (einziger REST-Einstieg)    │             │  → AppWriter / AppCorrector  │
│ src/…/Rest/RestContext.php  │◀─Interface──│    (Slim-Routen)             │
│ src/Task/Data/*Repo.php     │◀─Interface──│  → AppBridges pro Komponente │
│ (ILIAS-DB-Persistenz)       │             │    (Assessment/Task/EssayTask)│
└─────────────────────────────┘             └──────────────────────────────┘
```

- Alle REST-Calls der Writer-/Corrector-Web-Apps laufen über **eine** Datei: `endpoints/xlas_rest.php`. Query-Parameter `ass_id`, `context_id`, `user_id`, `signature`; die Route (`PATH_INFO`, z. B. `/corrector/data`) bestimmt per `Frontend::fromRoutePart()` das App-Objekt (`Apps/Service::initForRestCall`).
- Die Persistenz (Repos) implementiert das Plugin gegen die abstrakten Entities der Library (z. B. `src/Task/Data/CorrectorCommentRepo.php` im Plugin ↔ `Task\Data\CorrectorComment` in der Library).

Hinweis: Die im Auftrag genannten Pfade stimmen teilweise nicht mehr — `AppCorrector.php` liegt unter `src/Assessment/Apps/`, die Corrector-Bridge unter `src/Task/AppBridges/CorrectorBridge.php` (nicht `Assessment/Task/…`). Zusätzlich gibt es Corrector-Bridges in `src/Assessment/AppBridges/` und `src/EssayTask/AppBridges/` — pro Komponente eine.

## 1. Wie werden Kommentare persistiert?

`Task\AppBridges\CorrectorBridge::applyComment()` (aufgerufen über `PUT /corrector/changes`, Change-Typ `comment`):

- Entity `Task\Data\CorrectorComment`: `key` (App-generierter String-Schlüssel), `task_id`, `writer_id`, `corrector_id`, `start_position`, `end_position` (**Wort-Positionen** im Essay-Text), `parent_number` (Absatz), `comment`, `rating` (`''` | `cardinal` | `excellent`, siehe `EssayTask\Data\CommentRating`), `marks` (JSON, für PDF-/Bildkorrektur).
- Upsert per `(task_id, writer_id, key)`; Scope-Check: `corrector_id` muss der Korrektor des eingeloggten Users sein **und** ein `CorrectorAssignment` für (writer, corrector, task) existieren.

## 2. Wie werden Punkte persistiert?

`applyPoints()`, Change-Typ `points`, Entity `Task\Data\CorrectorPoints`: `key`, `task_id`, `writer_id`, `corrector_id`, `criterion_id`, `comment_id`, `points`.

- Punkte hängen **entweder** an einem Kommentar (`comment_key` → nicht-generelles Kriterium) **oder** direkt an einem generellen Kriterium (`RatingCriterion::getGeneral()`), sonst Ablehnung.
- Kriterien (`Task\Data\RatingCriterion`) sind je Task und optional je Korrektor definiert (`CriteriaMode`).
- Nur wirksam, wenn `enable_partial_points` in den Korrektureinstellungen aktiv ist.

## 3. Wie wird Summary/Votum persistiert?

`applySummary()`, Change-Typ `summary`, Entity `Task\Data\CorrectorSummary`: `summary_text`, `points` (Gesamtpunkte), `summary_pdf`, `grading_status`, Revisionsfelder.

- Gespeichert über `Task\CorrectionProcess\Service::checkAndSaveSummary()` — dort laufen die **Workflow-Checks**: Statuswechsel (`not_started/open/pre_graded/authorized/revised`, Enum `GradingStatus`), Autorisierungsrechte, Stitching/Zweitkorrektur-Logik, Events und Benachrichtigungen.
- Wichtig für Sichtbarkeit: Kommentare/Punkte/Summary eines Korrektors sehen andere Korrektoren erst, wenn dessen Summary **authorized** ist (`CorrectorBridge::getItem`, `$is_corrector || $summary->isAuthorized()`), und generell nur bei aktivierter `mutual_visibility` in den Assessment-Einstellungen (Admins sehen immer alles).

## 4. Kann man einen zusätzlichen Korrektor sauber modellieren? → Ja

- `Assessment\Data\Corrector` ist nur ein Mapping `user_id ↔ ass_id`; `Corrector\Service::getByUserId()` legt den Datensatz bei Bedarf automatisch an.
- Zuweisungen (`Task\Data\CorrectorAssignment`: writer × corrector × task × position) werden im Plugin-Admin-UI („Korrektorenzuweisung") gepflegt, inkl. Positionen (Erst-/Zweitkorrektur).
- **Ein ILIAS-Service-Account („KI-Korrektor") + Corrector-Datensatz + Assignments = vollwertiger Korrekturlayer.** Sämtliche Scope-, Workflow- und Sichtbarkeitsregeln greifen dann unverändert — genau das Verhalten, das für „Vorschläge, die der Mensch prüft" gewünscht ist (Variante B des Auftrags).

## 5. Welche IDs sind zwingend erforderlich?

| ID | Herkunft | Zweck |
|---|---|---|
| `ass_id` | Query-Param jedes REST-Calls | Assessment in der Library |
| `context_id` | Query-Param | ILIAS ref_id des Objekts |
| `user_id` | Query-Param | ILIAS-User (hier: Service-Account) |
| `task_id`, `writer_id` | Route/Payload | Identifiziert die Bearbeitung |
| `corrector_id` | aus Corrector-Datensatz des Users | in jedem Change-Payload |
| `criterion_id` | Export der Kriterien | für Punkte |
| `key` (String) | vom Client frei vergeben | Upsert-Schlüssel für Kommentare/Punkte |

## 6. Wie wird Auth aktuell gehandhabt?

- Beim Öffnen der Web-App erzeugt ILIAS ein Token (`Assessment\Authentication\Service`, Zufallsstring, per DB-Tabelle an `user_id`+`ass_id`+Purpose gebunden, IP wird gespeichert) und übergibt es der App.
- Jeder REST-Call sendet `signature = md5(user_id . ass_id . context_id . token)`; geprüft in `RestHelper::checkAuth()`. Zusätzlich `RestHelper::checkAccess()`: User muss existieren, das Objekt online sein und der User ILIAS-Lese-Rechte haben (`Permissions::canDoRestCall()`).
- **Keine externe API, keine API-Keys** — Tokens entstehen nur über interaktive ILIAS-Sessions. Für einen externen Provider muss man daher entweder Tokens serverseitig ausstellen oder (PoC) einen statischen Key prüfen.

## 7. Kleinster sauberer Hook für Import/Export? → Neue REST-„Frontend"-Route

Der Dispatcher (`Apps/Service::getApp`) mappt den ersten Routen-Teil auf ein App-Objekt. Der kleinste Eingriff ist ein dritter Fall neben `writer`/`corrector`:

- `Frontend::PROVIDER = 'provider'` (Enum-Case),
- `AppProvider extends AppCorrector` — erbt `getData`/`getItem`/`putChanges` samt aller Bridge-, Scope- und Workflow-Logik, registriert nur `/provider/*`-Routen und ersetzt die Token-Auth durch einen statischen Provider-Key,
- Factory-Methode `Internal::appProvider()`.

**Im Plugin ist keine Code-Änderung nötig** — `xlas_rest.php` reicht alles durch. Damit sind Export (GET) und Import (PUT) über exakt dieselben Datenstrukturen möglich, die auch die Corrector-App nutzt: keine neue Persistenzlogik, keine Spezial-Schnittstelle für einen Anbieter.
