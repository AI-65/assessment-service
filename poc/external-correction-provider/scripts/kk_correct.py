#!/usr/bin/env python3
"""KlausurenKiste correction worker (PoC).

Fetches a submission from the EDUTIEK provider endpoint, has an LLM produce
a full correction (marginal comments anchored to word positions, points per
criterion, overall verdict) and imports it back as suggestions of the AI
corrector. One shot or as a polling worker:

  kk_correct.py --once --task-id 1 --writer-id 1     correct one submission
  kk_correct.py --watch                              poll for new authorized
                                                     submissions and correct
                                                     everything not yet done

Connection settings come from the environment (or a .env-style file passed
via --env-file): XLAS_BASE, XLAS_ASS_ID, XLAS_CONTEXT_ID, XLAS_USER_ID,
XLAS_PROVIDER_KEY, ANTHROPIC_API_KEY.

Requires: pip install anthropic (Python 3.9+).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from anthropic import Anthropic

SCRIPTS = Path(__file__).resolve().parent

MODEL = os.environ.get("KK_MODEL", "claude-opus-4-8")

DISCLAIMER = "KI-generierter Korrekturvorschlag – bitte prüfen und ggf. anpassen."

SYSTEM_PROMPT = """Du bist erfahrener Korrektor juristischer Klausuren (Korrekturassistent von KlausurenKiste).
Du korrigierst streng, aber fair und wohlwollend im Zweifel, wie ein Prüfer im Staatsexamen.

Du erhältst: Aufgabenstellung, Lösungsskizze, Bewertungskriterien mit Maximalpunkten,
und die Bearbeitung. Im Bearbeitungstext ist jedes Wort mit ⟦N⟧-Markern nummeriert
(der Marker steht VOR dem jeweiligen Wort, nicht jedes Wort ist markiert — zähle von
den Markern aus weiter).

Erstelle eine vollständige Korrektur:

1. RANDBEMERKUNGEN (comments): 3 bis 8 Anmerkungen zu konkreten Textstellen.
   - start_word/end_word: Wortnummern der kommentierten Passage (end_word >= start_word).
   - rating: "cardinal" für schwere Fehler (Kardinalfehler), "excellent" für
     herausragende Passagen, sonst "none".
   - Zu jeder Anmerkung, die ein NICHT-generelles Kriterium betrifft, vergib dort
     Teilpunkte (points-Array der Anmerkung, criterion_id aus der Kriterienliste).
     Die Summe der Teilpunkte je Kriterium darf dessen Maximum nicht überschreiten.
2. KOPFNOTEN (criterion_points): Punkte für die GENERELLEN Kriterien (im Kriterien-
   katalog mit [generell] markiert) — diese hängen nicht an Anmerkungen.
3. GESAMTVOTUM (summary.text): 3-6 Sätze Gutachtenstil-Feedback: Stärken, Schwächen,
   Gesamteindruck. Kein HTML außer <p>-Absätzen.

