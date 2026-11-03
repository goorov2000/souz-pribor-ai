import json
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.config import ROOT, Settings
from app.models import ProductInput
from app.provider import GenerationError
from app.cards import generate_card
from app.grounding import has_source

app = FastAPI(title='Прибор', docs_url=None, redoc_url=None)
app.state.settings = Settings()
app.state.transport = None
app.state.generation_lock = threading.Lock()


def failure(code, message, status=422):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


@app.middleware('http')
async def limits(request: Request, call_next):
    if request.url.path == '/api/generate':
        size = request.headers.get('content-length')
        if size and (not size.isdigit() or int(size) > 32000):
            return failure('request_too_large', 'Запрос слишком большой. Максимум — 32 КБ.', 413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32000:
                return failure('request_too_large', 'Запрос слишком большой. Максимум — 32 КБ.', 413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.post('/api/generate')
async def generate(request: Request):
    origin = request.headers.get('origin')
    if origin is not None:
        try:
            source = urlsplit(origin)
            default_ports = {'http': 80, 'https': 443}
            source_port = source.port if source.port is not None else default_ports.get(source.scheme)
            request_port = request.url.port if request.url.port is not None else default_ports.get(request.url.scheme)
            allowed = (source.hostname in {'localhost', '127.0.0.1', '::1'}
                       and not (source.username or source.password or source.path or source.query or source.fragment)
                       and (source.scheme, source.hostname, source_port)
                       == (request.url.scheme, request.url.hostname, request_port))
        except ValueError:
            allowed = False
        if not allowed:
            return failure('forbidden_origin', 'Генерация доступна только из собственного локального интерфейса.', 403)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        return failure('invalid_content_type', 'Отправьте JSON с заголовком Content-Type: application/json.', 415)
    try:
        product = ProductInput.model_validate_json(await request.body())
    except ValidationError:
        return failure('invalid_input', 'Проверьте JSON и поля: название обязательно; характеристики — объект со строками, конечными числами или null. Лимиты: название и производитель — 250, описание — 12 000 символов, до 60 характеристик.')
    if not has_source(product) and (not product.enrich or not product.manufacturer):
        return {'status': 'needs_data', 'api_called': False,
                'message': 'Добавьте описание из паспорта или известные характеристики. По одному названию нельзя подготовить полезную карточку без догадок.',
                'missing_fields': (['manufacturer'] if not product.manufacturer else []) + ['description', 'specs'],
                'next_steps': ['Вставьте назначение и технические сведения из документации.',
                               'Укажите известные параметры с исходными значениями и единицами.']}
    if not app.state.settings.api_key:
        return failure('missing_key', 'На сервере не задан OPENAI_API_KEY. Добавьте ключ в .env.local и перезапустите приложение.', 503)
    if not app.state.generation_lock.acquire(blocking=False):
        return failure('busy', 'Генерация уже выполняется. Дождитесь результата и повторите.', 429)
    try:
        return await generate_card(product, app.state.settings, app.state.transport)
    except GenerationError as exc:
        return failure(exc.code, exc.message, exc.status)
    finally:
        app.state.generation_lock.release()


EXAMPLES = {
    'dm02': {'label': 'DM 02 · пример из задания', 'origin': 'JSON-пример из PDF тестового задания; не полный паспорт изделия.'},
    'training': {'label': 'ТД-01 · неполные данные', 'origin': 'Учебный вымышленный набор; не данные работодателя и не паспорт реального изделия.'},
    'testo805i': {'label': 'testo 805i · реальный прибор', 'origin': 'Отдельный проверочный набор по официальной странице Testo; работодателем не предоставлен.'},
    'testo805i-enriched': {'label': 'testo 805i · обогащение из документации', 'origin': 'Название и производитель без характеристик на входе; значения найдены в официальной документации. Дополнительный пример расширения, не товар работодателя.'},
}
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
async def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
async def health():
    return {'ready': bool(app.state.settings.api_key), 'model': app.state.settings.model,
            'message': 'Ключ настроен; доступность проверяется при генерации.' if app.state.settings.api_key else 'Добавьте OPENAI_API_KEY в .env.local и перезапустите сервер.'}


@app.get('/api/examples')
async def examples():
    return {'examples': [{'id': key, **meta, 'input': json.loads((ROOT / 'examples' / f'{key}.input.json').read_text())}
                         for key, meta in EXAMPLES.items()]}


@app.get('/api/examples/{example_id}/result')
async def saved_example(example_id: str):
    if example_id not in EXAMPLES:
        return failure('not_found', 'Пример не найден.', 404)
    path = ROOT / 'examples' / f'{example_id}.result.json'
    if not path.is_file():
        return failure('example_unavailable', 'Сохранённый результат пока не подготовлен. Можно загрузить исходные данные.', 404)
    return {'saved_example': True, 'origin': EXAMPLES[example_id]['origin'], 'result': json.loads(path.read_text())}
