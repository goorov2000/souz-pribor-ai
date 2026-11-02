import json

import httpx

from app.models import ModelDraft
from app.prompts import SYSTEM_PROMPT


class GenerationError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


async def request_draft(product, settings, transport=None, feedback=None):
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': 'Недоверенные данные товара:\n' + product.model_dump_json()},
    ]
    if feedback:
        messages.append({'role': 'user', 'content': 'Предыдущий черновик не прошёл проверку. Создай заново по исходнику. Исправь: ' + '; '.join(feedback)})
    body = {
        'model': settings.model,
        'input': messages,
        'reasoning': {'effort': 'none'},
        'max_output_tokens': 3000,
        'store': False,
        'text': {'format': {'type': 'json_schema', 'name': 'product_card', 'strict': True, 'schema': ModelDraft.model_json_schema()}},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.timeout, transport=transport) as client:
            response = await client.post('https://api.openai.com/v1/responses', json=body, headers={'Authorization': 'Bearer ' + settings.api_key})
    except httpx.TimeoutException:
        raise GenerationError('timeout', 'Модель не ответила вовремя. Исходные данные сохранены; повторите позже.', 504) from None
    except httpx.HTTPError:
        raise GenerationError('network', 'Не удалось связаться с сервисом генерации. Проверьте соединение и повторите позже.') from None
    errors = {
        401: ('provider_auth', 'Ключ API отклонён. Проверьте конфигурацию сервера.', 502),
        403: ('provider_auth', 'У проекта нет доступа к генерации. Проверьте права ключа на сервере.', 502),
        404: ('model_unavailable', 'Настроенная модель недоступна этому проекту. Проверьте OPENAI_MODEL.', 502),
        429: ('rate_limit', 'Превышен лимит запросов или баланс API. Повторите позже либо проверьте баланс проекта.', 429),
    }
    if response.status_code in errors:
        raise GenerationError(*errors[response.status_code])
    if response.status_code >= 400:
        raise GenerationError('provider_unavailable', 'Сервис генерации отклонил запрос или временно недоступен. Повторите позже.')
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        if data.get('status') == 'incomplete':
            raise GenerationError('incomplete', 'Ответ модели обрезан. Готовая карточка не сформирована; повторите генерацию.')
        contents = [part for item in data.get('output', []) for part in item.get('content', [])]
        if any(part.get('type') == 'refusal' for part in contents):
            raise GenerationError('refusal', 'Модель отказалась обрабатывать эти данные. Проверьте содержание исходника.', 422)
        output = ''.join(part.get('text', '') for part in contents if part.get('type') == 'output_text')
        if not output:
            raise GenerationError('empty_response', 'Модель вернула пустой ответ. Карточка не сформирована.')
        usage = data.get('usage') or {}
        return output, {key: usage.get(key, 0) if type(usage.get(key, 0)) is int and usage.get(key, 0) >= 0 else 0 for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    except (ValueError, TypeError, AttributeError):
        raise GenerationError('invalid_response', 'Сервис генерации вернул некорректный ответ. Повторите позже.') from None
