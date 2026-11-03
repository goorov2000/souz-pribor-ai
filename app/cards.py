"""Deterministic checks are useful guards, not a semantic truth proof."""
import json
import re

from pydantic import ValidationError

from app.models import ModelDraft
from app.grounding import has_source, has_value, source_issues
from app.enrichment import enrich_product
from app.provider import GenerationError, request_draft

NUMBERS = re.compile(r'[+−-]?\d+(?:[.,]\d+)*')
CODES = re.compile(r'\b(?:[A-Za-zА-Яа-яЁё]+[\w./-]*\d[\w./-]*|[A-ZА-ЯЁ]{2,})\b')
UNITS = re.compile(r'(?<!\w)(?:МПа|кПа|Па|МГц|кГц|Гц|кВт|Вт|мА|мВ|мм|см|бар|[°º][CС]|IP\d+)(?!\w)')


def check_draft(raw, product):
    try:
        draft = ModelDraft.model_validate_json(raw, strict=True)
    except ValidationError:
        return None, ['ответ не соответствует схеме']
    issues = []
    draft.seo_title = draft.seo_title.strip()
    draft.seo_description = draft.seo_description.strip()
    draft.commercial_block = draft.commercial_block.strip()
    if draft.status == 'draft' and not 1 <= len(draft.seo_title) <= 70:
        issues.append('SEO-заголовок должен содержать от 1 до 70 символов')
    if draft.status == 'draft' and not 800 <= len(draft.seo_description) <= 1200:
        length = len(draft.seo_description)
        action = f'сократи примерно на {length - 900} символов' if length > 1200 else f'добавь примерно {900 - length} символов'
        issues.append(f'SEO-описание: сейчас {length} символов, требуется 800–1200; {action}, цель 850–950. В SEO оставь только несколько параметров; остальные уже есть в таблице. Не перечисляй все условия погрешности и версии ОС. Не меняй статус из-за длины')
    if draft.status == 'draft' and (not 3 <= len(draft.search_queries) <= 5 or any(not q.strip() for q in draft.search_queries) or len(set(draft.search_queries)) != len(draft.search_queries)):
        issues.append('нужно от 3 до 5 разных непустых поисковых запросов')
    if draft.status == 'draft' and (not draft.commercial_block.strip() or len(draft.commercial_block) > 1000):
        issues.append('commercial-блок должен быть непустым и коротким, до 1000 символов')
    if draft.status == 'needs_data' and not 1 <= len(draft.seo_description) <= 600:
        issues.append('При нехватке данных дай краткую справку до 600 символов вместо SEO-описания')
    source = '\n'.join([product.name, product.manufacturer or '', product.description or '',
                        *[f'{key}: {value}' for key, value in product.specs.items() if value is not None]])
    generated = '\n'.join([draft.seo_title, draft.seo_description, draft.commercial_block, *draft.search_queries, *draft.warnings, *[f.name for f in draft.extracted_facts]])
    for pattern, label in [(NUMBERS, 'числа'), (CODES, 'обозначения'), (UNITS, 'единицы')]:
        known = set(pattern.findall(source))
        if pattern is CODES:
            known.add('SEO')  # An editorial label, not a product designation.
        unknown = set(pattern.findall(generated)) - known
        if unknown:
            issues.append(f'найдены неподтверждённые {label}; используй только исходную запись')
    seen = {key.casefold() for key, value in product.specs.items() if has_value(value)}
    for index, fact in enumerate(draft.extracted_facts, 1):
        fact.name = fact.name.strip()
        source_text = getattr(product, fact.source) or ''
        if (not fact.name.strip() or not fact.value.strip() or not fact.quote.strip()
                or fact.quote not in source_text
                or len(fact.name) > 100 or fact.value not in fact.quote
                or fact.name.casefold() in seen):
            issues.append(f'Извлечённый факт №{index}: проверь имя и источник. quote должна дословно входить в указанный источник, value — дословно в quote, включая падеж. Не дублируй заполненные характеристики. Исправь только факт, не меняй draft на needs_data из-за ошибки цитаты')
        seen.add(fact.name.casefold())
    if len(draft.extracted_facts) > 100 or len(draft.missing_characteristics) > 60:
        issues.append('не более 100 извлечённых фактов и 60 недостающих характеристик')
    gaps = set()
    for name in draft.missing_characteristics:
        key = name.strip().casefold()
        if not key or len(name) > 150 or key in seen or key in gaps:
            issues.append('названия недостающих характеристик должны быть непустыми, уникальными и не повторять известные поля')
        gaps.add(key)
    conflicts = set()
    for fact in draft.conflicts:
        if (fact.name not in product.specs or product.specs.get(fact.name) in (None, '')
                or not fact.value.strip() or not fact.quote.strip()
                or fact.quote not in (product.description or '') or fact.value not in fact.quote
                or fact.value == str(product.specs.get(fact.name)) or fact.name in conflicts):
            issues.append('противоречие должно ссылаться на известное поле specs и точную цитату с другим значением')
        conflicts.add(fact.name)
    issues.extend(source_issues(product, draft))
    return draft, issues


