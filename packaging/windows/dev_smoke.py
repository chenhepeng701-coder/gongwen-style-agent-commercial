"""Development-machine installed EXE acceptance. Synthetic inputs, loopback only.

This harness is never bundled and does not claim clean-machine or real-model E2E.
"""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
import winreg
import zipfile

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = '公文写作助手'
TEST_SECRET = 'TEST_SECRET_DO_NOT_USE_123'
REGISTRY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant'


def wait_for(condition, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError('Timed out waiting for package acceptance condition')


def windows_for(pid):
    user = ctypes.windll.user32
    found = []
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    @callback
    def visit(hwnd, _):
        process = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value == pid:
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, name, len(name))
            found.append((hwnd, name.value))
        return True
    user.EnumWindows(visit, 0)
    return found


def close_normally(process):
    user = ctypes.windll.user32
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    hwnd = wait_for(lambda: next((h for h, name in windows_for(process.pid) if name == 'GongwenDesktopControl'), None))
    user.PostMessageW(hwnd, 0x0010, 0, 0)
    dialog = wait_for(lambda: next((h for h, name in windows_for(process.pid) if name == '#32770'), None), 10)
    user.PostMessageW(dialog, 0x0111, 6, 0)  # Confirm the owned window's normal exit.
    assert process.wait(timeout=15) == 0


