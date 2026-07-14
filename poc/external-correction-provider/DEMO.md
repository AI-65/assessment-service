# Demo-Drehbuch: EDUTIEK × KlausurenKiste (Uni Freiburg)

Testumgebung: ILIAS 10.9 mit LongEssayAssessment-Plugin (Fork) auf `ssh privat`.
Der Korrektur-Worker (`kk-worker.service`) läuft dauerhaft und korrigiert neue
Abgaben automatisch mit Claude Opus 4.8.

## Vorbereiteter Zustand

| Abgabe | Inhalt | Zeigt |
|---|---|---|
| **Teilnehmer 1** (writer1) | schwache Bearbeitung (Saldotheorie falsch) | KI als separater Erstkorrektur-Layer: 5 Anmerkungen inkl. Kardinalfehler, Votum, Note |
| **Wanda Writer** (writer2) | vollständiges Gutachten, 1.700 Wörter | **Beide Modi gleichzeitig:** KI-Layer (18,5 P, freigegeben) als Referenz **und** Karlas eigene Felder vorbefüllt als bearbeitbarer Entwurf (18 P, offen) — Nils' „Vorkorrektur zum Bearbeiten" |
| **Willem Writer** (writer3) | noch leer | Live-Durchlauf vor Publikum |

Logins (alle `http://localhost:8081`):

| Login | Passwort | Rolle |
|---|---|---|
| writer3 | WriterPoc2026 | Student (schreibt live) |
| root | IliasPoc2026 | Dozent/Prüfungsamt |
| korrektor2 | Korr2Poc2026 | Menschliche Korrektorin (Karla) |

## Vorbereitung (am Demo-Tag, ~10 Minuten vorher)

Abhaken in dieser Reihenfolge:

```bash
# 1. Tunnel öffnen — Terminal offen lassen!
ssh -L 8081:localhost:8081 -o ServerAliveInterval=30 privat

# 2. In diesem Terminal (jetzt auf dem Server): alles gesund?
sudo systemctl status kk-worker --no-pager | head -3   # → "active (running)"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081/login.php  # → 302

# 3. Live-Teil zurücksetzen (falls geprobt wurde)
~/kk-worker/reset_live.sh    # → "Live-Demo-Reset fertig"

# 4. Zweites Terminal: Worker-Log fürs Publikum
ssh privat 'journalctl -u kk-worker -f'
```

Browser vorbereiten:
- [ ] 3 Fenster/Profile: writer3, root, korrektor2 — **vorab einloggen** und je bis zur Testklausur klicken
- [ ] Als korrektor2 einmal Teilnehmer 1 öffnen und Doktorhut-Icon testen (Muskelgedächtnis)
- [ ] DevTools schließen, Bookmarkleiste/private Tabs weg, Bildschirmfreigabe testen

Wenn irgendetwas in Schritt 2 nicht stimmt → Abschnitt „Wenn etwas schiefgeht" unten.

## Drehbuch (15–20 Min)

Vokabular: durchgehend **„Vorkorrektur"** sagen — das ist Nils' eigenes Wort aus
dem Vorgespräch. Nicht „KI-Korrektur ersetzt", sondern „Vorkorrektur, die der
Korrektor prüft".

### Teil 1 — Einstieg: Vorkorrektur als eigener Layer (3 Min)

Als **korrektor2**: Testklausur → **Teilnehmer 1** öffnen.

- Blaue KI-Markierungen im Text, Anmerkungen mit Teilpunkten rechts
- Doktorhut-Icon links → „Korrektur von KI-Vorschlag (Erstkorrektur)": Gutachten
  mit Disclaimer, Punkte, Note
- Kernsatz: *„Die Vorkorrektur ist ein eigener Layer. Ihre Korrektorin sieht sie,
  aber ihre eigene Bewertung — die leeren Felder rechts — bleibt unabhängig. Der
  Mensch bleibt final verantwortlich."*
- Fachlich glänzen: Die KI hat den Kardinalfehler bei der Saldotheorie gefunden
  (arglistige Täuschung → keine Saldierung, BGH)

### Teil 2 — Nils' Wunsch: Vorkorrektur zum Bearbeiten (4 Min)

Noch als **korrektor2**: zurück zur Liste → **Wanda Writer** öffnen.

- Jetzt sind **Karlas eigene Felder vorbefüllt**: Anmerkungen, Teilpunkte,
  Votum-Entwurf — Status „offen", nichts ist freigegeben
- Live vormachen: eine KI-Anmerkung **editieren**, eine Punktzahl **ändern**,
  eine Anmerkung **löschen** — dann sagen: *„Das ist genau die Vorkorrektur aus
  unserem letzten Gespräch: Die Korrektorin übernimmt, ändert oder verwirft, und
  gibt am Ende selbst frei."*
- Beide Modi sind **derselbe Endpunkt, nur ein Parameter** — die Hochschule
  wählt den Workflow, nicht wir

### Teil 3 — Live-Durchlauf (6–8 Min)

1. **Fenster writer3 (Willem):** Testklausur → „Meine Aufgabe" → „Bearbeitung
   starten" → vorbereitetes Gutachten **einfügen** (Paste ist für die Demo per
   Einstellung erlaubt; erwähnenswert: im echten Klausurmodus blockiert EDUTIEK
   fremden Text — Klausurintegrität ist eingebaut) → abgeben
