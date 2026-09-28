"""FIX-01 release contracts; no dependency downloads or machine registry writes.

Set GONGWEN_TEST_NSIS to the already installed makensis.exe for the compiled
branch tests. They execute production NSIS decision code with registry reads
and the Microsoft installer replaced only at their external side-effect seams.
This does not claim clean-machine Runtime installation acceptance.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_release
import verify_package

ROOT, HERE = build_release.ROOT, build_release.HERE
LICENSE = 'licenses/proxy_tools/LICENSE.txt'
LICENSE_SHA = 'a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4'
SOURCE = 'https://raw.githubusercontent.com/jtushman/proxy_tools/ccd35a569b95bec271a59890780c7821756c548a/LICENSE.txt'


@pytest.fixture
def manifest():
    return json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))


def test_python_nsis_minimum_is_one_build_contract():
    import desktop_shell
    minimum = build_release.webview2_min_runtime(ROOT)
    assert minimum == '146.0.3856.49'
    assert tuple(map(int, minimum.split('.'))) == desktop_shell.WEBVIEW2_MIN_RUNTIME
    script = (HERE / 'installer.nsi').read_text(encoding='utf-8')
    assert '!ifndef WEBVIEW2_MIN_RUNTIME\n!error ' in script
    assert '!define WEBVIEW2_MIN_RUNTIME' not in script
    assert re.findall(r'\$\{VersionCompare\} \$1 "([^"]+)" \$0', script) == [
        '${WEBVIEW2_MIN_RUNTIME}', '${WEBVIEW2_MIN_RUNTIME}']
    builder = ast.parse((HERE / 'build_release.py').read_bytes())
    nsis_calls = [n for n in ast.walk(builder) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == 'run'
                  and 'makensis.exe' in ast.unparse(n)]
    assert len(nsis_calls) == 1
    assert "'/DWEBVIEW2_MIN_RUNTIME=' + minimum_runtime" in ast.unparse(nsis_calls[0])
    assert "minimum_runtime = webview2_min_runtime(stage)" in ast.unparse(builder)


@pytest.mark.parametrize('literal', ['(146, 0, 3856)', '(146, 0, 3856, True)', "'146.0.3856.49'", '(146, 0, 3856, -1)'])
def test_build_rejects_invalid_runtime_contract(tmp_path, literal):
    owner = tmp_path / 'backend/desktop_shell.py'
    owner.parent.mkdir()
    owner.write_text('WEBVIEW2_MIN_RUNTIME = ' + literal, encoding='utf-8')
    with pytest.raises(AssertionError):
        build_release.webview2_min_runtime(tmp_path)


@pytest.mark.parametrize('user,machine,installs', [
    ('', '', 1),
    ('145.0.9999.99', '', 1),
    ('', '145.0.9999.99', 1),
    ('146.0.3856.48', '', 1),
    ('', '146.0.3856.48', 1),
    ('146.0.3856.49', '', 0),
    ('', '146.0.3856.49', 0),
    ('154.0.4258.37', '', 0),
    ('', '155.0.4283.18', 0),
    ('145.0.9999.99', '155.0.4283.18', 0),
])
def test_compiled_nsis_runtime_install_decision(tmp_path, user, machine, installs):
    compiler = Path(os.environ['GONGWEN_TEST_NSIS']).resolve()
    assert compiler.is_file(), 'Use the existing NSIS toolchain; do not download one'
    source = (HERE / 'installer.nsi').read_text(encoding='utf-8')
    detect = re.search(r'Function DetectWebView2\n.*?FunctionEnd', source, re.S).group()
    detect, count = re.subn(r'  ReadRegStr \$1 HK(CU|LM) [^\n]+',
                           lambda m: '  StrCpy $1 ' + ('$R8' if m[1] == 'CU' else '$R9'), detect)
    assert count == 2
    section = source.split('  SectionIn RO\n', 1)[1].split('  SetOutPath "$INSTDIR"', 1)[0]
    file_line = '    File /oname=MicrosoftEdgeWebView2RuntimeInstallerX64.exe "${WEBVIEW2_INSTALLER}"'
    exec_line = '    ExecWait \'"$PLUGINSDIR\\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" /silent /install\' $2'
    assert section.count(file_line) == section.count(exec_line) == 1
    section = section.replace(file_line, '    ; No real runtime payload in the branch test')
    section = section.replace(exec_line, '    IntOp $R7 $R7 + 1\n    StrCpy $R9 "154.0.4258.37"\n    StrCpy $2 0')
    # Abort paths must fail the test, not display a dialog on a test machine.
    section, count = re.subn(r'      MessageBox [^\n]+', '      SetErrorLevel 91', section)
    assert count == 1
    result = tmp_path / 'result.txt'
    executable = tmp_path / 'runtime-contract.exe'
    script = tmp_path / 'runtime-contract.nsi'
    script.write_text('\n'.join([
        'Unicode true', '!include "LogicLib.nsh"', '!include "WordFunc.nsh"',
        'Name "Runtime contract test"', f'OutFile "{executable}"',
        'RequestExecutionLevel user', 'SilentInstall silent', detect, 'Section',
        f'  StrCpy $R8 "{user}"', f'  StrCpy $R9 "{machine}"', '  StrCpy $R7 0',
        section, f'  FileOpen $R6 "{result}" w', '  FileWrite $R6 "$R7,$0"',
        '  FileClose $R6', 'SectionEnd', '']), encoding='utf-8')
    compiled = subprocess.run([str(compiler), '/V2', '/INPUTCHARSET', 'UTF8',
                               '/DWEBVIEW2_MIN_RUNTIME=' + build_release.webview2_min_runtime(ROOT), str(script)],
                              capture_output=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    subprocess.run([str(executable), '/S'], check=True, timeout=30)
    assert result.read_text() == f'{installs},1'


def test_upstream_full_license_is_pinned_and_byte_preserved(manifest):
    entry = next(item for item in manifest['desktop_license_files'] if item['path'] == LICENSE)
    assert entry['source_url'] == SOURCE
    assert entry['source_commit'] == 'ccd35a569b95bec271a59890780c7821756c548a'
    assert entry['sha256'] == LICENSE_SHA
    assert hashlib.sha256((HERE / LICENSE).read_bytes()).hexdigest() == LICENSE_SHA
    verify_package.verify_desktop_licenses(HERE, manifest)


@pytest.mark.parametrize('location', ['stage', 'dist/公文写作助手/_internal'])
@pytest.mark.parametrize('change', ['missing', 'tampered', 'notice_only'])
def test_verify_package_rejects_bad_license_even_before_other_inventory(tmp_path, manifest, location, change):
    for destination in ('stage', 'dist/公文写作助手/_internal'):
        for entry in manifest['desktop_license_files']:
            target = tmp_path / destination / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            if destination == location and entry['path'] == LICENSE:
                if change == 'missing':
                    continue
                raw = b'changed license' if change == 'tampered' else (HERE / 'licenses/proxy_tools-NOTICE.txt').read_bytes()
            else:
                raw = (HERE / entry['path']).read_bytes()
            target.write_bytes(raw)
    with pytest.raises(AssertionError, match='(Missing desktop license|Desktop license SHA-256 mismatch).*proxy_tools/LICENSE.txt'):
        verify_package.verify_package(tmp_path, tmp_path / 'site-packages', manifest)


def test_notice_and_removed_manifest_entry_cannot_replace_full_license(manifest):
    invalid = copy.deepcopy(manifest)
    invalid['desktop_license_files'] = [item for item in invalid['desktop_license_files'] if item['path'] != LICENSE]
    with pytest.raises(AssertionError, match='NOTICE is not a license substitute'):
        verify_package.verify_desktop_licenses(HERE, invalid)


def test_build_validates_pins_before_collecting_inputs():
    tree = ast.parse((HERE / 'build_release.py').read_bytes())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    text = ast.unparse(main)
    assert text.index('verify_desktop_licenses(HERE, manifest)') < text.index('licenses(stage,')
    assert "item.get('source_url', 'desktop-dependency-license-evidence')" in text