Sei konkret: zitiere oder benenne die Textstelle, nenne die Norm oder das Problem.
Vergib Punkte nachvollziehbar relativ zu den Maxima der Kriterien."""

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["comments", "criterion_points", "summary"],
    "properties": {
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start_word", "end_word", "comment", "rating", "points"],
                "properties": {
                    "start_word": {"type": "integer"},
                    "end_word": {"type": "integer"},
                    "comment": {"type": "string"},
                    "rating": {"type": "string", "enum": ["cardinal", "excellent", "none"]},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["criterion_id", "points"],
                            "properties": {
                                "criterion_id": {"type": "integer"},
                                "points": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
        "criterion_points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "points"],
                "properties": {
                    "criterion_id": {"type": "integer"},
                    "points": {"type": "number"},
                },
            },
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "points"],
            "properties": {
                "text": {"type": "string"},
                "points": {"type": "number"},
            },
        },
    },
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_env_file(path: str) -> None:
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cfg(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing environment variable: {name}")
    return value


def provider_get(route: str, user_id: "str | None" = None) -> dict:
    params = {
        "ass_id": cfg("XLAS_ASS_ID"),
        "context_id": cfg("XLAS_CONTEXT_ID"),
        "user_id": user_id or cfg("XLAS_USER_ID"),
    }
    url = cfg("XLAS_BASE").rstrip("/") + route + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Provider-Key": cfg("XLAS_PROVIDER_KEY")})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", text).strip()


def numbered_essay(essay_html: str) -> "tuple[str, int]":
    """Convert the EDUTIEK essay HTML (every word wrapped in <w-p w=N>) into
    plain text with a ⟦N⟧ marker before every 5th word. Returns (text, max_word)."""
    words = re.findall(r'<w-p\s+[^>]*w="(\d+)"[^>]*>(.*?)</w-p>', essay_html or "", re.S)
    if not words:
        return strip_html(essay_html), 0
    parts = []
    max_word = 0
    for number_str, raw in words:
        number = int(number_str)
        max_word = max(max_word, number)
        word = strip_html(raw)
        if number == 1 or number % 5 == 0:
            parts.append(f"⟦{number}⟧{word}")
        else:
            parts.append(word)
    return " ".join(parts), max_word


def build_user_prompt(request: dict) -> "tuple[str, int]":
    task = request["task"]
    criteria_lines = []
    for crit in request["criteria"]:
        general = " [generell]" if crit["is_general"] else ""
        desc = f" — {crit['description']}" if crit.get("description") else ""
        criteria_lines.append(
            f"- id {crit['id']}: {crit['title']}{general}, max. {crit['max_points']} Punkte{desc}"
        )
    essay_text, max_word = numbered_essay(request["essay"]["text"] or "")
    prompt = f"""# Aufgabenstellung
{strip_html(task.get('instructions') or '')}

# Lösungsskizze
{strip_html(task.get('solution') or '(keine hinterlegt)')}

# Bewertungskriterien
{chr(10).join(criteria_lines)}

# Bearbeitung ({request['item'].get('pseudonym', 'anonym')}, Wörter 1–{max_word})
{essay_text}"""
    return prompt, max_word


def run_llm(request: dict) -> dict:
    prompt, max_word = build_user_prompt(request)
    client = Anthropic()
    log(f"korrigiere mit {MODEL} ({max_word} Wörter) ...")
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the request")
    text = "".join(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    result["_max_word"] = max_word
    return result


def to_suggestions(request: dict, correction: dict, summary_status: str = "authorized",
                   key_prefix: str = "ai") -> dict:
    """Map the LLM output to the correction-suggestions/v0 schema, clamping
    word positions and per-criterion point sums to valid ranges."""
    criteria = {c["id"]: c for c in request["criteria"]}
    max_word = correction.get("_max_word", 0) or 10 ** 9
    spent: dict[int, float] = {}

    def clamp_points(criterion_id: int, points: float) -> "float | None":
        crit = criteria.get(criterion_id)
        if crit is None:
            return None
        room = crit["max_points"] - spent.get(criterion_id, 0.0)
        value = max(0.0, min(float(points), room))
        if value <= 0:
            return None
        spent[criterion_id] = spent.get(criterion_id, 0.0) + value
        return value

    comments = []
    point_counter = 0
    for i, comment in enumerate(correction.get("comments", []), start=1):
        start = max(1, min(int(comment["start_word"]), max_word))
        end = max(start, min(int(comment["end_word"]), max_word))
        entry = {
            "key": f"{key_prefix}-c{i}",
            "start_position": start,
            "end_position": end,
            "comment": comment["comment"],
            "rating": None if comment["rating"] == "none" else comment["rating"],
            "points": [],
        }
        for point in comment.get("points", []):
            crit = criteria.get(point["criterion_id"])
            if crit is None or crit["is_general"]:
                continue  # general criteria points may not attach to comments
            value = clamp_points(point["criterion_id"], point["points"])
            if value is not None:
                point_counter += 1
                entry["points"].append(
                    {"key": f"{key_prefix}-p{point_counter}", "criterion_id": point["criterion_id"], "points": value}
                )
        comments.append(entry)

    criterion_points = []
    for point in correction.get("criterion_points", []):
        crit = criteria.get(point["criterion_id"])
        if crit is None or not crit["is_general"]:
            continue  # non-general criteria need a comment
        value = clamp_points(point["criterion_id"], point["points"])
        if value is not None:
            point_counter += 1
            criterion_points.append(
                {"key": f"{key_prefix}-p{point_counter}", "criterion_id": point["criterion_id"], "points": value}
            )

    total = round(sum(spent.values()), 2)
    return {
        "schema": "external-correction-suggestions/v0",
        "item": {"task_id": request["item"]["task_id"], "writer_id": request["item"]["writer_id"]},
        "provider": {"name": "KlausurenKiste", "model": MODEL, "disclaimer": DISCLAIMER},
        "comments": comments,
        "criterion_points": criterion_points,
        "summary": {
            "text": correction["summary"]["text"],
            "points": total,
            # authorized: visible to other correctors as a separate AI layer;
            # open: stays an editable draft (used by the draft import mode)
            "status": summary_status,
        },
    }


USAGE_LOG = Path(os.environ.get("KK_USAGE_LOG", str(Path.home() / "kk-worker" / "usage.jsonl")))


def log_usage(record: dict) -> None:
    """Append one billing record per corrected submission (JSONL)."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log(f"WARNUNG: usage log fehlgeschlagen: {exc}")


