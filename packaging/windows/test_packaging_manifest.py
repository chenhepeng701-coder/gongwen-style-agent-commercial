"""Packaging-only smoke checks, separate from application/backend tests."""
import json
from pathlib import Path
import subprocess
import unittest

import build_release
import verify_package

ROOT = build_release.ROOT


class PackagingManifestTests(unittest.TestCase):
    def test_only_tracked_or_explicit_new_sources_are_selected(self):
        manifest = json.loads((build_release.HERE / 'manifest.json').read_text(encoding='utf-8'))
        tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')) - {''}
        selected = set(build_release.approved_sources(manifest, tracked))
        self.assertLessEqual(selected, tracked | set(manifest['approved_new_production_files']))
        self.assertIn('backend/app/main.py', selected)
        self.assertIn('backend/app/config.py', selected)
        self.assertIn('backend/app/utils/secret_store.py', selected)
        self.assertNotIn('backend/.env', selected)
        self.assertFalse(any(name.startswith(('backend/tests/', 'tmp/', 'backend/data/', 'backend/uploads/', 'backend/output/')) for name in selected))
        protected = json.loads((ROOT / 'tmp/release-rc-01/protected-user-files.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(selected.intersection(protected))

    def test_manifest_rejects_untracked_required_file(self):
        manifest = json.loads((build_release.HERE / 'manifest.json').read_text(encoding='utf-8'))
        tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')) - {''}
        manifest['production_tracked_files'].append('backend/not-authorized.py')
        with self.assertRaisesRegex(AssertionError, 'Only tracked inputs'):
            build_release.approved_sources(manifest, tracked)

    def test_built_package_exact_inventory_and_runtime_dependencies(self):
        run = Path((ROOT / 'tmp/release-rc-01/latest-package.txt').read_text(encoding='utf-8'))
        manifest = json.loads((build_release.HERE / 'manifest.json').read_text(encoding='utf-8'))
        result = verify_package.verify_package(run, ROOT / 'backend/.venv/Lib/site-packages', manifest)
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['private_reference_bundled'])
        self.assertFalse(result['user_data_bundled'])
        self.assertFalse(result['secrets_bundled'])

    def test_installation_acceptance_belongs_to_current_candidate(self):
        run = Path((ROOT / 'tmp/release-rc-01/latest-package.txt').read_text(encoding='utf-8'))
        report = json.loads((run / 'dev-acceptance.json').read_text(encoding='utf-8'))
        self.assertEqual(report['result'], 'PASS')
        self.assertEqual(report['loopback_only'], 'PASS')
        self.assertEqual(report['real_model_calls'], 0)
        self.assertEqual(report['clean_machine_acceptance'], 'NOT_RUN')
        self.assertEqual(report['secure_secret_storage'], 'DPAPI_CURRENT_USER_PASS')
        self.assertEqual(report['runtime_plaintext_secret_scan'], 'PASS')
        self.assertEqual(report['packaged_legacy_migration'], 'PASS')
        self.assertFalse(report['packaged_env_secret_override_enabled'])
        self.assertFalse(report['windows_process_elevated'])
        self.assertEqual(report['stateful_origin_required'], 'PASS')
        self.assertRegex(report['dev_smoke_stateful_origin'], r'^http://127\.0\.0\.1:\d+$')

    def test_startup_migration_acceptance_belongs_to_current_candidate(self):
        run = Path((ROOT / 'tmp/release-rc-01/latest-package.txt').read_text(encoding='utf-8'))
        report = json.loads((run / 'startup-migration-acceptance.json').read_text(encoding='utf-8'))
        self.assertEqual(Path(report['candidate']), run)
        self.assertEqual(report['result'], 'PASS')
        self.assertFalse(report['windows_process_elevated'])
        for name in ('external_network_calls', 'real_model_calls', 'embedding_calls'):
            self.assertEqual(report[name], 0)
        for name in ('migration_before_http_ready_without_settings_get', 'second_startup_idempotent',
                     'no-legacy', 'placeholder', 'packaged_environment_secrets_ignored',
                     'fail_closed_preserves_plaintext_store-write', 'fail_closed_preserves_plaintext_config-replace'):
            self.assertEqual(report['cases'][name], 'PASS')


if __name__ == '__main__':
    unittest.main(verbosity=2)
