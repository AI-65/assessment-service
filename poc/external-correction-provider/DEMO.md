# Demo-Drehbuch: EDUTIEK × KlausurenKiste (Uni Freiburg)

Testumgebung: ILIAS 10.9 mit LongEssayAssessment-Plugin (Fork) auf `ssh privat`,
erreichbar über SSH-Tunnel. Der Korrektur-Worker (`kk-worker.service`) läuft
dauerhaft und korrigiert neue Abgaben automatisch mit Claude Opus 4.8.

## Vorbereitung (5 Minuten vor der Demo)

```bash
# Tunnel öffnen (offen lassen!)
ssh -L 8081:localhost:8081 -o ServerAliveInterval=30 privat

# Auf dem Server: Worker läuft? Demo-Zustand zurücksetzen?
sudo systemctl status kk-worker --no-pager
~/kk-worker/reset_writer2.sh          # entfernt writer2-Abgabe für frischen Live-Durchlauf
```

Browser-Tabs vorbereiten (alle `http://localhost:8081`):

| Tab | Login | Passwort | Rolle in der Demo |
|---|---|---|---|
| 1 | writer2 | WriterPoc2026 | Studentin (schreibt live) |
| 2 | root | IliasPoc2026 | Dozent/Prüfungsamt (autorisiert + weist zu) |
| 3 | korrektor2 | Korr2Poc2026 | Menschliche Korrektorin |

Optional viertes Terminal für den Wow-Effekt:
```bash
ssh privat 'journalctl -u kk-worker -f'
```

## Teil 1 — Ausgangslage zeigen (2 Min)

Als **korrektor2**: Magazin → „Testklausur Zivilrecht (PoC)" → Abgabe von
„Teilnehmer 1" öffnen. Zeigen:

- Links der Klausurtext mit **blauen KI-Markierungen** (inkl. Kardinalfehler
  bei der Saldotheorie, „Exzellent" bei § 142 I BGB)
- Doktorhut-Icon → **„Korrektur von KI-Vorschlag (Erstkorrektur)"**: Gutachten
  mit Disclaimer, 11,5 Punkte, Teilpunkte je Kriterium
- Kernbotschaft: *Die KI ist ein eigener Korrektur-Layer. Die menschliche
  Korrektorin arbeitet unabhängig in ihrem Layer, kann übernehmen, ändern,
  ignorieren — sie bleibt final verantwortlich.*

## Teil 2 — Live-Durchlauf (5–8 Min)

1. **Tab 1 (writer2):** Klausur öffnen → „Bearbeitung starten" → 5–10 Sätze
   zum Fall schreiben (gern mit absichtlichem Fehler) → **abgeben**.
2. **Tab 2 (root):** Objekt → „Schreiberverwaltung" → Abgabe von Wanda Writer
   **autorisieren**. Dann „Korrekturverwaltung" → der Abgabe **KI-Vorschlag
   Korrekturservice** (Erstkorrektur) und **Karla Korrektor** (Zweitkorrektur)
   zuweisen.
   - *Erzähltext: „Die Hochschule behält die Kontrolle — erst die Autorisierung
     gibt die Abgabe frei, und sie entscheidet, wer korrigiert."*
3. **Terminal-Tab:** Der Worker erkennt die Abgabe beim nächsten Poll (≤ 15 s)
   und korrigiert — dauert **30–60 Sekunden**. Log zeigt: export → Korrektur →
   import.
4. **Tab 3 (korrektor2):** Korrekturliste neu laden → neue Abgabe öffnen →
   die frische KI-Korrektur ist da: Randbemerkungen, Teilpunkte, Votum.

## Teil 3 — Technik-Folie (2 Min)

- Fork-Umfang: **4 Dateien** im assessment-service, **null Änderungen** am
  ILIAS-Plugin — bewusst generisch als `provider`-Schnittstelle gebaut,
  taugt als Vorlage für die angekündigte offene EDUTIEK-API
- Datenfluss: EDUTIEK exportiert (Aufgabe, Musterlösung, Kriterien,
  Text mit Wortpositionen, nur Pseudonyme) → KlausurenKiste korrigiert →
  strukturierte Vorschläge zurück (JSON-Schemas versioniert)
- Offene Punkte ehrlich benennen: Auth (PoC-Key → OAuth2), PDF-Abgaben,
  Trigger (Polling → Push), Workflow-Frage „KI als Erstkorrektur vs.
  echter Vorschlagsmodus" → Entscheidung EDUTIEK/Reuschenbach

## Wenn etwas schiefgeht

| Problem | Lösung |
|---|---|
| Seite lädt nicht | Tunnel neu: `ssh -L 8081:localhost:8081 privat` |
| Worker korrigiert nicht | `ssh privat 'journalctl -u kk-worker -n 30'`; Neustart: `sudo systemctl restart kk-worker` |
| Live-Teil klemmt | Auf Teil 1 zurückfallen — die writer1-Korrektur ist immer da |
| Erneut proben | `~/kk-worker/reset_writer2.sh` |

Worker manuell für eine Abgabe starten (falls Watch klemmt):
```bash
ssh privat 'cd ~/kk-worker && ./venv/bin/python scripts/kk_correct.py \
  --env-file worker.env --once --task-id 1 --writer-id <ID>'
```
