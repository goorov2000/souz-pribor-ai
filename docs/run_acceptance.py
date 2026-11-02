"""Explicit, bounded live acceptance run; preserves all attempts and never logs secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import httpx
from fastapi.testclient import TestClient

from app.config import ROOT
from app.main import app
from app.models import ProductInput


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Actually call the paid API')
    parser.add_argument('--run-id', default='2026-11-03-acceptance')
    parser.add_argument('--phase', default='baseline')
    parser.add_argument('--replay-phase', help='Recover captured successful provider responses without paying twice')
    parser.add_argument('--only', nargs='+', help='Exact case IDs to run')
    parser.add_argument('--request-limit', type=int, help='Explicit total cap across phases; never resets the used count')
    args = parser.parse_args()
    names = (args.run_id, args.phase) + ((args.replay_phase,) if args.replay_phase else ())
    if any(Path(value).name != value or value in ('.', '..') for value in names):
        parser.error('run-id and phase must be plain directory names')
    manifest = json.loads((ROOT / 'evaluation/cases.json').read_text())
    cases = manifest['cases']
    if args.only:
        if set(args.only) - {case['id'] for case in cases}:
            parser.error('Unknown case ID')
        cases = [case for case in cases if case['id'] in args.only]
    for case in cases:
        raw = (ROOT / 'evaluation' / case['input_file']).read_text()
        if case.get('expect_api_call') is not False:
            ProductInput.model_validate_json(raw)
        else:
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                pass
            else:
                raise ValueError('Expected intentionally malformed JSON')
    if not args.live:
        print(f'Validated {len(cases)} cases. Add --live to make paid requests.')
        return 0
    if not app.state.settings.api_key:
        print('API key is not configured.')
        return 1
    if app.state.settings.model != 'gpt-6-luna':
        print('Acceptance run expects the configured gpt-6-luna model; no request made.')
        return 1
    run = ROOT / 'artifacts/acceptance' / args.run_id
    phase = run / args.phase
    if phase.exists():
        print('This phase already exists; refusing to overwrite evidence.')
        return 1
    phase.mkdir(parents=True)
    ledger_path = run / 'requests.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'limit': 24, 'requests': 0}
    if args.request_limit is not None:
        if not ledger['requests'] <= args.request_limit <= 36:
            parser.error('Total request limit must cover existing calls and be at most 36')
        if args.request_limit != ledger['limit']:
            ledger.setdefault('limit_changes', []).append({'phase': args.phase, 'from': ledger['limit'], 'to': args.request_limit})
            ledger['limit'] = args.request_limit
            write_json(ledger_path, ledger)
    snapshot = phase / 'source'
    snapshot.mkdir()
    hashes = {}
    for source in sorted((ROOT / 'app').glob('*.py')):
        shutil.copyfile(source, snapshot / source.name)
        hashes[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    if args.replay_phase:
        old_context = json.loads((run / args.replay_phase / 'context.json').read_text())
        if old_context['source_sha256'] != hashes:
            raise ValueError('Replay requires the unchanged application source')
    write_json(phase / 'context.json', {'at': datetime.now(timezone.utc).isoformat(),
               'model': app.state.settings.model, 'source_sha256': hashes, 'cases': cases})
    records = []
    attempts = []
    replay = []

    class RecordedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if replay:
                saved = replay.pop(0)
                attempt = {'number': saved['number'], 'request': json.loads(request.content),
                           'response': saved['response'], 'http_status': 200,
                           'replayed_from': args.replay_phase, 'seconds': saved['seconds']}
                attempts.append(attempt)
                return httpx.Response(200, json=saved['response'], request=request)
            if ledger['requests'] >= ledger['limit']:
                from app.provider import GenerationError
                raise GenerationError('acceptance_budget', 'Общий лимит приёмочного прогона исчерпан.', 429)
            ledger['requests'] += 1
            write_json(ledger_path, ledger)
            attempt = {'number': ledger['requests'], 'request': json.loads(request.content)}
            attempts.append(attempt)
            started = time.perf_counter()
            try:
                async with httpx.AsyncHTTPTransport() as transport:
                    response = await transport.handle_async_request(request)
                    content = await response.aread()
                attempt['http_status'] = response.status_code
                # Provider errors may quote a credential: preserve only their status.
                if response.status_code < 400:
                    payload = json.loads(content)
                    attempt['response'] = {key: payload.get(key) for key in ('id', 'model', 'status', 'output', 'usage')}
                # aread() already decoded compression; do not forward Content-Encoding.
                return httpx.Response(response.status_code, content=content, request=request)
            except httpx.HTTPError as error:
                attempt['network_error'] = type(error).__name__
                raise
            finally:
                attempt['seconds'] = round(time.perf_counter() - started, 3)

    previous_transport = app.state.transport
    try:
        with TestClient(app, base_url='http://127.0.0.1:8000') as client:
            for case in cases:
                raw = (ROOT / 'evaluation' / case['input_file']).read_text()
                for repetition in range(1, case['repeat'] + 1):
                    attempts = []
                    replay = []
                    if args.replay_phase:
                        old_path = run / args.replay_phase / f"{case['id']}-{repetition}.json"
                        old_record = json.loads(old_path.read_text())
                        replay = [attempt for attempt in old_record['attempts'] if attempt.get('response')]
                    app.state.transport = RecordedTransport()
                    started = time.perf_counter()
                    before = ledger['requests']
                    response = client.post('/api/generate', content=raw,
                                           headers={'Content-Type': 'application/json'})
                    body = response.json()
                    record = {'case': case['id'], 'repetition': repetition,
                              'http_status': response.status_code,
                              'seconds': round(time.perf_counter() - started, 3),
                              'request_count': len(attempts), 'attempts': attempts,
                              'new_request_count': ledger['requests'] - before,
                              'body': body, 'review': 'not-reviewed'}
                    filename = f"{case['id']}-{repetition}"
                    write_json(phase / (filename + '.json'), record)
                    if response.status_code == 200:
                        (phase / (filename + '.md')).write_text(body['exports']['markdown'])
                    records.append({key: record[key] for key in ('case', 'repetition', 'http_status', 'seconds', 'request_count', 'review')})
                    write_json(phase / 'summary.json', records)
                    outcome = body.get('error', {}).get('code', 'card-needs-review')
                    print(f"{filename}: HTTP {response.status_code}, {outcome}, {record['seconds']}s, new calls={record['new_request_count']}", flush=True)
                    if outcome == 'acceptance_budget':
                        return 1
    finally:
        app.state.transport = previous_transport
    print(f"Evidence: {phase.relative_to(ROOT)}; HTTP calls across phases: {ledger['requests']}/{ledger['limit']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