def correct_item(task_id: int, writer_id: int,
                 import_user_id: "int | None" = None,
                 import_corrector_id: "int | None" = None,
                 summary_status: str = "authorized") -> None:
    """Correct one submission. By default the suggestions are stored under the
    provider's own corrector (separate AI layer, authorized). If
    import_user_id/import_corrector_id are given, they are written as an
    editable DRAFT into that (human) corrector's own layer instead."""
    common = [
        "--base", cfg("XLAS_BASE"),
        "--ass-id", cfg("XLAS_ASS_ID"),
        "--context-id", cfg("XLAS_CONTEXT_ID"),
        "--provider-key", cfg("XLAS_PROVIDER_KEY"),
    ]
    export_args = [*common, "--user-id", cfg("XLAS_USER_ID")]
    with tempfile.TemporaryDirectory() as tmp:
        request_file = Path(tmp) / "request.json"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "export_item.py"), *export_args,
             "--task-id", str(task_id), "--writer-id", str(writer_id),
             "-o", str(request_file)],
            check=True, capture_output=True, text=True,
        )
        request = json.loads(request_file.read_text())
        log(f"export ok: task {task_id}, writer {writer_id} ({request['item'].get('pseudonym')})")

        correction = run_llm(request)

        # import targets: (user_id, corrector_id, status, key_prefix, label)
        targets = []
        if import_user_id is not None:
            targets.append((import_user_id, import_corrector_id, summary_status,
                            f"ai-k{import_corrector_id}", "Entwurf"))
        else:
            # pre-fill a human corrector's own fields with an editable draft
            # (KK_DRAFT_USER_ID/KK_DRAFT_CORRECTOR_ID); with KK_DRAFT_ONLY=1
            # this replaces the separate AI reference layer entirely
            draft_user = os.environ.get("KK_DRAFT_USER_ID")
            draft_corr = os.environ.get("KK_DRAFT_CORRECTOR_ID")
            draft_only = os.environ.get("KK_DRAFT_ONLY") == "1" and draft_user and draft_corr
            if not draft_only:
                targets.append((int(cfg("XLAS_USER_ID")), request["provider"]["corrector_id"],
                                summary_status, "ai", "KI-Layer"))
            if draft_user and draft_corr:
                targets.append((int(draft_user), int(draft_corr), "open",
                                f"ai-k{draft_corr}", "Entwurf"))

        first = True
        for user_id, corrector_id, status, prefix, label in targets:
            suggestions = to_suggestions(request, correction, status, prefix)
            if first:
                log(f"korrektur fertig: {len(suggestions['comments'])} Anmerkungen, "
                    f"{suggestions['summary']['points']} Punkte")
                first = False
            suggestions_file = Path(tmp) / f"suggestions-{corrector_id}.json"
            suggestions_file.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_suggestions.py"), *common,
                 "--user-id", str(user_id), "--corrector-id", str(corrector_id),
                 str(suggestions_file)],
                capture_output=True, text=True,
            )
            print(result.stdout, end="")
            if result.returncode != 0:
                raise RuntimeError(f"import failed ({label}): {result.stdout}{result.stderr}")
            log(f"import ok ({label}, Korrektor {corrector_id})")

        log_usage({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ass_id": int(cfg("XLAS_ASS_ID")),
            "task_id": task_id,
            "writer_id": writer_id,
            "pseudonym": request["item"].get("pseudonym"),
            "words": len(re.sub(r"<[^>]+>", " ", request["essay"]["text"] or "").split()),
            "model": MODEL,
            "comments": len(suggestions["comments"]),
            "points_total": suggestions["summary"]["points"],
            "targets": [t[4] for t in targets],
        })


