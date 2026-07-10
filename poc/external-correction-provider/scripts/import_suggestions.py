#!/usr/bin/env python3
"""Import ExternalCorrectionSuggestions JSON
(schema/correction-suggestions.schema.json) into the assessment service.

Maps the suggestions to the change format of the corrector app and sends it
via PUT {base}/provider/changes. Everything is stored under the provider's
own corrector record; the scope checks of the corrector bridge enforce this.

Usage:
  import_suggestions.py --base https://ilias.example/.../endpoints/xlas_rest.php \
      --ass-id 1 --context-id 123 --user-id 6 --corrector-id 4 \
      --provider-key SECRET suggestions.json

--user-id / --corrector-id are the provider service account and its corrector
record (both contained in the exported correction request under 'provider').
No external dependencies, Python 3.9+.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request


def put_changes(base: str, params: dict, key: str, body: dict) -> dict:
    url = base.rstrip('/') + '/provider/changes?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        method='PUT',
        headers={'X-Provider-Key': key, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, help='URL of xlas_rest.php')
    p.add_argument('--ass-id', type=int, required=True)
    p.add_argument('--context-id', type=int, required=True)
    p.add_argument('--user-id', type=int, required=True)
    p.add_argument('--corrector-id', type=int, required=True)
    p.add_argument('--provider-key', required=True)
    p.add_argument('suggestions', help='suggestions JSON file, or - for stdin')
    args = p.parse_args()

    if args.suggestions == '-':
        suggestions = json.load(sys.stdin)
    else:
        with open(args.suggestions, encoding='utf-8') as f:
            suggestions = json.load(f)

    if suggestions.get('schema') != 'external-correction-suggestions/v0':
        sys.exit('Unsupported schema: ' + str(suggestions.get('schema')))

    task_id = suggestions['item']['task_id']
    writer_id = suggestions['item']['writer_id']
    now = int(time.time())

    ids = {
        'task_id': task_id,
        'writer_id': writer_id,
        'corrector_id': args.corrector_id,
    }

    comment_changes = []
    points_changes = []

    for comment in suggestions.get('comments', []):
        comment_changes.append({
            'type': 'comment',
            'key': comment['key'],
            'last_change': now,
            'action': 'save',
            'payload': ids | {
                'start_position': comment['start_position'],
                'end_position': comment['end_position'],
                'parent_number': comment.get('parent_number') or 0,
                'comment': comment['comment'],
                'rating': comment.get('rating'),
                'marks': comment.get('marks'),
            },
        })
        for point in comment.get('points', []):
            points_changes.append({
                'type': 'points',
                'key': point['key'],
                'last_change': now,
                'action': 'save',
                'payload': ids | {
                    'comment_key': comment['key'],
                    'criterion_id': point['criterion_id'],
                    'points': point['points'],
                },
            })

    for point in suggestions.get('criterion_points', []):
        points_changes.append({
            'type': 'points',
            'key': point['key'],
            'last_change': now,
            'action': 'save',
            'payload': ids | {
                'comment_key': None,
                'criterion_id': point['criterion_id'],
                'points': point['points'],
            },
        })

    summary_changes = []
    summary = suggestions.get('summary')
    if summary:
        text = summary.get('text', '')
        disclaimer = (suggestions.get('provider') or {}).get('disclaimer')
        if disclaimer:
            text = f'<p><em>{disclaimer}</em></p>\n{text}'
        summary_changes.append({
            'type': 'summary',
            'key': f'summary-{task_id}-{writer_id}-{args.corrector_id}',
            'last_change': now,
            'action': 'save',
            'payload': ids | {
                'text': text,
                'points': summary.get('points'),
                'status': summary.get('status', 'pre_graded'),
                'last_change': now,
            },
        })

    params = {
        'ass_id': args.ass_id,
        'context_id': args.context_id,
        'user_id': args.user_id,
    }

    ok = True
    # comments must exist before points can reference them; the summary
    # status check may depend on existing points, so send it last
    for label, changes in (('comment', comment_changes),
                           ('points', points_changes),
                           ('summary', summary_changes)):
        if not changes:
            continue
        result = put_changes(args.base, params, args.provider_key,
                             {'Task': {label: changes}})
        for entry in (result.get('Task', {}).get(label) or []):
            done = bool(entry.get('done'))
            status = 'ok' if done else 'FAILED'
            info = entry.get('result') or ''
            print(f"{label} {entry.get('key')}: {status} {info}".rstrip())
            ok = ok and done

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
