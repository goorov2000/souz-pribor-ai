import asyncio
import json
import socket

import httpx
import pytest

from app import enrichment
from app.config import ROOT, Settings
from app.models import ProductInput
from app.cards import generate_card


URL = 'https://manufacturer.example/dm02'
PRODUCT = ProductInput.model_validate_json((ROOT / 'examples/dm02.input.json').read_text()).model_copy(update={'enrich': True})
DOCUMENT = '<html><body><h1>Манотомь DM 02</h1><p>Материал корпуса: сталь.</p><p>Масса: 0 кг.</p></body></html>'


def research_payload():
    return {'status': 'exact_match', 'model': 'DM 02', 'manufacturer': 'Манотомь',
            'identity_quote': 'Манотомь DM 02', 'source_url': URL,
            'facts': [{'name': 'Материал корпуса', 'value': 'сталь', 'quote': 'Материал корпуса: сталь.', 'source_url': URL}],
            'warnings': []}


def install(monkeypatch, research, document=DOCUMENT, cited=True, api_error=False):
    requests = []

    async def is_public(url):
        return url == URL
    monkeypatch.setattr(enrichment, 'public_url', is_public)

    def respond(request):
        requests.append(request)
        if request.method == 'GET':
            assert request.url == URL
            assert 'authorization' not in request.headers
            return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'}, text=document)
        body = json.loads(request.content)
        if body.get('tools'):
            assert body['tool_choice'] == 'required'
            assert body['max_tool_calls'] == 5
            if api_error:
                return httpx.Response(500, json={'error': {'message': 'secret-marker'}})
            payload = research
            sources = [{'type': 'web_search_call', 'action': {'type': 'search', 'sources': [{'url': URL}]}}] if cited else []
        else:
            payload = json.loads((ROOT / 'tests/fixtures/provider-draft.json').read_text())
            sources = []
        return httpx.Response(200, json={'status': 'completed', 'output': sources + [
            {'content': [{'type': 'output_text', 'text': json.dumps(payload, ensure_ascii=False)}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}})
    return httpx.MockTransport(respond), requests


def test_verified_document_adds_a_new_fact_with_provenance_to_both_exports(monkeypatch):
    transport, requests = install(monkeypatch, research_payload())
    result = asyncio.run(generate_card(PRODUCT, Settings(api_key='test-placeholder'), transport))
    card = result['card']
    row = next(row for row in card['characteristics'] if row['name'] == 'Материал корпуса')
    assert row == {'name': 'Материал корпуса', 'value': 'сталь', 'source': 'web', 'quote': 'Материал корпуса: сталь.', 'source_url': URL}
    assert card['enrichment']['provided'] == 4
    assert card['enrichment']['from_documents'] == 1
    assert 'description' not in card['missing_fields']
    assert card['research']['sources'] == [URL]
    assert json.loads(result['exports']['json']) == card
    assert URL in result['exports']['markdown']
    assert result['metadata']['usage']['total_tokens'] == 60
    assert [request.method for request in requests] == ['POST', 'GET', 'POST']


@pytest.mark.parametrize('mutation', ['other_manufacturer', 'other_model', 'identity_quote', 'fact_quote', 'uncited', 'ambiguous', 'api_error', 'foreign_source'])
def test_unverified_or_ambiguous_research_does_not_add_values(monkeypatch, mutation):
    research = research_payload()
    if mutation == 'other_manufacturer': research['manufacturer'] = 'Другой завод'
    if mutation == 'other_model': research['model'] = 'DM 03'
    if mutation == 'identity_quote': research['identity_quote'] = 'Отсутствующая цитата DM 02'
    if mutation == 'fact_quote': research['facts'][0]['quote'] = 'Нержавеющая сталь'
    if mutation == 'ambiguous': research.update(status='ambiguous', facts=[])
    if mutation == 'foreign_source': research['facts'][0]['source_url'] = 'https://other.example/data'
    transport, _ = install(monkeypatch, research, cited=mutation != 'uncited', api_error=mutation == 'api_error')
    product, report, usage = asyncio.run(enrichment.enrich_product(PRODUCT, Settings(api_key='test-placeholder'), transport))
    assert product.specs == PRODUCT.specs
    assert report['added'] == 0
    assert report['warnings']
    assert 'secret-marker' not in json.dumps(report)


def test_document_conflict_is_quarantined_instead_of_overwriting_known_input(monkeypatch):
    research = research_payload()
    product = PRODUCT.model_copy(update={'specs': {**PRODUCT.specs, 'Материал корпуса': 'латунь'}})
    transport, _ = install(monkeypatch, research)
    result = asyncio.run(generate_card(product, Settings(api_key='test-placeholder'), transport))
    card = result['card']
    row = next(row for row in card['characteristics'] if row['name'] == 'Материал корпуса')
    assert 'противоречие' in row['value']
    assert card['research']['conflicts'][0]['input_value'] == 'латунь'
    assert card['research']['conflicts'][0]['value'] == 'сталь'
    assert card['enrichment']['from_documents'] == 0


def test_document_can_fill_a_blank_input_value_and_preserves_zero(monkeypatch):
    research = research_payload()
    research['facts'] = [{'name': 'Масса', 'value': '0 кг', 'quote': 'Масса: 0 кг.', 'source_url': URL}]
    product = PRODUCT.model_copy(update={'specs': {'Масса': None}})
    transport, _ = install(monkeypatch, research)
    working, report, _ = asyncio.run(enrichment.enrich_product(product, Settings(api_key='test-placeholder'), transport))
    assert working.specs['Масса'] == '0 кг'
    assert report['added'] == 1


def test_literal_model_quote_can_disambiguate_a_copied_full_product_name(monkeypatch):
    research = research_payload()
    research.update(model=PRODUCT.name, identity_quote='DM 02')
    transport, _ = install(monkeypatch, research)
    _, report, _ = asyncio.run(enrichment.enrich_product(PRODUCT, Settings(api_key='test-placeholder'), transport))
    assert report['added'] == 1


def test_shared_manual_parameter_is_excluded_without_a_model_specific_quote(monkeypatch):
    research = research_payload()
    research['facts'][0].update(source_url='https://manufacturer.example/shared-manual.pdf',
                               name='Присоединение', value='7/16 UNF', quote='Присоединение: 7/16 UNF')
    transport, _ = install(monkeypatch, research)
    working, report, _ = asyncio.run(enrichment.enrich_product(PRODUCT, Settings(api_key='test-placeholder'), transport))
    assert working.specs == PRODUCT.specs
    assert report['added'] == 0


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_document_fetch_rejects_private_or_local_dns_destinations(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    assert not asyncio.run(enrichment.public_url('https://manufacturer.example/doc'))


def test_document_fetch_checks_each_redirect_and_never_forwards_credentials(monkeypatch):
    async def is_public(url):
        return url == URL
    monkeypatch.setattr(enrichment, 'public_url', is_public)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/private'})
    with pytest.raises(ValueError):
        asyncio.run(enrichment.source_text(URL, httpx.MockTransport(respond)))
    assert len(calls) == 1


def test_no_research_request_when_input_opts_out():
    def unexpected(request):
        raise AssertionError('Unexpected network request')
    _, report, usage = asyncio.run(enrichment.enrich_product(PRODUCT.model_copy(update={'enrich': False}),
                                 Settings(api_key='test-placeholder'), httpx.MockTransport(unexpected)))
    assert report['status'] == 'not_requested'
    assert usage['total_tokens'] == 0


def test_optical_ratio_is_not_confused_with_temperature_resolution():
    from app.grounding import source_issues
    from app.models import ModelDraft
    product = ProductInput(name='Термометр', specs={'разрешение': '0.1 °C', 'оптика': '10:1'})
    draft = ModelDraft(status='draft', seo_title='Термометр', seo_description='Оптическое разрешение 10:1. Разрешение — 0.1 °C.',
                       search_queries=[], commercial_block='', extracted_facts=[], missing_characteristics=[], conflicts=[], warnings=[])
    assert not source_issues(product, draft)
    draft.seo_description = 'Разрешение — 10 °C.'
    assert source_issues(product, draft)
