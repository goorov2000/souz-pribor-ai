"""Optional browser regressions; every request is intercepted, no server or API needed."""
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class ClientRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            executable_path=os.environ.get('CHROMIUM_EXECUTABLE_PATH') or cls.playwright.chromium.executable_path,
            headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.requests = []
        self.saved_requests = []
        self.examples = [
            {'id': key, 'label': label, 'origin': origin,
             'input': json.loads((ROOT / 'examples' / f'{key}.input.json').read_text())}
            for key, label, origin in (
                ('dm02', 'DM 02 · пример из задания', 'Пример из задания.'),
                ('training', 'ТД-01 · неполные данные', 'Учебный вымышленный набор.'),
            )
        ]

        def intercept(route):
            path = urlsplit(route.request.url).path
            files = {'/': ('static/index.html', 'text/html'),
                     '/static/app.js': ('static/app.js', 'text/javascript'),
                     '/static/style.css': ('static/style.css', 'text/css')}
            if path in files:
                name, media_type = files[path]
                route.fulfill(path=str(ROOT / name), content_type=media_type)
            elif path == '/api/health':
                route.fulfill(json={'ready': True})
            elif path == '/api/examples':
                route.fulfill(json={'examples': self.examples})
            elif path in ('/api/examples/dm02/result', '/api/examples/training/result'):
                key = path.split('/')[-2]
                self.saved_requests.append(key)
                route.fulfill(json={'saved_example': True, 'origin': 'Сохранённый пример.',
                                    'result': json.loads((ROOT / 'examples' / f'{key}.result.json').read_text())})
            elif path == '/api/generate':
                self.requests.append(route.request.post_data_json)
                route.fulfill(status=422, json={'error': {'code': 'test_capture', 'message': 'Запрос перехвачен тестом.'}})
            else:
                route.abort()

        self.page.route('**/*', intercept)
        self.page.goto('http://127.0.0.1:8000/')

    def tearDown(self):
        self.page.close()

    def test_saved_example_opens_on_first_click(self):
        expect(self.page.locator('#example-select option')).to_have_count(3)
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        expect(self.page.locator('#saved-banner')).to_contain_text('сохранённый результат')
        expect(self.page.locator('#name')).to_have_value(self.examples[0]['input']['name'])
        self.assertEqual(self.saved_requests, ['dm02'])
        self.assertEqual(self.requests, [])

    def test_selected_saved_example_and_exports_open(self):
        self.page.locator('#example-select').select_option('training')
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        expect(self.page.locator('#preview')).to_contain_text('данных нет')
        self.page.locator('[data-output="json"]').click()
        expect(self.page.locator('#json-output')).to_be_visible()
        self.assertEqual(json.loads(self.page.locator('#json-output').inner_text())['missing_fields'],
                         ['manufacturer', 'specs.питание', 'specs.интерфейс'])
        self.page.locator('[data-output="markdown"]').click()
        expect(self.page.locator('#markdown-output')).to_contain_text('Характеристики')
        self.assertEqual(self.saved_requests, ['training'])
        self.assertEqual(self.requests, [])

    def test_example_loading_error_is_visible_beside_disabled_buttons(self):
        self.page.route('**/api/examples', lambda route: route.fulfill(
            status=503, json={'error': {'message': 'Примеры недоступны.'}}))
        self.page.reload()
        expect(self.page.locator('#example-origin')).to_contain_text('Не удалось загрузить примеры')
        expect(self.page.locator('#show-example')).to_be_disabled()
        expect(self.page.locator('#load-example')).to_be_disabled()
        expect(self.page.locator('#name')).to_be_editable()

    def test_invalid_json_preserves_input_and_previous_card(self):
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        previous = self.page.locator('#preview').text_content()
        self.page.locator('[data-input="json"]').click()
        invalid = (ROOT / 'evaluation/inputs/08-invalid-json.txt').read_text()
        self.page.locator('#json-input').fill(invalid)
        self.page.locator('#generate').click()
        expect(self.page.locator('#input-error')).to_contain_text('Некорректный JSON')
        expect(self.page.locator('#json-input')).to_have_value(invalid)
        expect(self.page.locator('#preview')).to_have_text(previous)
        expect(self.page.locator('#stale-banner')).to_be_visible()
        self.assertEqual(self.requests, [])
        self.page.locator('#json-input').fill(json.dumps(self.examples[0]['input'], ensure_ascii=False))
        self.page.locator('#generate').click()
        expect(self.page.locator('#error-banner')).to_contain_text('перехвачен')
        expect(self.page.locator('#preview')).to_have_text(previous)
        self.assertEqual(len(self.requests), 1)

    def test_copy_and_downloads_match_the_visible_card(self):
        self.page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        self.page.locator('[data-output="json"]').click()
        self.page.locator('#copy').click()
        expect(self.page.locator('#copy')).to_have_text('Скопировано')
        clipboard = self.page.evaluate('navigator.clipboard.readText()')
        self.assertEqual(json.loads(clipboard), json.loads(self.page.locator('#json-output').inner_text()))
        for button, output, filename in (
            ('#download-json', '#json-output', 'product-card.json'),
            ('#download-markdown', '#markdown-output', 'product-card.md'),
        ):
            with self.page.expect_download() as pending:
                self.page.locator(button).click()
            download = pending.value
            self.assertEqual(download.suggested_filename, filename)
            self.assertEqual(Path(download.path()).read_text(), self.page.locator(output).text_content())

    def test_unsafe_json_numbers_do_not_generate(self):
        self.page.locator('[data-input="json"]').click()
        for literal in ('9007199254740993', '-9007199254740993', '1e30', '1e309'):
            with self.subTest(number=literal):
                self.page.locator('#json-input').fill('{"name":"Прибор","specs":{"значение":' + literal + '}}')
                self.page.locator('#generate').click()
                expect(self.page.locator('#input-error')).to_contain_text('строк', timeout=1000)
                self.assertEqual(self.requests, [])

    def test_special_keys_survive_json_to_form_and_submission(self):
        specs = {'__proto__': 'исходное значение', 'constructor': 'обычный ключ',
                 'toString': None, 'питание': '24 В', 'дробное': 1.6, 'ноль': 0,
                 'безопасное': 9007199254740991, 'точное': '9007199254740993'}
        self.page.locator('[data-input="json"]').click()
        self.page.locator('#json-input').fill(json.dumps({'name': 'Прибор', 'specs': specs}, ensure_ascii=False))
        self.page.locator('[data-input="form"]').click()
        self.page.locator('#name').fill('Другой прибор')
        self.page.locator('#generate').click()
        expect(self.page.locator('#error-banner')).to_contain_text('перехвачен')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['specs'], specs)
        self.assertEqual(json.loads(self.page.locator('#json-input').input_value())['specs'], specs)


if __name__ == '__main__':
    unittest.main()
