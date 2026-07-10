#!/usr/bin/env python3
"""Export one submission (task + writer) from the assessment service as an
ExternalCorrectionRequest JSON bundle (schema/correction-request.schema.json).

Calls the provider REST endpoints of the PoC fork:
  GET {base}/provider/data
  GET {base}/provider/item/{task_id}/{writer_id}

Usage:
  export_item.py --base https://ilias.example/.../endpoints/xlas_rest.php \
      --ass-id 1 --context-id 123 --user-id 6 --provider-key SECRET \
      --task-id 1 --writer-id 5 [-o request.json]

--user-id is the ILIAS user id of the provider's service account. It must be
registered as a corrector of the assessment and be assigned to the item.
No external dependencies, Python 3.9+.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request


def get(base: str, route: str, params: dict, key: str) -> dict:
    url = base.rstrip('/') + route + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'X-Provider-Key': key})
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode('utf-8')
    if not body:
        return {}
    return json.loads(body)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, help='URL of xlas_rest.php')
    p.add_argument('--ass-id', type=int, required=True)
    p.add_argument('--context-id', type=int, required=True)
    p.add_argument('--user-id', type=int, required=True,
                   help='user id of the provider service account')
    p.add_argument('--provider-key', required=True)
    p.add_argument('--task-id', type=int, required=True)
    p.add_argument('--writer-id', type=int, required=True)
    p.add_argument('-o', '--output', default='-')
    args = p.parse_args()

    params = {
        'ass_id': args.ass_id,
        'context_id': args.context_id,
        'user_id': args.user_id,
    }

    data = get(args.base, '/provider/data', params, args.provider_key)
    item = get(args.base, f'/provider/item/{args.task_id}/{args.writer_id}',
               params, args.provider_key)

    task_data = data.get('Task', {}) or {}
    task_item = item.get('Task', {}) or {}
    essay_item = item.get('EssayTask', {}) or {}

    if not task_item.get('Item'):
        sys.exit('No item data returned. Is the provider user assigned as '
                 'corrector for this task/writer and is the writer authorized?')

    # find the provider's own corrector record among the corrections
    corrector_id = None
    position = None
    for corr in task_item.get('Corrections', []):
        if corr.get('user_id') == args.user_id:
            corrector_id = corr.get('corrector_id')
            position = corr.get('position')
            break
    if corrector_id is None:
        sys.exit('Provider user is not among the correctors of this item.')

    task_info = {}
    for task in task_data.get('Tasks', []):
        if task.get('task_id') == args.task_id:
            task_info = task
            break

    criteria = []
    for crit in task_item.get('Criteria', []):
        if crit.get('corrector_id') in (None, 0, corrector_id):
            criteria.append({
                'id': crit.get('id'),
                'title': crit.get('title'),
                'description': crit.get('description'),
                'max_points': crit.get('points'),
                'is_general': bool(crit.get('is_general')),
            })

    settings = task_data.get('Settings', {}) or {}
    essay = essay_item.get('Essay', {}) or {}

    request = {
        'schema': 'external-correction-request/v0',
        'assessment': {
            'ass_id': args.ass_id,
            'context_id': args.context_id,
        },
        'item': {
            'task_id': args.task_id,
            'writer_id': args.writer_id,
            'pseudonym': task_item.get('Item', {}).get('pseudonym'),
        },
        'provider': {
            'user_id': args.user_id,
            'corrector_id': corrector_id,
            'position': position,
        },
        'task': {
            'title': task_info.get('title'),
            'instructions': task_info.get('instructions'),
            'solution': task_info.get('solution'),
        },
        'essay': {
            'text': essay.get('text'),
            'pdf_available': bool(essay.get('pdf_version')),
        },
        'criteria': criteria,
        'settings': {
            'enable_comments': bool(settings.get('enable_comments')),
            'enable_comment_ratings': bool(settings.get('enable_comment_ratings')),
            'enable_partial_points': bool(settings.get('enable_partial_points')),
            'positive_rating': settings.get('positive_rating'),
            'negative_rating': settings.get('negative_rating'),
        },
    }

    out = json.dumps(request, ensure_ascii=False, indent=2)
    if args.output == '-':
        print(out)
    else:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(out + '\n')
        print(f'written: {args.output}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
