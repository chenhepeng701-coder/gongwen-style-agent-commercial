"""Installed desktop acceptance support. UI actions are performed separately.

Never bundled. Uses a fresh Runtime Root and backs up existing shell integration.
Activating the real desktop .lnk uses the same Windows ShellExecute path as a
double-click, with LOCALAPPDATA isolated for the launched application.
"""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import winreg
import socket
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
NAME = '公文写作助手'
POWERSHELL = shutil.which('pwsh') or 'powershell.exe'
KEYS = [r'Software\GongwenWritingAssistant',
        r'Software\Microsoft\Windows\CurrentVersion\Uninstall\GongwenWritingAssistant']


def wait(condition, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(.2)
    raise AssertionError('Desktop acceptance timeout')


def ps(command):
    return subprocess.check_output([POWERSHELL, '-NoProfile',
                                    '-NonInteractive', '-Command', command], text=True, encoding='utf-8-sig')


def browsers():
    return json.loads(ps("@(Get-Process chrome,msedge -ErrorAction SilentlyContinue | Select-Object ProcessName,Id) | ConvertTo-Json -Compress"))


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def registry():
    result = {}
    for path in KEYS:
        values = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    values.append(winreg.EnumValue(key, i))
            result[path] = values
        except FileNotFoundError:
            result[path] = None
    return result


def shell_launch(shortcut, wait_exit=False):
    class Execute(ctypes.Structure):
        _fields_ = [('cbSize', w.DWORD), ('fMask', w.ULONG), ('hwnd', w.HWND),
                    ('lpVerb', w.LPCWSTR), ('lpFile', w.LPCWSTR), ('lpParameters', w.LPCWSTR),
                    ('lpDirectory', w.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', w.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', w.LPCWSTR), ('hkeyClass', w.HKEY),
                    ('dwHotKey', w.DWORD), ('hIcon', w.HANDLE), ('hProcess', w.HANDLE)]
    call = Execute(cbSize=ctypes.sizeof(Execute), fMask=0x40 | 0x100, lpVerb='open',
                   lpFile=str(shortcut), nShow=1)
    assert ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(call))
    if call.hProcess:
        if wait_exit:
            kernel = ctypes.windll.kernel32
            kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
            assert kernel.WaitForSingleObject(call.hProcess, 55000) == 0, 'Second process did not exit'
            code = w.DWORD()
            assert kernel.GetExitCodeProcess(call.hProcess, ctypes.byref(code)) and code.value == 0
        ctypes.windll.kernel32.CloseHandle.argtypes = [w.HANDLE]
        ctypes.windll.kernel32.CloseHandle(call.hProcess)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['install', 'upgrade', 'launch', 'snapshot', 'audit', 'closed', 'uninstall'])
    parser.add_argument('--build', type=Path)
    parser.add_argument('--work', type=Path)
    args = parser.parse_args()
    assert not ctypes.windll.shell32.IsUserAnAdmin(), 'Acceptance must run non-admin'
    if args.phase == 'install':
        work = ROOT / 'tmp' / ('dns01-' + uuid.uuid4().hex[:8])
        work.mkdir()
        desktop = ctypes.create_unicode_buffer(32768)
        assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop) == 0
        menu = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs' / NAME
        links = [Path(desktop.value) / (NAME + '.lnk'), menu / (NAME + '.lnk'), menu / '卸载.lnk']
        prior = {}
        for index, link in enumerate(links):
            if link.is_file():
                backup = work / ('prior-shortcut-' + str(index) + '.lnk')
                shutil.copyfile(link, backup)
                prior[str(link)] = str(backup)
        report = {'build': str(args.build.resolve()), 'work': str(work), 'prior_registry': registry(),
                  'prior_shortcuts': prior, 'desktop_shortcut': str(links[0]),
                  'install': str(work / 'installed'), 'runtime': str(work / 'Local/GongwenWritingAssistant'),
                  'browser_before': browsers(), 'real_model_calls': 0, 'real_embedding_calls': 0,
                  'launch_method': 'Windows ShellExecuteEx on installed desktop shortcut',
                  'non_admin': True}
        (work / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        subprocess.run([str(args.build.resolve() / (NAME + '-Setup.exe')), '/S', '/D=' + report['install']],
                       check=True, timeout=120)
        assert all(p.is_file() for p in links)
        assert (Path(report['install']) / (NAME + '.exe')).is_file()
        report['installed_hashes'] = hashes(Path(report['install']))
        for path, digest in hashes(args.build / 'dist' / NAME).items():
            assert report['installed_hashes'][path] == digest
        report['installer'] = report['desktop_shortcut_created'] = 'PASS'
    else:
        work = args.work.resolve()
        assert work.parent == ROOT / 'tmp' and work.name.startswith('dns01-')
        report = json.loads((work / 'acceptance.json').read_text(encoding='utf-8'))
        runtime = Path(report['runtime'])
        state = runtime / 'config/desktop-instance.json'
        if args.phase == 'upgrade':
            assert not state.exists()
            subprocess.run([str(args.build.resolve() / (NAME + '-Setup.exe')), '/S', '/D=' + report['install']],
                           check=True, timeout=120)
            report['build'] = str(args.build.resolve())
            report['installed_hashes'] = hashes(Path(report['install']))
            for path, digest in hashes(args.build / 'dist' / NAME).items():
                assert report['installed_hashes'][path] == digest
        elif args.phase == 'launch':
            for key in tuple(os.environ):
                if any(word in key.upper() for word in ('API_KEY', 'PYTHON', 'VITE', 'REFERENCE', 'PROXY')):
                    os.environ.pop(key, None)
            os.environ['LOCALAPPDATA'] = str(work / 'Local')
            os.environ['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
            os.environ['NO_PROXY'] = '*'
            shell_launch(Path(report['desktop_shortcut']), wait_exit=state.exists())
            instance = wait(lambda: json.loads(state.read_text(encoding='utf-8')) if state.is_file() else None)
            report.setdefault('launches', []).append(instance)
            report['browser_after_launch'] = browsers()
            print(json.dumps(instance))
        elif args.phase == 'snapshot':
            instance = json.loads(state.read_text(encoding='utf-8'))
            pid = instance['pid']
            report['listeners'] = json.loads(ps(f'@(Get-NetTCPConnection -State Listen -OwningProcess {pid} | Select-Object LocalAddress,LocalPort) | ConvertTo-Json -Compress'))
            report['browser_after_snapshot'] = browsers()
            user = ctypes.windll.user32
            for name in ('IsIconic', 'IsZoomed', 'IsWindowVisible'):
                getattr(user, name).argtypes = [w.HWND]
            user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
            user.GetForegroundWindow.restype = w.HWND
            rect = w.RECT()
            assert user.GetWindowRect(instance['hwnd'], ctypes.byref(rect))
            native = {'minimized': bool(user.IsIconic(instance['hwnd'])),
                      'maximized': bool(user.IsZoomed(instance['hwnd'])),
                      'foreground': user.GetForegroundWindow() == instance['hwnd'],
                      'visible': bool(user.IsWindowVisible(instance['hwnd'])),
                      'rect': [rect.left, rect.top, rect.right, rect.bottom]}
            report.setdefault('native_snapshots', []).append(native)
            report['install_unchanged'] = hashes(Path(report['install'])) == report['installed_hashes']
            assert report['install_unchanged']
            assert (runtime / 'webview').is_dir()
            print(json.dumps({'instance': instance, 'listeners': report['listeners'], 'install_unchanged': True, 'native': native}))
        elif args.phase == 'audit':
            instance = json.loads(state.read_text(encoding='utf-8'))
            origin = 'http://127.0.0.1:' + str(instance['port'])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            codes = {}
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                codes[method] = []
                for value in (None, origin, 'http://localhost:' + str(instance['port'])):
                    request = urllib.request.Request(origin + '/api/desktop-boundary-test-missing',
                                                     method=method, headers={'Origin': value} if value else {})
                    try:
                        response = opener.open(request, timeout=3)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        codes[method].append(response.status)
                assert codes[method] == [403, 404, 403]
            processes = json.loads(ps('Get-CimInstance Win32_Process | Select-Object Name,ProcessId,ParentProcessId,CreationDate | ConvertTo-Json -Compress'))
            descendants = {instance['pid']}
            while True:
                expanded = descendants | {p['ProcessId'] for p in processes if p['ParentProcessId'] in descendants}
                if expanded == descendants:
                    break
                descendants = expanded
            owned = [p for p in processes if p['ProcessId'] in descendants]
            assert any(p['Name'] == 'msedgewebview2.exe' for p in owned)
            assert not any(p['Name'].lower() in {'chrome.exe', 'msedge.exe', 'conhost.exe', 'cmd.exe', 'powershell.exe'} for p in owned)
            webview_pid = next(p['ProcessId'] for p in owned if p['Name'] == 'msedgewebview2.exe')
            version = ps(f'(Get-Process -Id {webview_pid}).MainModule.FileVersionInfo.ProductVersion').strip()
            report['runtime_version_in_use'] = version
            report['origin_boundary_codes'] = codes
            report.setdefault('owned_process_snapshots', []).append(owned)
            report['browser_after_audit'] = browsers()
            report['state_after_cancel'] = instance
            print(json.dumps({'boundary_codes': codes, 'runtime_version': version, 'owned_processes': owned}))
        elif args.phase == 'closed':
            instance = report['launches'][-1]
            wait(lambda: not state.exists())
            wait(lambda: ps(f'@(Get-Process -Id {instance["pid"]} -ErrorAction SilentlyContinue).Count -eq 0').strip() == 'True')
            with socket.socket() as probe:
                probe.settimeout(2)
                assert probe.connect_ex(('127.0.0.1', instance['port'])) != 0
            if report.get('owned_process_snapshots'):
                for process in report['owned_process_snapshots'][-1]:
                    wait(lambda: ps(f'@(Get-Process -Id {process["ProcessId"]} -ErrorAction SilentlyContinue).Count -eq 0').strip() == 'True')
            report['closed'] = 'PASS'
            report.setdefault('closed_instances', []).append(instance)
        elif args.phase == 'uninstall':
            assert not state.exists(), 'Close the application before uninstalling'
            sentinel = runtime / 'output/uninstall-preservation.txt'
            sentinel.write_text('DESKTOP-NATIVE-SHELL-01 preservation evidence', encoding='utf-8')
            before = hashes(runtime)
            uninstaller = Path(report['install']) / 'Uninstall.exe'
            subprocess.run([str(uninstaller), '/S'], check=True, timeout=90)
            wait(lambda: not (Path(report['install']) / (NAME + '.exe')).exists())
            assert hashes(runtime) == before
            report['uninstall'] = report['user_data_preserved'] = 'PASS'
            # Restore only the shell integration captured before this installation.
            for path, values in report['prior_registry'].items():
                if values is not None:
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
                        for name, value, kind in values:
                            winreg.SetValueEx(key, name, 0, kind, value)
            for link, backup in report['prior_shortcuts'].items():
                Path(link).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup, link)
    (work / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(work))


if __name__ == '__main__':
    main()