def markdown(card):
    def escape(value):
        return re.sub(r'([\\`*_{}\[\]<>|])', r'\\\1', str(value)).replace('\n', ' ')
    rows = ['# ' + escape(card['seo_title']), '', '> Требует проверки человеком перед публикацией.', '',
            '## Commercial-блок для менеджера', '', escape(card['commercial_block']), '',
            '## SEO-описание', '', '\n\n'.join(escape(part) for part in re.split(r'\n\s*\n', card['seo_description'])), '',
            '## Товар', '', 'Название: ' + escape(card['product_identity']['name']),
            'Производитель: ' + escape(card['product_identity']['manufacturer']), '',
            '## Характеристики для карточки', '', '| Параметр | Значение | Источник |', '| --- | --- | --- |']
    for row in card['characteristics']:
        source = characteristic_source(row)
        rows.append('| ' + ' | '.join(escape(value) for value in [row['name'], row['value'], source]) + ' |')
    research = card.get('research', {})
    if research.get('status') != 'not_requested' and research:
        rows += ['', '## Источники обогащения', '']
        rows += ['- ' + escape(url) for url in research.get('sources', [])]
        rows += ['- ' + escape(message) for message in research.get('warnings', [])]
    rows += ['', '## Поисковые запросы', ''] + ['- ' + escape(q) for q in card['search_queries']]
    rows += ['', '## Требует уточнения', '']
    rows += ['- ' + escape(value) for value in card['missing_fields'] + card['warnings']] or ['- Нет дополнительных замечаний.']
    return '\n'.join(rows) + '\n'


def characteristic_source(row):
    if row['source'] == 'specs':
        return 'исходные характеристики'
    if row['source'] is None:
        return 'нет в исходных материалах'
    if row['source'] == 'web':
        return 'документация: ' + row['source_url'] + ' — ' + (row['quote'] or '')
    return ('название: ' if row['source'] == 'name' else 'описание: ') + (row['quote'] or '')


def build_card(draft, product):
    missing = []
    for name in ('manufacturer', 'description'):
        if not getattr(product, name):
            missing.append(name)
    characteristics = []
    conflicts = {fact.name: fact for fact in draft.conflicts}
    extracted = {fact.name.casefold(): fact for fact in draft.extracted_facts}
    if not product.specs and not draft.extracted_facts and not draft.missing_characteristics:
        missing.append('specs')
        characteristics.append({'name': 'Характеристики', 'value': 'данных нет', 'source': 'specs', 'quote': None})
    for name, value in product.specs.items():
        if not has_value(value) and name.casefold() in extracted:
            fact = extracted.pop(name.casefold())
            characteristics.append({'name': name, 'value': fact.value, 'source': fact.source, 'quote': fact.quote})
            continue
        absent = not has_value(value)
        if absent or name in conflicts:
            missing.append('specs.' + name)
        display = 'требует уточнения: противоречие источников' if name in conflicts else 'данных нет' if absent else str(value)
        characteristics.append({'name': name, 'value': display, 'source': 'specs', 'quote': None})
    characteristics += [{'name': f.name, 'value': f.value, 'source': f.source, 'quote': f.quote} for f in extracted.values()]
    existing = {row['name'].casefold() for row in characteristics}
    for name in draft.missing_characteristics:
        name = name.strip()
        if name.casefold() not in existing:
            characteristics.append({'name': name, 'value': 'данных нет', 'source': None, 'quote': None})
            missing.append('specs.' + name)
            existing.add(name.casefold())
    warnings = list(dict.fromkeys(draft.warnings + ['Черновик: проверьте смысл, назначение и полноту по паспорту перед публикацией.']))
    warnings += [f'Противоречие: {name}. В характеристиках: {product.specs[name]}; в описании: {fact.value}. Цитата: «{fact.quote}».'
                 for name, fact in conflicts.items()]
    if any(field not in {'specs.' + name for name in conflicts} for field in missing):
        warnings.append('Часть исходных данных отсутствует; свойства не восстанавливались по аналогам.')
    return {'seo_title': draft.seo_title, 'seo_description': draft.seo_description,
            'product_identity': {'name': product.name, 'manufacturer': product.manufacturer or 'данных нет'},
            'enrichment': {'provided': sum(has_value(value) for value in product.specs.values()),
                           'extracted': len(draft.extracted_facts),
                           'missing': sum(row['value'] == 'данных нет' for row in characteristics)},
            'characteristics': characteristics, 'search_queries': draft.search_queries,
            'commercial_block': draft.commercial_block, 'missing_fields': missing, 'warnings': warnings}


