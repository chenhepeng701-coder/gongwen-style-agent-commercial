"""Validate actual PyInstaller input/output inventory before installer compilation."""
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


def verify_package(run, site_packages, manifest):
    stage, application = run / 'stage', run / 'dist/公文写作助手'
    analysis = ast.literal_eval((run / 'work/app/Analysis-00.toc').read_text(encoding='utf-8'))
    collected = ast.literal_eval((run / 'work/app/COLLECT-00.toc').read_text(encoding='utf-8'))[0]
    expected_output = {Path(name).as_posix() if kind == 'EXECUTABLE' else '_internal/' + Path(name).as_posix()
                       for name, _, kind in collected}
    vendor_path = manifest['vendor_manifest']
    vendor_raw = (stage / vendor_path).read_bytes()
    assert hashlib.sha256(vendor_raw).hexdigest() == manifest['vendor_manifest_sha256']
    vendor_root = Path(vendor_path.removeprefix('backend/')).parent
    # The existing runtime owner hashes this complete pinned graph, including
    # its upstream builder/test source. These files are data, never executed.
    vendor_files = {(vendor_root / item['path']).as_posix(): item['sha256'] for item in json.loads(vendor_raw)['files']}
    approved = {str(Path(d.locate_file(file)).resolve()).casefold()
                for d in importlib.metadata.distributions(path=[str(site_packages)])
                if d.metadata['Name'].lower().replace('_', '-') in {
                    name.lower().replace('_', '-') for name in manifest['python_runtime_distributions']}
                for file in d.files or []}
    # PyInstaller 6.22.3 Analysis TOC: scripts, Python modules, binaries, data.
    for index in (13, 14, 15, 18):
        for name, source, kind in analysis[index]:
            path = Path(source).resolve()
            assert not any(part.lower() in {'.env', '.git', 'node_modules', '_pytest'} for part in Path(name).parts), name
            if 'tests' in Path(name).parts:
                assert vendor_files.get(Path(name).as_posix()) == hashlib.sha256(path.read_bytes()).hexdigest(), name
            assert not name.startswith(('pytest', '_pytest', 'anyio.pytest_plugin', 'fastapi.testclient', 'starlette.testclient'))
            if 'site-packages' in path.parts:
                assert str(path).casefold() in approved, f'Unapproved runtime dependency: {name}'
    inputs = json.loads((stage / 'bundle-inputs.json').read_text(encoding='utf-8'))
    for item in inputs:
        source = stage / item['source']
        target = application / '_internal' / item['destination'] / source.name
        assert source.read_bytes() == target.read_bytes(), str(target)
    files = []
    for path in sorted(application.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(application).as_posix()
        assert not any(part.lower() in {'.git', 'node_modules', 'uploads', 'output', 'logs', 'tmp', '__pycache__', 'secrets'} for part in path.relative_to(application).parts), relative
        if 'tests' in path.relative_to(application).parts:
            assert vendor_files.get(relative.removeprefix('_internal/')) == hashlib.sha256(path.read_bytes()).hexdigest(), relative
        assert not path.name.startswith('.env') and not relative.endswith(('.log', '.map')), relative
        assert not relative.startswith('_internal/frontend/src/'), relative
        assert 'writing-reference-' not in relative and 'reference_outbound' not in relative, relative
        raw = path.read_bytes()
        for marker in ('TEST_SECRET_DO_NOT_USE_123', 'SYNTHETIC_LLM_SECRET', 'SYNTHETIC_EMBEDDING_SECRET'):
            assert marker.encode() not in raw and marker.encode('utf-16-le') not in raw, 'Test credential in package: ' + relative
        assert path.suffix != '.dpapi' and path.name not in {'config.yaml', 'app_settings.json'}, relative
        files.append({'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size})
    assert (application / '公文写作助手.exe').is_file()
    assert (application / '_internal/python312.dll').is_file()
    assert {item['path'] for item in files} == expected_output, 'Unexpected or missing package output'
    result = {'status': 'PASS', 'file_count': len(files), 'files': files,
              'secrets_bundled': False, 'user_data_bundled': False, 'private_reference_bundled': False,
              'test_secret_leak_found': False}
    (run / 'PACKAGE-MANIFEST.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
