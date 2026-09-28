"""Native-shell candidate checks. Run after installed acceptance, never bundled."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import unittest

import build_release
import verify_package

ROOT = build_release.ROOT
HERE = build_release.HERE


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class NativePackagingTests(unittest.TestCase):
    def test_strict_source_selection_keeps_business_and_private_data_out_of_changes(self):
        tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')) - {''}
        selected = set(build_release.approved_sources(MANIFEST, tracked))
        self.assertLessEqual(selected, tracked | set(MANIFEST['approved_new_production_files']))
        self.assertIn('backend/desktop_shell.py', selected)
        self.assertFalse(any(p.startswith(('backend/tests/', 'tmp/', 'backend/data/', 'backend/uploads/')) for p in selected))
        invalid = json.loads(json.dumps(MANIFEST))
        invalid['production_tracked_files'].append('backend/not-authorized.py')
        with self.assertRaisesRegex(AssertionError, 'Only tracked inputs'):
            build_release.approved_sources(invalid, tracked)

    def test_actual_dependency_and_file_inventory(self):
        result = verify_package.verify_package(ARGS.build, ARGS.site_packages, MANIFEST)
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['secrets_bundled'] or result['user_data_bundled'] or result['private_reference_bundled'])

    def test_built_candidate_contains_current_production_bytes(self):
        paths = ['backend/desktop_launcher.py', 'backend/desktop_shell.py'] + [
            'packaging/windows/' + n for n in ('manifest.json', 'app.spec', 'installer.nsi', 'build_release.py', 'verify_package.py')]
        for name in paths:
            self.assertEqual((ROOT / name).read_bytes(), (ARGS.build / 'stage' / name).read_bytes(), name)
        provenance = read(ARGS.build / 'stage/BUILD-MANIFEST.json')
        current_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        self.assertEqual(provenance['commit'], current_head)
        self.assertEqual(provenance['python_version'], '3.12.14')
        self.assertEqual(provenance['pyinstaller_version'], '6.22.3')
        self.assertEqual(provenance['source_state'], 'clean_tracked_source')

    def test_official_offline_runtime_signature_hash_and_license(self):
        verify_package.verify_webview2_installer(ARGS.webview2_installer, MANIFEST)
        record = read(ARGS.build / 'INSTALLER-MANIFEST.json')
        self.assertEqual(record['webview2_redistributable'], MANIFEST['webview2_redistributable'])
        self.assertTrue(record['offline_install'])
        self.assertFalse(record['runtime_download_on_startup'])
        self.assertEqual(hashlib.sha256((ARGS.build / record['filename']).read_bytes()).hexdigest(), record['sha256'])
        for item in MANIFEST['desktop_license_files']:
            self.assertEqual(hashlib.sha256((HERE / item['path']).read_bytes()).hexdigest(), item['sha256'])
            self.assertEqual((HERE / item['path']).read_bytes(), (ARGS.build / 'dist/公文写作助手/_internal' / item['path']).read_bytes())

    def test_consoleless_exe_and_installer_use_windows_gui_subsystem(self):
        for path in [ARGS.build / '公文写作助手-Setup.exe', ARGS.build / 'dist/公文写作助手/公文写作助手.exe']:
            raw = path.read_bytes()
            pe = struct.unpack_from('<I', raw, 0x3c)[0]
            self.assertEqual(raw[pe:pe + 4], b'PE\0\0')
            self.assertEqual(struct.unpack_from('<H', raw, pe + 24 + 68)[0], 2)

    def test_pinned_full_proxy_license_in_build_and_package(self):
        relative = 'licenses/proxy_tools/LICENSE.txt'
        entry = next(item for item in MANIFEST['desktop_license_files'] if item['path'] == relative)
        inputs = read(ARGS.build / 'stage/BUILD-MANIFEST.json')['source_files']
        evidence = next(item for item in inputs if item['path'] == relative)
        self.assertEqual(evidence['sha256'], entry['sha256'])
        self.assertEqual(evidence['origin'], entry['source_url'])
        files = read(ARGS.build / 'PACKAGE-MANIFEST.json')['files']
        self.assertEqual(next(item for item in files if item['path'] == '_internal/' + relative)['sha256'], entry['sha256'])
        for folder in ('stage', 'dist/公文写作助手/_internal'):
            raw = (ARGS.build / folder / relative).read_bytes()
            self.assertEqual(raw, (HERE / relative).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])

    def test_installer_records_the_staged_python_runtime_contract(self):
        record = read(ARGS.build / 'INSTALLER-MANIFEST.json')
        minimum = build_release.webview2_min_runtime(ARGS.build / 'stage')
        self.assertEqual(minimum, '146.0.3856.49')
        self.assertEqual(record['webview2_min_runtime'], minimum)
        # NSIS writes redirected output in the Windows code page. The version
        # argument is ASCII; inspect its exact bytes without decoding UI text.
        self.assertIn(('WEBVIEW2_MIN_RUNTIME=' + minimum).encode('ascii'),
                      (ARGS.build / 'installer-build.log').read_bytes())

    def test_nsis_per_user_offline_runtime_and_uninstall_contract(self):
        text = (ARGS.build / 'stage/packaging/windows/installer.nsi').read_text(encoding='utf-8')
        self.assertIn('RequestExecutionLevel user', text)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\GongwenWritingAssistant"', text)
        self.assertIn('Call DetectWebView2\n  ${If} $0 == 0', text)
        self.assertIn('MicrosoftEdgeWebView2RuntimeInstallerX64.exe" /silent /install', text)
        self.assertNotIn('inetc::', text)
        self.assertNotIn('NSISdl::', text)
        self.assertNotIn('ExecShell', text)
        self.assertNotIn('RMDir /r "$LOCALAPPDATA', text)

    def test_installed_native_acceptance_belongs_to_this_candidate(self):
        report = read(ARGS.acceptance / 'acceptance.json')
        self.assertEqual(Path(report['build']), ARGS.build.resolve())
        for key in ('installer', 'desktop_shortcut_created', 'closed', 'uninstall', 'user_data_preserved'):
            self.assertEqual(report[key], 'PASS', key)
        self.assertTrue(report['non_admin'] and report['install_unchanged'])
        self.assertEqual(report['real_model_calls'], 0)
        self.assertEqual(report['real_embedding_calls'], 0)
        launches = report['launches']
        self.assertEqual(launches[0], launches[1])
        self.assertNotEqual(launches[0]['identity'], launches[-1]['identity'])
        self.assertGreaterEqual(len(report['closed_instances']), 2)
        self.assertTrue(any(s['minimized'] for s in report['native_snapshots']))
        self.assertTrue(any(s['maximized'] for s in report['native_snapshots']))
        self.assertTrue(any(s['foreground'] and not s['minimized'] for s in report['native_snapshots']))
        widths = {s['rect'][2] - s['rect'][0] for s in report['native_snapshots'] if not s['minimized'] and not s['maximized']}
        self.assertGreaterEqual(len(widths), 2)
        for codes in report['origin_boundary_codes'].values():
            self.assertEqual(codes, [403, 404, 403])
        for snapshot in report['owned_process_snapshots']:
            self.assertTrue(any(p['Name'] == 'msedgewebview2.exe' for p in snapshot))
            self.assertFalse(any(p['Name'].lower() in {'chrome.exe', 'msedge.exe', 'cmd.exe', 'conhost.exe'} for p in snapshot))
        self.assertTrue((ARGS.acceptance / 'native-main.jpg').is_file())
        self.assertTrue((ARGS.acceptance / 'native-origin-save.jpg').is_file())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('build', 'site-packages', 'webview2-installer', 'acceptance'):
        parser.add_argument('--' + name, required=True, type=Path)
    ARGS = parser.parse_args()
    MANIFEST = read(HERE / 'manifest.json')
    unittest.main(argv=['test_desktop_packaging.py'], verbosity=2)
