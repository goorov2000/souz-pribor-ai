"""Run repeatable checks from an isolated source snapshot, with no API credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('app', 'static', 'tests', 'docs', 'examples', 'evaluation', 'scripts', '.github')
SOURCE_FILES = ('pyproject.toml', 'uv.lock', 'README.md', 'DESIGN.md')
SUFFIXES = {'.py', '.js', '.css', '.html', '.json', '.md', '.txt', '.yml', '.yaml', '.woff2', '.svg', '.png'}


def snapshot(destination, artifact):
    paths = [ROOT / name for name in SOURCE_FILES]
    for name in SOURCE_DIRS:
        paths += [path for path in (ROOT / name).rglob('*')
                  if path.is_file() and path.suffix in SUFFIXES
                  and not any(part.startswith('.') and part != '.github' for part in path.relative_to(ROOT).parts)
                  and '__pycache__' not in path.parts]
    manifest = {}
    with zipfile.ZipFile(artifact / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for source in sorted(set(paths)):
            if source.is_symlink():
                raise RuntimeError('Source snapshot cannot contain symlinks: ' + str(source.relative_to(ROOT)))
            relative = source.relative_to(ROOT)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
            bundle.write(source, str(relative))
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (artifact / 'source.json').write_text(json.dumps({'sha256': fingerprint, 'files': manifest}, indent=2) + '\n')
    return fingerprint


def clean_environment():
    environment = os.environ.copy()
    for name in ('VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'PYTHONPATH', 'PYTHONHOME', 'OPENAI_BASE_URL'):
        environment.pop(name, None)
    environment.update(OPENAI_API_KEY='', OPENAI_MODEL='gpt-6-luna', PYTHONUNBUFFERED='1')
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prove-regressions', action='store_true', help='Also prove the browser check rejects the previous broken first-click behavior')
    args = parser.parse_args()
    artifact = ROOT / 'artifacts/check' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    artifact.mkdir(parents=True)
    report = {'status': 'RUNNING', 'started_at': datetime.now(timezone.utc).isoformat(),
              'stages': [], 'live_llm_evaluation': 'NOT_RUN', 'paid_api_calls': 0}

    def save():
        (artifact / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        lines = ['# Проверка приложения', '', 'Статус: ' + report['status'], '',
                 'Живая смысловая оценка LLM: NOT_RUN. API-ключи в прогон не передаются.', '',
                 '| Этап | Статус | Время, с | Журнал |', '| --- | --- | --- | --- |']
        lines += [f"| {row['name']} | {row['status']} | {row['seconds']} | [{row['log']}]({row['log']}) |" for row in report['stages']]
        if report.get('error'):
            lines += ['', 'Ошибка: ' + report['error']]
        (artifact / 'report.md').write_text('\n'.join(lines) + '\n')

    save()
    try:
        if not shutil.which('uv'):
            raise RuntimeError('Install uv before running this command.')
        with tempfile.TemporaryDirectory(prefix='pribor-check-') as directory:
            workspace = Path(directory)
            report['source_sha256'] = snapshot(workspace, artifact)
            environment = clean_environment()
            environment['PRIBOR_CHECK_ARTIFACTS'] = str(artifact)

            def stage(name, command, expected_failure=False, timeout=180):
                print(name + '…', flush=True)
                started = time.perf_counter()
                log = artifact / (name + '.log')
                with log.open('w') as stream:
                    process = subprocess.Popen(command, cwd=workspace, env=environment,
                                               stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
                    try:
                        exit_code = process.wait(timeout=timeout)
                    except BaseException:
                        if os.name != 'nt':
                            os.killpg(process.pid, signal.SIGTERM)
                        else:
                            process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            if os.name != 'nt':
                                os.killpg(process.pid, signal.SIGKILL)
                            else:
                                process.kill()
                            process.wait()
                        raise
                passed = exit_code != 0 if expected_failure else exit_code == 0
                report['stages'].append({'name': name, 'status': 'PASS' if passed else 'FAIL',
                                         'seconds': round(time.perf_counter() - started, 2),
                                         'exit_code': exit_code, 'expected_failure': expected_failure,
                                         'log': log.name})
                save()
                print(name + ': ' + ('PASS' if passed else 'FAIL'), flush=True)
                if not passed:
                    print(log.read_text()[-8000:], flush=True)
                    raise RuntimeError('Check failed: ' + name)

            stage('install', ['uv', 'sync', '--frozen', '--group', 'dev', '--python', sys.executable], timeout=600)
            python = workspace / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            chromium = environment.get('CHROMIUM_EXECUTABLE_PATH')
            if chromium:
                if not Path(chromium).is_file():
                    raise RuntimeError('CHROMIUM_EXECUTABLE_PATH does not point to a browser')
            else:
                stage('browser-install', [str(python), '-m', 'playwright', 'install', 'chromium'], timeout=600)
            stage('api-and-saved-data', [str(python), '-m', 'pytest', '-q', '--junitxml=' + str(artifact / 'pytest.xml')])
            stage('isolated-client', [str(python), 'docs/check_client.py'])
            stage('real-server-browser', [str(python), 'docs/check_e2e.py'])
            if args.prove_regressions:
                source = workspace / 'static/app.js'
                original = source.read_text()
                needle = "$('example-select').value = state.examples[0].id;"
                if original.count(needle) != 1:
                    raise RuntimeError('Cannot reproduce the previous first-click regression automatically')
                source.write_text(original.replace(needle, "$('example-select').value = '';"))
                try:
                    stage('reject-broken-first-click', [str(python), 'docs/check_e2e.py',
                          'SavedExamplesTests.test_saved_example_opens_on_first_click'], expected_failure=True)
                    if 'AssertionError' not in (artifact / 'reject-broken-first-click.log').read_text():
                        raise RuntimeError('Regression probe failed for an unexpected reason')
                finally:
                    source.write_text(original)
        report['status'] = 'PASS'
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'FAIL'
        report['error'] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
        print('Report: ' + str(artifact / 'report.md'), flush=True)
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
