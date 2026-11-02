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
                route.fulfill(json={'examples': []})
            elif path == '/api/generate':
                self.requests.append(route.request.post_data_json)
                route.fulfill(status=422, json={'error': {'code': 'test_capture', 'message': 'Запрос перехвачен тестом.'}})
            else:
                route.abort()

        self.page.route('**/*', intercept)
        self.page.goto('http://127.0.0.1:8000/')

    def tearDown(self):
        self.page.close()

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
