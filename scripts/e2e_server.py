"""Test-only entry point: production routes, deterministic external LLM transport."""
import json

import httpx

from app.config import ROOT, Settings
from app.main import app
from app import enrichment

DRAFT = json.loads((ROOT / 'tests/fixtures/provider-draft.json').read_text())
SOURCE_URL = 'https://manufacturer.example/dm02'


async def fixture_url(url):
    return url == SOURCE_URL


enrichment.public_url = fixture_url


def respond(request):
    if request.method == 'GET':
        assert str(request.url) == SOURCE_URL
        assert 'authorization' not in request.headers
        return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'},
                              text='<h1>Манотомь DM 02</h1><p>Материал корпуса: сталь.</p>')
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    body = json.loads(request.content)
    product = json.loads(body['input'][1]['content'].split('\n', 1)[1])
    if product['name'] == 'Сбой API':
        return httpx.Response(429, json={'error': {'message': 'Simulated provider limit'}})
    if body.get('tools'):
        research = {'status': 'exact_match', 'model': 'DM 02', 'manufacturer': 'Манотомь',
                    'identity_quote': 'Манотомь DM 02', 'source_url': SOURCE_URL,
                    'facts': [{'name': 'Материал корпуса', 'value': 'сталь', 'quote': 'Материал корпуса: сталь.', 'source_url': SOURCE_URL}],
                    'warnings': []}
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'web_search_call', 'action': {'sources': [{'url': SOURCE_URL}]}},
            {'content': [{'type': 'output_text', 'text': json.dumps(research, ensure_ascii=False)}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300}})
    return httpx.Response(200, json={
        'status': 'completed',
        'output': [{'content': [{'type': 'output_text', 'text': json.dumps(DRAFT, ensure_ascii=False)}]}],
        'usage': {'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300},
    })


app.state.settings = Settings(api_key='e2e-placeholder')
app.state.transport = httpx.MockTransport(respond)