def file_hashes(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--inspect-seconds', type=int, default=0)
    args = parser.parse_args()
    build = args.build.resolve()
    assert build.is_relative_to(ROOT / 'tmp')
    installer = build / (APP_NAME + '-Setup.exe')
    assert installer.is_file()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY):
            raise AssertionError('An existing user installation must not be overwritten by this test')
    except FileNotFoundError:
        pass
    desktop_path = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop_path) == 0
    desktop_shortcut = Path(desktop_path.value) / (APP_NAME + '.lnk')
    start_menu = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs' / APP_NAME
    assert not desktop_shortcut.exists() and not start_menu.exists(), 'Never overwrite pre-existing shortcuts'
    work = ROOT / 'tmp' / ('rc01-dev-' + uuid.uuid4().hex[:8])
    install = work / 'installed'
    runtime = work / 'Local/GongwenWritingAssistant'
    work.mkdir()
    report = {'environment': 'DEVELOPMENT_MACHINE', 'real_model_calls': 0, 'real_embedding_calls': 0,
              'clean_machine_acceptance': 'NOT_RUN', 'runtime': str(runtime)}
    report['windows_process_elevated'] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    assert not report['windows_process_elevated'], 'Verify the installer/runtime with a normal user token'
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = None
    calls = {'llm': 0, 'embedding': 0}

    class FakeProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers.get('Authorization') == 'Bearer ' + TEST_SECRET
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/embeddings'):
                calls['embedding'] += 1
                data = {'data': [{'index': i, 'embedding': [1.0, 0.5, 0.25, 0.125]}
                                  for i, _ in enumerate(payload['input'])]}
            elif self.path.endswith('/chat/completions'):
                calls['llm'] += 1
                data = {'choices': [{'message': {'content': 'SYNTHETIC connection successful'}}]}
            else:
                raise AssertionError('Unexpected fake provider route')
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    fake = ThreadingHTTPServer(('127.0.0.1', 0), FakeProvider)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    env = {key: value for key, value in os.environ.items() if
           not any(word in key.upper() for word in ('API_KEY', 'PROXY', 'PYTHON', 'VITE', 'REFERENCE'))}
    env.update(LOCALAPPDATA=str(work / 'Local'), PATH=str(Path(os.environ['SystemRoot']) / 'System32'), NO_PROXY='*')
    # The installed launcher must strip these instead of accepting injection.
    env.update(GONGWEN_LLM_API_KEY=TEST_SECRET, GONGWEN_EMBEDDING_API_KEY=TEST_SECRET)
    state = runtime / 'config/desktop-instance.json'

    def start():
        child = subprocess.Popen([str(install / (APP_NAME + '.exe'))], env=env, cwd=install, startupinfo=startup)
        def state_when_ready():
            assert child.poll() is None, 'Installed launcher exited before becoming ready'
            return json.loads(state.read_text(encoding='utf-8')) if state.is_file() else None
        return child, wait_for(state_when_ready)

    try:
        subprocess.run([str(installer), '/S', '/D=' + str(install)], check=True, startupinfo=startup, timeout=90)
        assert (install / (APP_NAME + '.exe')).is_file()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
            assert winreg.QueryValueEx(key, 'DisplayVersion')[0] == '0.1.0'
        shortcuts = [desktop_shortcut, start_menu / (APP_NAME + '.lnk')]
        assert all(path.is_file() for path in shortcuts)
        before = file_hashes(install)
        for relative, digest in file_hashes(build / 'dist' / APP_NAME).items():
            assert before[relative] == digest, 'Installed payload must match the verified package'
        report['installer'] = 'PASS'
        process, instance = start()
        wait_for(lambda: any(name == 'GongwenDesktopControl' for _, name in windows_for(process.pid)))
        url = f'http://127.0.0.1:{instance["port"]}'
        report['dev_smoke_stateful_origin'] = url
        with httpx.Client(base_url=url, headers={'Origin': url}, trust_env=False, timeout=30) as client:
            assert client.get('/api/health').headers['x-gongwen-instance'] == instance['identity']
            assert '<div id="root">' in client.get('/').text
            assert client.get('/api/not-found').status_code == 404
            listeners = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
                f"Get-NetTCPConnection -State Listen -OwningProcess {process.pid} | Select-Object -ExpandProperty LocalAddress"], text=True)
            assert set(listeners.split()) == {'127.0.0.1'}, listeners
            report['loopback_only'] = 'PASS'
            second = subprocess.Popen([str(install / (APP_NAME + '.exe'))], env=env, cwd=install, startupinfo=startup)
            assert second.wait(timeout=15) == 0
            assert json.loads(state.read_text()) == instance
            report['single_instance'] = 'PASS'
            assert not client.get('/api/settings').json()['api_key_configured']
            assert not client.get('/api/settings/embedding').json()['api_key_configured']
            report['packaged_env_secret_override_enabled'] = False
            with httpx.Client(base_url=url, trust_env=False) as foreign:
                assert foreign.get('/api/health').status_code == 200
                assert foreign.head('/').status_code == 200
                for method, endpoint in (('POST', '/api/settings'), ('PUT', '/api/library/documents/missing/tags'),
                                         ('DELETE', '/api/library/documents/missing'), ('PATCH', '/api/settings')):
                    assert foreign.request(method, endpoint, json={}).status_code == 403
                for origin in ('null', url.replace('127.0.0.1', 'localhost'), url + '/',
                               url.replace('http:', 'https:'), 'https://invalid.example'):
                    assert foreign.post('/api/settings', headers={'Origin': origin}, json={}).status_code == 403
            report['stateful_origin_required'] = 'PASS'
            fake_url = f'http://127.0.0.1:{fake.server_port}'
            llm = dict(provider_key='deepseek', api_key=TEST_SECRET, base_url=fake_url, model='offline')
            embedding = dict(api_key=TEST_SECRET, base_url=fake_url, model='offline')
            for path, data in (('', llm), ('/embedding', embedding)):
                assert client.post('/api/settings' + path, json=data).json()['success']
                public = client.get('/api/settings' + path)
                assert public.json()['api_key_configured'] and data['api_key'] not in public.text
                assert client.post('/api/settings' + path + '/test', json={**data, 'api_key': ''}).json()['success']
            report['model_configuration_offline'] = 'PASS'
            assert len(list((runtime / 'config/secrets').glob('*.dpapi'))) == 2
            for path in runtime.rglob('*'):
                # Windows locks this byte while running. The complete scan below
                # includes desktop.lock after the owned application exits.
                if path.is_file() and path != runtime / 'config/desktop.lock':
                    assert TEST_SECRET.encode() not in path.read_bytes(), path.name
                    assert TEST_SECRET.encode('utf-16-le') not in path.read_bytes(), path.name
            report['secure_secret_storage'] = 'DPAPI_CURRENT_USER_PASS'
            fixture = (ROOT / 'backend/tests/fixtures/writing_reference_pack_synthetic.json').read_bytes()
            assert client.post('/api/settings/writing-reference', files={'file': ('synthetic.json', fixture)}).json() == {'configured': True}
            assert client.post('/api/settings/writing-reference', files={'file': ('invalid.json', b'{}')}).status_code == 422
            report['writing_reference'] = 'PASS'
            material = ('合成测试工作总结\n一、工作情况\n' + '测试单位组织安全检查，建立问题记录并明确整改责任。' * 65).encode()
            uploaded = client.post('/api/library/upload', files={'file': ('synthetic.txt', material, 'text/plain')})
            assert uploaded.status_code == 200, uploaded.text
            doc_id = uploaded.json()['document']['id']
            wait_for(lambda: client.get('/api/library/documents/' + doc_id).json().get('ingestion_status') == 'READY')
            report['upload_to_ready_offline'] = 'PASS'
            exported = client.post('/api/generation/export/word', json={'title': '合成验收稿', 'content': '一、工作情况\n本段为合成测试文本。'})
            assert exported.status_code == 200, exported.text
            assert 'word/document.xml' in zipfile.ZipFile(io.BytesIO(exported.content)).namelist()
            assert list((runtime / 'output').glob('*.docx'))
            rejected = client.post('/api/generation/export/word', json={'title': '合成越界检查', 'content': '合成文本',
                'output_path': str(install / 'forbidden.docx')})
            assert rejected.status_code >= 400
            assert not (install / 'forbidden.docx').exists()
            assert before == file_hashes(install), 'Installed files must remain read-only'
            report['word_export'] = 'PASS'
            report['installed_files_unchanged'] = 'PASS'
            if args.inspect_seconds:
                print(json.dumps({'inspection_url': url, 'seconds': min(args.inspect_seconds, 180)}), flush=True)
                time.sleep(min(args.inspect_seconds, 180))
        close_normally(process)
        process = None
        with socket.socket() as connection:
            assert connection.connect_ex(('127.0.0.1', instance['port'])) != 0
        report['normal_exit_stops_service'] = 'PASS'
        process, restarted = start()
        restart_url = f'http://127.0.0.1:{restarted["port"]}'
        with httpx.Client(base_url=restart_url, headers={'Origin': restart_url}, trust_env=False) as client:
            assert client.get('/api/settings').json()['api_key_configured']
            assert client.get('/api/settings/embedding').json()['api_key_configured']
            assert client.get('/api/settings/writing-reference').json()['configured']
            assert client.get('/api/library/documents/' + doc_id).json()['ingestion_status'] == 'READY'
            for path, data in (('', llm), ('/embedding', embedding)):
                assert client.post('/api/settings' + path + '/test', json={**data, 'api_key': ''}).json()['success']
        report['restart_persistence'] = 'PASS'
        close_normally(process)
        process = None
        # Upgrade scenario: a new legacy provider migrates inside the real EXE.
        configuration = runtime / 'config/config.yaml'
        legacy = yaml.safe_load(configuration.read_text(encoding='utf-8'))
        legacy['providers']['legacy_provider'] = {**legacy['providers']['deepseek'], 'api_key': TEST_SECRET}
        configuration.write_text(yaml.safe_dump(legacy), encoding='utf-8')
        process, migrated = start()
        migration_url = f'http://127.0.0.1:{migrated["port"]}'
        with httpx.Client(base_url=migration_url, headers={'Origin': migration_url}, trust_env=False) as client:
            assert client.get('/api/settings', params={'provider_key': 'legacy_provider'}).json()['api_key_configured']
        assert len(list((runtime / 'config/secrets').glob('*.dpapi'))) == 3
        assert TEST_SECRET not in configuration.read_text(encoding='utf-8')
        close_normally(process)
        process = None
        report['packaged_legacy_migration'] = 'PASS'
        for path in runtime.rglob('*'):
            if path.is_file():
                assert TEST_SECRET.encode() not in path.read_bytes(), path.name
                assert TEST_SECRET.encode('utf-16-le') not in path.read_bytes(), path.name
        report['runtime_plaintext_secret_scan'] = 'PASS'
        for path in (runtime / 'logs').rglob('*'):
            if path.is_file():
                assert TEST_SECRET.encode() not in path.read_bytes()
        subprocess.run([str(install / 'Uninstall.exe'), '/S'], check=True, startupinfo=startup, timeout=60)
        def uninstall_finished():
            # NSIS hands off to its temporary uninstaller process; directory
            # removal precedes shortcut/registry cleanup in the uninstall script.
            if install.exists() or desktop_shortcut.exists() or start_menu.exists():
                return False
            for key in (REGISTRY, r'Software\GongwenWritingAssistant'):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                        return False
                except FileNotFoundError:
                    pass
            return True
        wait_for(uninstall_finished)
        assert not desktop_shortcut.exists() and not start_menu.exists()
        assert (runtime / 'config/config.yaml').is_file()
        assert list((runtime / 'uploads').glob('*.txt'))
        report['uninstall_preserves_user_data'] = 'PASS'
        report['fake_provider_calls'] = calls
        report['result'] = 'PASS'
    except Exception as exc:
        report['result'] = 'BLOCK'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            # Only this harness's child, never another installation or service.
            process.terminate()
            process.wait(timeout=10)
        fake.shutdown()
        (build / 'dev-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