def own_correction_exists(task_id: int, writer_id: int) -> bool:
    """True if the provider's corrector already has comments or points on the item."""
    # check as the corrector who receives the import: unauthorized drafts are
    # only visible to their own corrector, so the item must be fetched with
    # that user's id (draft mode: the human corrector, else the provider)
    if os.environ.get("KK_DRAFT_ONLY") == "1" and os.environ.get("KK_DRAFT_USER_ID"):
        check_user = int(os.environ["KK_DRAFT_USER_ID"])
    else:
        check_user = int(cfg("XLAS_USER_ID"))
    item = provider_get(f"/provider/item/{task_id}/{writer_id}", str(check_user))
    task_data = item.get("Task") or {}
    corrector_id = None
    for corr in task_data.get("Corrections", []):
        if corr.get("user_id") == check_user:
            corrector_id = corr.get("corrector_id")
    if corrector_id is None:
        return True  # not assigned -> nothing to do
    if any(c.get("corrector_id") == corrector_id for c in task_data.get("Comments", [])):
        return True
    for summary in task_data.get("Summaries", []):
        if summary.get("corrector_id") == corrector_id and summary.get("points") is not None:
            return True
    return False


def watch(interval: int) -> None:
    log(f"watch-Modus: prüfe alle {interval}s auf neue Abgaben ...")
    failed: dict = {}
    while True:
        try:
            data = provider_get("/provider/data")
            items = (data.get("Task") or {}).get("Items", [])
            for item in items:
                key = (item["task_id"], item["writer_id"])
                if not item.get("can_correct"):
                    continue
                if failed.get(key, 0) >= 3:
                    continue
                if own_correction_exists(*key):
                    continue
                log(f"neue Abgabe gefunden: task {key[0]}, writer {key[1]}")
                try:
                    correct_item(*key)
                except Exception as exc:  # keep watching after a failure
                    failed[key] = failed.get(key, 0) + 1
                    log(f"FEHLER bei {key}: {exc}")
        except Exception as exc:
            log(f"FEHLER beim Abruf: {exc}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help=".env-style file with the XLAS_*/ANTHROPIC_* variables")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="correct one submission and exit")
    mode.add_argument("--watch", action="store_true", help="poll for new submissions")
    parser.add_argument("--task-id", type=int, help="required with --once")
    parser.add_argument("--writer-id", type=int, help="required with --once")
    parser.add_argument("--interval", type=int, default=15, help="watch poll interval seconds")
    parser.add_argument("--import-user-id", type=int,
                        help="draft mode: ILIAS user id of the HUMAN corrector to receive the suggestions as editable draft")
    parser.add_argument("--import-corrector-id", type=int,
                        help="draft mode: corrector id belonging to --import-user-id")
    parser.add_argument("--summary-status", default=None, choices=["open", "pre_graded", "authorized"],
                        help="grading status for the imported summary (default: authorized; draft mode default: open)")
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)

    draft = args.import_user_id is not None
    if draft != (args.import_corrector_id is not None):
        parser.error("--import-user-id and --import-corrector-id must be used together")
    status = args.summary_status or ("open" if draft else "authorized")

    if args.once:
        if args.task_id is None or args.writer_id is None:
            parser.error("--once requires --task-id and --writer-id")
        correct_item(args.task_id, args.writer_id,
                     args.import_user_id, args.import_corrector_id, status)
    else:
        watch(args.interval)


if __name__ == "__main__":
    main()
