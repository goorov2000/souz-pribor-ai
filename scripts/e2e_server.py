"""Test-only entry point: production routes, deterministic external LLM transport."""
import json

import httpx

from app.config import ROOT, Settings
from app.main import app

DRAFT = json.loads((ROOT / 'tests/fixtures/provider-draft.json').read_text())


def respond(request):
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    body = json.loads(request.content)
    product = json.loads(body['input'][1]['content'].split('\n', 1)[1])
    if product['name'] == 'Сбой API':
        return httpx.Response(429, json={'error': {'message': 'Simulated provider limit'}})
    return httpx.Response(200, json={
        'status': 'completed',
        'output': [{'content': [{'type': 'output_text', 'text': json.dumps(DRAFT, ensure_ascii=False)}]}],
        'usage': {'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300},
    })


app.state.settings = Settings(api_key='e2e-placeholder')
app.state.transport = httpx.MockTransport(respond)
