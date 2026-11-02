"""Explicit paid smoke check. Never imported by pytest; at most 12 requests total."""
import asyncio
from datetime import datetime, timezone
import json
import sys

import httpx

from app.cards import generate_card
from app.config import ROOT, Settings
from app.models import ProductInput
from app.provider import GenerationError

LEDGER = ROOT / 'examples' / 'live-run.json'


async def main():
    settings = Settings()
    if not settings.api_key:
        print('OPENAI_API_KEY не настроен.')
        return 1
    if settings.model != 'gpt-6-luna':
        print('Живая проверка разрешена только для gpt-6-luna.')
        return 1
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {'requests': 0, 'runs': []}
    def save():
        LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    class CountedTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            if ledger['requests'] >= 12:
                raise GenerationError('budget', 'Лимит живых запросов исчерпан.')
            ledger['requests'] += 1
            save()
            return await super().handle_async_request(request)
    identifiers = sys.argv[1:] or ['dm02', 'training']
    for identifier in identifiers:
        if identifier not in ('dm02', 'training'):
            print('Неизвестный пример.'); return 1
        product = ProductInput.model_validate_json((ROOT / 'examples' / f'{identifier}.input.json').read_text())
        before = ledger['requests']
        record = {'example': identifier, 'at': datetime.now(timezone.utc).isoformat()}
        try:
            result = await generate_card(product, settings, CountedTransport())
            result['metadata']['provenance'] = {'kind': 'live-api', 'generated_at': record['at'],
                'input': f'{identifier}.input.json', 'origin': 'JSON из PDF тестового задания' if identifier == 'dm02' else 'Учебный вымышленный набор; не паспорт реального изделия'}
            (ROOT / 'examples' / f'{identifier}.result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            (ROOT / 'examples' / f'{identifier}.result.md').write_text(result['exports']['markdown'])
            record.update({'status': 'verified', 'usage': result['metadata']['usage'], 'counts': result['counts']})
            print(identifier, 'verified', result['counts'], 'attempts', result['metadata']['attempts'], 'usage', result['metadata']['usage'])
        except GenerationError as error:
            record.update({'status': 'failed', 'error_code': error.code, 'usage': getattr(error, 'usage', None)})
            print(identifier, 'failed', error.code, error.message)
        record['requests'] = ledger['requests'] - before
        ledger['runs'].append(record); save()
    print('Live requests total:', ledger['requests'])
    return int(any(record['status'] == 'failed' for record in ledger['runs'][-len(identifiers):]))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
