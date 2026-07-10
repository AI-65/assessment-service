#!/usr/bin/env python3
"""End-to-end smoke test for the provider PoC (no ILIAS needed).

1. Runs smoke.php auth (provider key checks against the real AppProvider).
2. Starts a local mock server, runs scripts/import_suggestions.py with
   examples/suggestions.example.json against it and captures the PUT bodies.
3. Pipes each captured body into smoke.php changes, where the real
   BaseApp::putChanges parses it and a recording bridge captures the
   resulting ChangeRequests.
4. Asserts that comments, points and summary arrive with the right keys,
   actions and payload fields.

Usage: python3 run_smoke.py   (from anywhere; requires php + composer install)
"""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
POC = BASE.parent
KEY = 'smoke-test-provider-key'

captured = []


class Handler(BaseHTTPRequestHandler):
    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        captured.append(body)
        # acknowledge every change as done, like the real endpoint would
        response = {}
        for component, types in body.items():
            response[component] = {
                label: [{'type': label, 'key': c['key'], 'action': 'save',
                         'done': True, 'result': None} for c in changes]
                for label, changes in types.items()
            }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def fail(msg):
    print(f'FAIL {msg}')
    sys.exit(1)


def ok(msg):
    print(f'PASS {msg}')


def main():
    # 1. auth checks
    result = subprocess.run(['php', str(POC / 'tests' / 'smoke.php'), 'auth'])
    if result.returncode != 0:
        fail('smoke.php auth')
    ok('smoke.php auth')

    # 2. capture what import_suggestions.py sends
    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{server.server_port}/xlas_rest.php'

    result = subprocess.run([
        sys.executable, str(POC / 'scripts' / 'import_suggestions.py'),
        '--base', base_url, '--ass-id', '1', '--context-id', '123',
        '--user-id', '6', '--corrector-id', '4', '--provider-key', KEY,
        str(POC / 'examples' / 'suggestions.example.json'),
    ], capture_output=True, text=True)
    server.shutdown()
    if result.returncode != 0:
        fail(f'import_suggestions.py: {result.stdout}{result.stderr}')
    ok(f'import_suggestions.py sent {len(captured)} change requests')

    # 3. feed each captured body through the real putChanges
    applied = {}
    for body in captured:
        result = subprocess.run(
            ['php', str(POC / 'tests' / 'smoke.php'), 'changes'],
            input=json.dumps(body), capture_output=True, text=True)
        if result.returncode != 0:
            fail(f'smoke.php changes: {result.stderr}')
        out = json.loads(result.stdout)
        if out['status'] != 200:
            fail(f"putChanges returned {out['status']}")
        for change_type, changes in out['applied'].items():
            applied.setdefault(change_type, []).extend(changes)
        # every change must be acknowledged as done by the bridge
        for label, responses in out['response'].get('Task', {}).items():
            for entry in responses:
                if not entry.get('done'):
                    fail(f"change {label}/{entry.get('key')} not done")
    ok('putChanges parsed all bodies with status 200')

    # 4. content assertions against the example file
    example = json.loads((POC / 'examples' / 'suggestions.example.json').read_text())

    comments = applied.get('comment', [])
    if {c['key'] for c in comments} != {c['key'] for c in example['comments']}:
        fail(f'comment keys mismatch: {comments}')
    for c in comments:
        p = c['payload']
        if c['action'] != 'save':
            fail(f"comment {c['key']}: action {c['action']}")
        if p['task_id'] != 1 or p['writer_id'] != 5 or p['corrector_id'] != 4:
            fail(f"comment {c['key']}: wrong ids {p}")
        if not isinstance(p['start_position'], int) or not p['comment']:
            fail(f"comment {c['key']}: bad payload {p}")
    ok(f'{len(comments)} comments arrived with correct ids and positions')

    points = applied.get('points', [])
    expected_points = {p['key'] for c in example['comments'] for p in c.get('points', [])}
    expected_points |= {p['key'] for p in example.get('criterion_points', [])}
    if {p['key'] for p in points} != expected_points:
        fail(f'points keys mismatch: {points}')
    by_key = {p['key']: p['payload'] for p in points}
    if by_key['ai-p1']['comment_key'] != 'ai-c1':
        fail('ai-p1 not linked to comment ai-c1')
    if by_key['ai-p3']['comment_key'] is not None:
        fail('general criterion points must not have a comment_key')
    if by_key['ai-p3']['criterion_id'] != 7 or by_key['ai-p3']['points'] != 1.5:
        fail(f"ai-p3 payload wrong: {by_key['ai-p3']}")
    ok(f'{len(points)} points arrived, comment linkage and criteria correct')

    summaries = applied.get('summary', [])
    if len(summaries) != 1:
        fail(f'expected one summary, got {summaries}')
    s = summaries[0]['payload']
    if s['points'] != example['summary']['points'] or s['status'] != 'pre_graded':
        fail(f'summary payload wrong: {s}')
    if example['provider']['disclaimer'] not in s['text']:
        fail('disclaimer missing in summary text')
    if example['summary']['text'] not in s['text']:
        fail('summary text missing')
    ok('summary arrived with points, status and disclaimer')

    print('\nall smoke tests passed')


if __name__ == '__main__':
    main()
