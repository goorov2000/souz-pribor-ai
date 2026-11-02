"""Cold-start real servers and browsers; never intercept application HTTP requests."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = Path(os.environ.get('PRIBOR_CHECK_ARTIFACTS', ROOT / 'artifacts/e2e'))
ARTIFACT.mkdir(parents=True, exist_ok=True)


class Server:
    def __init__(self, module, name):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.log = (ARTIFACT / (name + '-server.log')).open('w')
        environment = os.environ.copy()
        environment['OPENAI_API_KEY'] = ''
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', str(port)],
                                        cwd=ROOT, env=environment, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Server exited before readiness: ' + module)
                try:
                    with urlopen(self.url + '/api/health', timeout=0.5) as response:
                        if response.status == 200:
                            return
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.05)
            raise RuntimeError('Server readiness timed out: ' + module)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


class BrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(cls.entrypoint, cls.__name__)
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(
                executable_path=os.environ.get('CHROMIUM_EXECUTABLE_PATH') or cls.playwright.chromium.executable_path,
                headless=True, args=['--no-sandbox'])
        except BaseException:
            cls.playwright.stop()
            cls.server.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.close()

    def setUp(self):
        self.context = self.browser.new_context(permissions=['clipboard-read', 'clipboard-write'])
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.addCleanup(self.capture)
        self.page.goto(self.server.url)
        expect(self.page.get_by_label('Начать с примера')).to_be_enabled()

    def capture(self):
        name = self.id().split('.')[-1]
        try:
            self.page.screenshot(path=str(ARTIFACT / (name + '.png')), full_page=True)
        finally:
            try:
                self.context.tracing.stop(path=str(ARTIFACT / (name + '.zip')))
            finally:
                self.context.close()

    def open_saved(self):
        self.page.get_by_role('button', name=re.compile('^Посмотреть сохранённый результат')).click()
        expect(self.page.locator('#result')).to_be_visible()


class SavedExamplesTests(BrowserCase):
    entrypoint = 'app.main:app'

    def test_saved_example_opens_on_first_click(self):
        # Assert the precondition before clicking: an unselected initial state is a failure.
        expect(self.page.get_by_label('Начать с примера')).to_have_value('dm02')
        self.open_saved()
        expect(self.page.locator('#preview')).to_contain_text('0–1,6 МПа')
        expect(self.page.locator('#saved-banner')).to_be_visible()

    def test_real_example_and_download_without_api_key(self):
        expect(self.page.locator('#config-status')).to_contain_text('OPENAI_API_KEY')
        self.page.get_by_label('Начать с примера').select_option('testo805i')
        self.open_saved()
        expect(self.page.locator('#preview')).to_contain_text('-30 to +250 °C')
        self.page.get_by_role('tab', name='JSON', exact=True).last.click()
        expected = self.page.locator('#json-output').text_content()
        self.page.get_by_role('button', name='Копировать', exact=True).click()
        expect(self.page.get_by_role('button', name='Скопировано')).to_be_visible()
        self.assertEqual(json.loads(self.page.evaluate('navigator.clipboard.readText()')), json.loads(expected))
        with self.page.expect_download() as pending:
            self.page.get_by_role('button', name=re.compile('^Скачать JSON')).click()
        self.assertEqual(json.loads(Path(pending.value.path()).read_text()), json.loads(expected))

    def test_missing_key_preserves_previous_card(self):
        self.open_saved()
        previous = self.page.locator('#preview').text_content()
        self.page.get_by_role('button', name=re.compile('^Создать карточку')).click()
        expect(self.page.locator('#error-banner')).to_contain_text('OPENAI_API_KEY')
        expect(self.page.locator('#preview')).to_have_text(previous)
        expect(self.page.get_by_label('Название прибора')).not_to_be_empty()

    def test_invalid_json_never_posts_and_keeps_previous_card(self):
        self.open_saved()
        previous = self.page.locator('#preview').text_content()
        posted = []
        self.page.on('request', lambda request: posted.append(request.url) if request.method == 'POST' else None)
        self.page.get_by_role('tab', name='JSON', exact=True).first.click()
        invalid = (ROOT / 'evaluation/inputs/08-invalid-json.txt').read_text()
        self.page.get_by_label('Данные товара в JSON').fill(invalid)
        self.page.get_by_role('button', name=re.compile('^Создать карточку')).click()
        expect(self.page.locator('#input-error')).to_contain_text('Некорректный JSON')
        expect(self.page.get_by_label('Данные товара в JSON')).to_have_value(invalid)
        expect(self.page.locator('#preview')).to_have_text(previous)
        self.assertEqual(posted, [])


class GenerationTests(BrowserCase):
    entrypoint = 'scripts.e2e_server:app'

    def generate(self):
        self.page.get_by_role('button', name='Вставить', exact=True).click()
        self.page.get_by_role('button', name=re.compile('^Создать карточку')).click()
        expect(self.page.locator('#result-badge')).to_have_text('Черновик готов')

    def test_form_to_generation_to_markdown(self):
        self.generate()
        expect(self.page.locator('#saved-banner')).to_be_hidden()
        expect(self.page.locator('#preview')).to_contain_text('100 мм')
        self.page.get_by_role('tab', name='JSON', exact=True).last.click()
        card = json.loads(self.page.locator('#json-output').text_content())
        self.assertEqual({row['name']: row['value'] for row in card['characteristics'] if row['source'] == 'specs'},
                         {'диапазон измерений': '0–1,6 МПа', 'класс точности': '1,5', 'присоединение': 'G1/2', 'диаметр корпуса': '100 мм'})
        with self.page.expect_download() as pending:
            self.page.get_by_role('button', name='Markdown ↓', exact=True).click()
        content = Path(pending.value.path()).read_text()
        self.assertIn('0–1,6 МПа', content)
        self.assertIn('\n\n', content)

    def test_provider_error_preserves_work_and_allows_retry(self):
        self.generate()
        previous = self.page.locator('#preview').text_content()
        self.page.get_by_label('Название прибора').fill('Сбой API')
        self.page.get_by_role('button', name=re.compile('^Создать карточку')).click()
        expect(self.page.locator('#error-banner')).to_contain_text('лимит')
        expect(self.page.locator('#preview')).to_have_text(previous)
        expect(self.page.locator('#stale-banner')).to_be_visible()
        self.page.get_by_label('Название прибора').fill('Манометр показывающий DM 02')
        self.page.get_by_role('button', name=re.compile('^Создать карточку')).click()
        expect(self.page.locator('#result-badge')).to_have_text('Черновик готов')
        expect(self.page.locator('#error-banner')).to_be_hidden()


class StartupTests(unittest.TestCase):
    def test_failed_server_cannot_pass_readiness(self):
        with self.assertRaisesRegex(RuntimeError, 'Server exited before readiness'):
            Server('module_that_does_not_exist:app', 'expected-startup-failure')


if __name__ == '__main__':
    unittest.main()