2. **Fenster root:** Tab **„Teilnahmeverwaltung"** → Zeile Willem → Aktionen-Dropdown
   → **„Abgabe freigeben"** → bestätigen. Dann Tab **„Korrekturverwaltung"** →
   Willem zuweisen: **KI-Vorschlag Korrekturservice** (Erstkorrektur) + **Karla
   Korrektor** (Zweitkorrektur).
   *„Die Hochschule behält die Kontrolle: Erst die Freigabe macht die Abgabe für
   die Vorkorrektur sichtbar — und sie entscheidet, wer korrigiert."*
3. **Worker-Terminal zeigen:** ≤ 15 s später „neue Abgabe gefunden", dann
   „korrigiere mit claude-opus-4-8", nach 30–60 s „import ok"
4. **Fenster korrektor2:** Liste neu laden → Willem öffnen → frische Vorkorrektur

### Teil 4 — Technik & offene Punkte (3 Min, ehrlich)

**Was der PoC belegt:**
- Fork-Umfang: 4 Dateien im assessment-service, **null Änderungen am ILIAS-Plugin**
- Datenschutz: Export enthält nur Pseudonyme (im request.json zeigbar); Verarbeitung
  DSGVO-konform gestaltbar (AVV, EU-Inferenz möglich)
- Skalierung: ~45 s pro Klausur, parallelisierbar → 220–240 Samstagsklausuren
  sind über Nacht durch (zum Vergleich: 12,50 € Korrektorhonorar pro Klausur)

**Offen benennen, bevor sie fragen:**
- **Erwartungshorizont/Kurs-Querschnitt** (Daniels' Punkt): Die KI korrigiert
  standardisiert nach Schema — genau deshalb Vorkorrektur, der Mensch kalibriert
  die Endnote. Querschnitts-Anpassung = Roadmap, nicht Demo
- **Alternative Lösungswege:** über die Korrekturanweisung abgedeckt — die KI darf
  vertretbare Ansätze außerhalb der Musterlösung honorieren (Wandas § 242-Abwägung
  als Beispiel)
- **Auth/Betrieb:** PoC-Key → OAuth2 im Pilotbetrieb; PDF-/Handschrift-Abgaben
  noch nicht angebunden

### Abschluss — das Angebot (1 Min)

*„Unser Vorschlag: kostenloser Pilot mit einem Klausurenkurs auf dieser Basis.
Freiburg hostet ILIAS mit dem Plugin-Fork wie jedes andere Plugin — der Unterschied
zum Standard ist eine einzige Composer-Zeile. Wir betreiben die KI-Seite, pflegen
den Fork und halten ihn mit den EDUTIEK-Releases synchron. Parallel bringen wir
die Schnittstelle als Vorlage ins Gespräch mit Herrn Reuschenbach — wenn EDUTIEK
sie offiziell übernimmt, migrieren wir, und der Fork verschwindet."*

## Strategie-Spickzettel (nicht zeigen — für Rückfragen)

- **„Ihr forkt ein fremdes System?"** — EDUTIEK ist GPL-3; Forks sind vorgesehen
  und legal. Unser Diff ist bewusst minimal (4 Dateien) und generisch benannt
  (`provider`, nicht `klausurenkiste`) — als Referenzimplementierung für die
  offene Schnittstelle, die EDUTIEK ohnehin plant. Wir bieten den Diff EDUTIEK
  als PR an.
- **„Was, wenn EDUTIEK ein Update bringt?"** — Wartungszusage im Pilotvertrag:
  Wir rebasen den Fork auf jedes Upstream-Release (kleiner Diff = kleiner
  Aufwand) und testen die Schnittstelle. Freiburgs Rückweg ist trivial:
  composer.json auf Upstream zurückstellen, fertig — kein Lock-in.
- **„Wer betreibt was?"** — Freiburg: ILIAS + Plugin-Fork (wie bisher, gleiche
  IT-Prozesse). KlausurenKiste: Worker + KI + Fork-Pflege. Klare Grenze, klare
  Verantwortung.
- **„Warum nicht auf Reuschenbach warten?"** — Nicht sicher, ob/wann die
  offizielle Schnittstelle kommt. Der Fork macht Freiburg jetzt handlungsfähig
  und ist gleichzeitig das stärkste Argument gegenüber EDUTIEK, die Schnittstelle
  zu priorisieren. Beide Wege führen zum selben Ziel.
- **Kosten:** Pilot kostenlos (im Vorgespräch zugesagt). Preismodell danach
  gemeinsam entwickeln — pro Korrektur deutlich unter dem Korrektorhonorar.

## Wenn etwas schiefgeht

| Problem | Lösung |
|---|---|
| Seite lädt nicht | Tunnel neu: `ssh -f -N -L 8081:localhost:8081 privat` |
| Worker reagiert nicht | `ssh privat 'journalctl -u kk-worker -n 30'`; dann `sudo systemctl restart kk-worker` |
| Live-Teil klemmt | Auf Teil 1 + 2 zurückfallen — Teilnehmer 1 und Wanda sind immer da |
| Live-Teil erneut proben | `ssh privat '~/kk-worker/reset_live.sh'` |
| Korrektur manuell anstoßen | `ssh privat 'cd ~/kk-worker && ./venv/bin/python scripts/kk_correct.py --env-file worker.env --once --task-id 1 --writer-id <ID>'` |
| Entwurfs-Modus manuell (in Karlas Felder) | wie oben, zusätzlich `--import-user-id 321 --import-corrector-id 2` |

**Wichtig:** `reset_writer2.sh` NICHT mehr benutzen — das würde das vorbereitete
Wanda-Beispiel (Teil 2) löschen. Für Proben nur `reset_live.sh` (writer3).
