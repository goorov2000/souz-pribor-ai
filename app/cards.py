"""Deterministic checks are useful guards, not a semantic truth proof."""
import json
import re

from pydantic import ValidationError

from app.models import ModelDraft
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
    if not 1 <= len(draft.seo_title) <= 70:
        issues.append('SEO-заголовок должен содержать от 1 до 70 символов')
    if not 800 <= len(draft.seo_description) <= 1200:
        issues.append(f'SEO-описание: сейчас {len(draft.seo_description)} символов, требуется 800–1200')
    if not 3 <= len(draft.search_queries) <= 5 or any(not q.strip() for q in draft.search_queries) or len(set(draft.search_queries)) != len(draft.search_queries):
        issues.append('нужно от 3 до 5 разных непустых поисковых запросов')
    if not draft.commercial_block.strip() or len(draft.commercial_block) > 1000:
        issues.append('commercial-блок должен быть непустым и коротким, до 1000 символов')
    source = '\n'.join([product.name, product.manufacturer or '', product.description or '',
                        *[f'{key}: {value}' for key, value in product.specs.items() if value is not None]])
    generated = '\n'.join([draft.seo_title, draft.seo_description, draft.commercial_block, *draft.search_queries, *draft.warnings, *[f.name for f in draft.extracted_facts]])
    for pattern, label in [(NUMBERS, 'числа'), (CODES, 'обозначения'), (UNITS, 'единицы')]:
        known = set(pattern.findall(source))
        unknown = set(pattern.findall(generated)) - known
        if unknown:
            issues.append(f'найдены неподтверждённые {label}; используй только исходную запись')
    seen = {key.casefold() for key in product.specs}
    for fact in draft.extracted_facts:
        if (not fact.name.strip() or not fact.value.strip() or not fact.quote.strip()
                or fact.quote not in (product.description or '')
                or len(fact.name) > 100 or fact.value not in fact.quote
                or fact.name.casefold() in seen):
            issues.append('факт из описания не подтверждён точной цитатой либо дублирует specs')
        seen.add(fact.name.casefold())
    return draft, issues


def markdown(card):
    def escape(value):
        return re.sub(r'([\\`*_{}\[\]<>|])', r'\\\1', str(value)).replace('\n', ' ')
    rows = ['# ' + escape(card['seo_title']), '', '> Требует проверки человеком перед публикацией.', '',
            '## SEO-описание', '', escape(card['seo_description']), '',
            '## Характеристики', '', '| Параметр | Значение | Источник |', '| --- | --- | --- |']
    for row in card['characteristics']:
        source = 'исходные характеристики' if row['source'] == 'specs' else ('описание: ' + (row['quote'] or ''))
        rows.append('| ' + ' | '.join(escape(value) for value in [row['name'], row['value'], source]) + ' |')
    rows += ['', '## Поисковые запросы', ''] + ['- ' + escape(q) for q in card['search_queries']]
    rows += ['', '## Для менеджера', '', escape(card['commercial_block']), '', '## Требует уточнения', '']
    rows += ['- ' + escape(value) for value in card['missing_fields'] + card['warnings']] or ['- Нет дополнительных замечаний.']
    return '\n'.join(rows) + '\n'


def build_card(draft, product):
    missing = []
    for name in ('manufacturer', 'description'):
        if not getattr(product, name):
            missing.append(name)
    characteristics = []
    if not product.specs:
        missing.append('specs')
        characteristics.append({'name': 'Характеристики', 'value': 'данных нет', 'source': 'specs', 'quote': None})
    for name, value in product.specs.items():
        absent = value is None or value == ''
        if absent:
            missing.append('specs.' + name)
        characteristics.append({'name': name, 'value': 'данных нет' if absent else str(value), 'source': 'specs', 'quote': None})
    characteristics += [{'name': f.name, 'value': f.value, 'source': 'description', 'quote': f.quote} for f in draft.extracted_facts]
    warnings = list(dict.fromkeys(draft.warnings + ['Черновик: проверьте смысл, назначение и полноту по паспорту перед публикацией.']))
    if missing:
        warnings.append('Часть исходных данных отсутствует; свойства не восстанавливались по аналогам.')
    return {'seo_title': draft.seo_title, 'seo_description': draft.seo_description,
            'characteristics': characteristics, 'search_queries': draft.search_queries,
            'commercial_block': draft.commercial_block, 'missing_fields': missing, 'warnings': warnings}


async def generate_card(product, settings, transport=None):
    usage = dict.fromkeys(('input_tokens', 'output_tokens', 'total_tokens'), 0)
    feedback = None
    for attempt in range(1, 3):
        raw, spent = await request_draft(product, settings, transport, feedback)
        usage = {key: usage[key] + spent[key] for key in usage}
        draft, feedback = check_draft(raw, product)
        if not feedback:
            card = build_card(draft, product)
            return {'card': card, 'counts': {'seo_title': len(card['seo_title']), 'seo_description': len(card['seo_description'])},
                    'metadata': {'provider': 'OpenAI', 'model': settings.model, 'attempts': attempt, 'usage': usage,
                                 'verification': 'structure-and-source-tokens; human review required'},
                    'exports': {'json': json.dumps(card, ensure_ascii=False, indent=2), 'markdown': markdown(card)}}
    error = GenerationError('unverified_output', 'Ответ не прошёл проверку после одной коррекции. Карточка не выдана: ' + '; '.join(feedback) + '. Уточните исходник и повторите.', 422)
    error.usage = usage
    raise error
