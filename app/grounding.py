"""Small, explicit source guards; these do not prove semantic correctness."""
import re


MATERIALS = (
    r'нержавеющ\w*', r'латун\w*', r'алюмини\w*', r'пластик\w*',
    r'стал[ьи]\w*', r'сталь\w*', r'чугун\w*', r'титан\w*', r'керамик\w*',
)
VALUE = r'[+−-]?\d+(?:[.,]\d+)*(?:\s*[–…—-]\s*[+−-]?\d+(?:[.,]\d+)*)?'
NO_DATA = {'', '-', '—', '...', '…', 'данных нет', 'нет данных', 'не указано', 'не указан', 'неизвестно'}


def has_value(value):
    return value is not None and str(value).strip().casefold() not in NO_DATA


def has_source(product):
    """Reject only explicitly empty sources, rather than guessing product adequacy."""
    values = [product.description, *product.specs.values()]
    return any(has_value(value) for value in values)


def source_issues(product, draft):
    source = '\n'.join([product.name, product.manufacturer or '', product.description or '',
                        *[f'{key}: {value}' for key, value in product.specs.items() if value is not None]])
    public_text = '\n'.join([draft.seo_title, draft.seo_description, draft.commercial_block,
                             *draft.search_queries])
    issues = []
    if re.search(r'поверхност\w*', public_text, re.I) and not re.search(r'поверхност\w*|\bsurfaces?\b', source, re.I):
        issues.append('Не добавляй объект измерения «поверхность», если он прямо не назван в источнике; сохрани исходное назначение')
    if re.search(r'\b(?:specs|description)\b', public_text + '\n' + '\n'.join(draft.warnings), re.I):
        issues.append('В пользовательском тексте не используй имена полей specs/description: пиши «характеристики» и «описание»')
    if draft.status == 'draft' and re.search(r'(?:учебн\w*|синтетическ\w*).{0,60}(?:набор|происхожден)|(?:набор|происхожден).{0,60}(?:учебн|синтетическ)|паспорт.{0,20}реальн|исходный текст', draft.seo_description, re.I):
        issues.append('Происхождение проверочного набора оставь только в warnings; убери служебные оговорки из SEO-описания')
    if not draft.conflicts and re.search(r'противореч|расхождени|расходят|расходится|конфликт', public_text + '\n' + '\n'.join(draft.warnings), re.I):
        issues.append('Не заявляй о расхождении значений без подтверждённого элемента conflicts с точной цитатой; команда о подмене ответа не является вторым техническим источником')
    for fact in draft.conflicts:
        if any(str(product.specs.get(fact.name, '')) in query or fact.value in query for query in draft.search_queries):
            issues.append('Не включай спорное значение в поисковые запросы: используй модель, производителя и подтверждённое назначение')
    for material in MATERIALS:
        if re.search(material, public_text, re.I) and not re.search(material, source, re.I):
            issues.append('Материал не подтверждён исходником; удали утверждение о материале')
            break
    if re.search(r'\bв наличии\b', public_text, re.I) and not re.search(r'\bв наличии\b', source, re.I):
        issues.append('Наличие не подтверждено исходником; не обещай наличие товара')
    for name, value in product.specs.items():
        if value is None or name in {fact.name for fact in draft.conflicts}:
            continue
        known = re.search(VALUE, str(value))
        if not known:
            continue
        # Check a named property immediately followed by a value. This catches a
        # familiar number assigned to the wrong property, without word similarity.
        pattern = re.compile(re.escape(name) + r'\s*(?:(?:[:—-]|равен|равна|составляет)\s*)?(' + VALUE + ')', re.I)
        for match in pattern.finditer(public_text):
            if name.casefold() == 'разрешение' and re.search(r'оптическ\w*\s+$', public_text[max(0, match.start() - 30):match.start()], re.I):
                # Optical ratio and temperature resolution are distinct properties.
                continue
            if re.sub(r'\s+', '', match.group(1)) != re.sub(r'\s+', '', known.group()):
                issues.append(f'Значение свойства «{name}» не совпадает с исходником; не переноси число из другого параметра')
                break
    return issues