async def generate_card(product, settings, transport=None):
    original_product = product
    product, research, usage = await enrich_product(product, settings, transport)
    if original_product.enrich and not has_source(product):
        return {'status': 'needs_data', 'api_called': True,
                'message': 'Не удалось получить проверяемые сведения о точной модели. Добавьте производителя, исполнение и текст документации.',
                'missing_fields': (['manufacturer'] if not product.manufacturer else []) + ['description', 'specs'],
                'next_steps': research['warnings'], 'research': research,
                'metadata': {'provider': 'OpenAI', 'model': settings.model, 'attempts': 0, 'usage': usage}}
    feedback = None
    for attempt in range(1, 3):
        raw, spent = await request_draft(product, settings, transport, feedback)
        usage = {key: usage[key] + spent[key] for key in usage}
        draft, feedback = check_draft(raw, product)
        if not feedback:
            card = build_card(draft, product)
            web_facts = {fact['name'].casefold(): fact for fact in research['facts']}
            external_conflicts = {fact['name'].casefold(): fact for fact in research['conflicts']}
            for row in card['characteristics']:
                if row['source'] == 'specs' and row['name'].casefold() in web_facts:
                    fact = web_facts[row['name'].casefold()]
                    row.update(source='web', quote=fact['quote'], source_url=fact['source_url'])
                if row['name'].casefold() in external_conflicts:
                    fact = external_conflicts[row['name'].casefold()]
                    row.update(value='требует уточнения: противоречие источников', source='web',
                               quote=fact['quote'], source_url=fact['source_url'])
            card['enrichment']['provided'] = sum(has_value(value) for value in original_product.specs.values())
            if original_product.enrich:
                card['enrichment']['from_documents'] = research['added']
                if research['added']:
                    card['missing_fields'] = [field for field in card['missing_fields'] if field != 'description']
                card['research'] = research
                card['warnings'] += research['warnings']
                card['warnings'] += [f"Противоречие: {fact['name']}. Во входе: {fact['input_value']}; в документации: {fact['value']}. Источник: {fact['source_url']}"
                                     for fact in research['conflicts']]
            metadata = {'provider': 'OpenAI', 'model': settings.model, 'attempts': attempt, 'usage': usage,
                        'verification': 'structure-and-source-tokens; human review required'}
            if draft.status == 'needs_data':
                return {'status': 'needs_data', 'api_called': True,
                        'message': 'Для содержательной карточки пока мало технических фактов. Дополните источник и повторите генерацию.',
                        'summary': draft.seo_description, 'known_characteristics': card['characteristics'],
                        'enrichment': card['enrichment'],
                        'missing_fields': card['missing_fields'], 'warnings': draft.warnings,
                        **({'research': research} if original_product.enrich else {}),
                        'next_steps': ['Добавьте недостающие характеристики или более полный фрагмент паспорта.'],
                        'metadata': metadata}
            return {'status': 'draft', 'card': card, 'counts': {'seo_title': len(card['seo_title']), 'seo_description': len(card['seo_description'])},
                    'metadata': metadata,
                    'exports': {'json': json.dumps(card, ensure_ascii=False, indent=2), 'markdown': markdown(card)}}
    error = GenerationError('unverified_output', 'Ответ не прошёл проверку после одной коррекции. Карточка не выдана: ' + '; '.join(feedback) + '. Уточните исходник и повторите.', 422)
    error.usage = usage
    raise error
