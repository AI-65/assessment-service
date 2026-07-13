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


def provider_get(route: str) -> dict:
    params = {
        "ass_id": cfg("XLAS_ASS_ID"),
        "context_id": cfg("XLAS_CONTEXT_ID"),
        "user_id": cfg("XLAS_USER_ID"),
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


def to_suggestions(request: dict, correction: dict) -> dict:
    """Map the LLM output to the correction-suggestions/v0 schema, clamping
    word positions and per-criterion point sums to valid ranges."""
    criteria = {c["id"]: c for c in request["criteria"]}
    max_word = correction.pop("_max_word", 0) or 10 ** 9
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
            "key": f"ai-c{i}",
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
                    {"key": f"ai-p{point_counter}", "criterion_id": point["criterion_id"], "points": value}
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
                {"key": f"ai-p{point_counter}", "criterion_id": point["criterion_id"], "points": value}
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
            # authorized so that the human corrector sees the suggestions
            "status": "authorized",
        },
    }


def correct_item(task_id: int, writer_id: int) -> None:
    base_args = [
        "--base", cfg("XLAS_BASE"),
        "--ass-id", cfg("XLAS_ASS_ID"),
        "--context-id", cfg("XLAS_CONTEXT_ID"),
        "--user-id", cfg("XLAS_USER_ID"),
        "--provider-key", cfg("XLAS_PROVIDER_KEY"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        request_file = Path(tmp) / "request.json"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "export_item.py"), *base_args,
             "--task-id", str(task_id), "--writer-id", str(writer_id),
             "-o", str(request_file)],
            check=True, capture_output=True, text=True,
        )
        request = json.loads(request_file.read_text())
        log(f"export ok: task {task_id}, writer {writer_id} ({request['item'].get('pseudonym')})")

        suggestions = to_suggestions(request, run_llm(request))
        suggestions_file = Path(tmp) / "suggestions.json"
        suggestions_file.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2))
        log(f"korrektur fertig: {len(suggestions['comments'])} Anmerkungen, "
            f"{suggestions['summary']['points']} Punkte")

        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "import_suggestions.py"), *base_args,
             "--corrector-id", str(request["provider"]["corrector_id"]),
             str(suggestions_file)],
            capture_output=True, text=True,
        )
        print(result.stdout, end="")
        if result.returncode != 0:
            raise RuntimeError(f"import failed: {result.stdout}{result.stderr}")
        log("import ok — Vorschläge sind im Corrector sichtbar")


def own_correction_exists(task_id: int, writer_id: int) -> bool:
    """True if the provider's corrector already has comments or points on the item."""
    item = provider_get(f"/provider/item/{task_id}/{writer_id}")
    task_data = item.get("Task") or {}
    corrector_id = None
    for corr in task_data.get("Corrections", []):
        if corr.get("user_id") == int(cfg("XLAS_USER_ID")):
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
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)

    if args.once:
        if args.task_id is None or args.writer_id is None:
            parser.error("--once requires --task-id and --writer-id")
        correct_item(args.task_id, args.writer_id)
    else:
        watch(args.interval)


if __name__ == "__main__":
    main()
