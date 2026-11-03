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
            files['/static/brand/souz-pribor.svg'] = ('static/brand/souz-pribor.svg', 'image/svg+xml')
            files.update({'/static/vendor/' + file.name: (str(file.relative_to(ROOT)), 'font/woff2' if file.suffix == '.woff2' else 'text/css')
                          for file in (ROOT / 'static/vendor').iterdir() if file.suffix in ('.woff2', '.css')})
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

    def test_saved_sparse_example_shows_guidance_instead_of_a_long_card(self):
        self.page.locator('#example-select').select_option('training')
        self.page.locator('#show-example').click()
        expect(self.page.locator('#needs-data')).to_be_visible()
        expect(self.page.locator('#known-summary')).to_contain_text('−20…+80 °C')
        expect(self.page.locator('#needs-data-meta')).to_contain_text('Новая генерация не запускалась')
        expect(self.page.locator('#result')).to_be_hidden()
        self.assertEqual(self.saved_requests, ['training'])
        self.assertEqual(self.requests, [])

    def test_example_loading_error_is_visible_beside_disabled_buttons(self):
        self.page.route('**/api/examples', lambda route: route.fulfill(
            status=503, json={'error': {'message': 'Примеры недоступны.'}}))
        self.page.reload()
        expect(self.page.locator('#example-origin')).to_contain_text('Не удалось загрузить примеры')
        expect(self.page.locator('#example-origin')).to_be_visible()
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

    def test_seo_block_copy_contains_only_the_description(self):
        self.page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        self.page.get_by_role('button', name='Копировать SEO-описание', exact=True).click()
        clipboard = self.page.evaluate('navigator.clipboard.readText()')
        expected = json.loads((ROOT / 'examples/dm02.result.json').read_text())['card']['seo_description']
        self.assertEqual(clipboard, expected)

    def test_commercial_is_above_characteristics_and_both_blocks_copy_exactly(self):
        self.page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        self.page.locator('#show-example').click()
        expect(self.page.locator('.commercial-section')).to_be_visible()
        self.assertLess(self.page.locator('.commercial-section').bounding_box()['y'],
                        self.page.locator('#preview table').bounding_box()['y'])
        card = json.loads((ROOT / 'examples/dm02.result.json').read_text())['card']
        self.page.get_by_role('button', name='Копировать Commercial-блок для менеджера', exact=True).click()
        self.assertEqual(self.page.evaluate('navigator.clipboard.readText()'), card['commercial_block'])
        self.page.get_by_role('button', name='Копировать Характеристики для карточки', exact=True).click()
        self.assertEqual(self.page.evaluate('navigator.clipboard.readText()'), '\n'.join(row['name'] + '\t' + row['value'] for row in card['characteristics']))

    def test_enrichment_option_roundtrips_through_json_and_reload(self):
        self.page.locator('#load-example').click()
        expect(self.page.locator('#enrich')).to_be_checked()
        self.page.locator('#enrich').uncheck()
        self.page.locator('#json-tab').click()
        self.assertFalse(json.loads(self.page.locator('#json-input').input_value())['enrich'])
        self.page.reload()
        self.page.locator('#form-tab').click()
        expect(self.page.locator('#enrich')).not_to_be_checked()
        self.page.locator('#generate').click()
        expect(self.page.locator('#error-banner')).to_contain_text('перехвачен')
        self.assertFalse(self.requests[0]['enrich'])

    def test_reload_restores_invalid_json_and_previous_card_with_stale_warning(self):
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        previous = self.page.locator('#preview').text_content()
        self.page.locator('[data-input="json"]').click()
        self.page.locator('#json-input').fill('{"name":')
        self.page.reload()
        expect(self.page.locator('#json-input')).to_be_visible()
        expect(self.page.locator('#json-input')).to_have_value('{"name":')
        expect(self.page.locator('#preview')).to_have_text(previous)
        expect(self.page.locator('#stale-banner')).to_be_visible()
        self.assertEqual(self.requests, [])

    def test_needs_data_keeps_the_previous_card_and_provides_a_next_step(self):
        self.page.locator('#show-example').click()
        expect(self.page.locator('#result')).to_be_visible()
        previous = self.page.locator('#preview').text_content()
        self.page.route('**/api/generate', lambda route: route.fulfill(json={
            'status': 'needs_data', 'api_called': False, 'message': 'Добавьте характеристики.',
            'missing_fields': ['description', 'specs'], 'next_steps': ['Вставьте фрагмент паспорта.']}))
        self.page.locator('[data-input="json"]').click()
        self.page.locator('#json-input').fill('{"name":"Прибор"}')
        self.page.locator('#generate').click()
        expect(self.page.locator('#needs-data')).to_be_visible()
        expect(self.page.locator('#result-badge')).to_have_text('Нужны данные')
        expect(self.page.locator('#preview')).to_have_text(previous)
        expect(self.page.locator('#stale-banner')).to_be_visible()
        expect(self.page.locator('#preview')).to_be_hidden()
        self.page.locator('#previous-summary').click()
        expect(self.page.locator('#preview')).to_be_visible()
        self.page.locator('#complete-source').click()
        expect(self.page.locator('#description')).to_be_focused()
        self.page.locator('#description').fill('Измеряет температуру. Питание: 24 В.')
        expect(self.page.locator('#needs-data')).to_be_hidden()

    def test_narrow_screen_and_keyboard_tabs_remain_usable(self):
        self.page.set_viewport_size({'width': 320, 'height': 850})
        self.page.locator('#form-tab').focus()
        self.page.keyboard.press('ArrowRight')
        expect(self.page.locator('#json-input')).to_be_visible()
        expect(self.page.locator('#json-tab')).to_be_focused()
        self.page.keyboard.press('Home')
        expect(self.page.locator('#name')).to_be_visible()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))


if __name__ == '__main__':
    unittest.main()
