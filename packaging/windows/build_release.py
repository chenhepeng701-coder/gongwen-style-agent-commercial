"""Build from explicit Git paths into a fresh stage, never a worktree copy."""
import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def approved_sources(manifest, tracked):
    selected = set(manifest['production_tracked_files'])
    selected.update(name for name in tracked if any(fnmatch.fnmatchcase(name, pattern)
                    for pattern in manifest['production_tracked_patterns']))
    selected.update(manifest['frontend_build_input_files'])
    selected.update(name for name in tracked if any(fnmatch.fnmatchcase(name, pattern)
                    for pattern in manifest['frontend_build_input_patterns']))
    vendor = manifest['vendor_manifest']
    assert vendor in tracked, 'Vendor manifest must be tracked'
    raw = (ROOT / vendor).read_bytes()
    assert sha(raw) == manifest['vendor_manifest_sha256'], 'Vendor manifest changed'
    selected.add(vendor)
    for item in json.loads(raw)['files']:
        relative = PurePosixPath(item['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        name = str(PurePosixPath(vendor).parent / relative)
        assert name in tracked, name
        assert sha((ROOT / name).read_bytes()) == item['sha256'], name
        selected.add(name)
    assert selected <= tracked, 'Only tracked inputs are allowed outside the explicit new-file list'
    selected.update(manifest['approved_new_production_files'])
    return sorted(selected)


def copy_input(source, destination, records, origin):
    assert source.is_file() and not source.is_symlink(), str(source)
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    records.append({'path': destination.as_posix(), 'sha256': sha(raw), 'size': len(raw), 'origin': origin})


def licenses(stage, site_packages, manifest, build_tools):
    result = []
    distributions = {d.metadata['Name'].lower().replace('_', '-'): d
                     for d in importlib.metadata.distributions(path=[str(site_packages)])}
    for name, version in manifest['python_runtime_distributions'].items():
        distribution = distributions[name.lower().replace('_', '-')]
        assert distribution.version == version, f'Dependency version mismatch: {name}'
        license_files = [p for p in distribution.files or [] if
                         any(part.lower().startswith(('license', 'copying', 'notice')) for part in p.parts)]
        assert license_files, f'Missing license: {name}'
        for index, path in enumerate(license_files):
            target = stage / 'licenses' / name / (str(index) + '-' + path.name)
            copy_input(Path(distribution.locate_file(path)), target, result, f'{name}=={version}')
    copy_input(Path(sys.base_prefix) / 'LICENSE.txt', stage / 'licenses/Python-LICENSE.txt', result, sys.version)
    for distribution in importlib.metadata.distributions(path=[str(build_tools)]):
        if distribution.metadata['Name'].lower() not in {'pyinstaller', 'pyinstaller-hooks-contrib'}:
            continue
        for path in distribution.files or []:
            if any(part.lower().startswith(('license', 'copying')) for part in path.parts):
                copy_input(Path(distribution.locate_file(path)), stage / 'licenses' / distribution.metadata['Name'] / path.name,
                           result, f'{distribution.metadata["Name"]}=={distribution.version}')
    lock = json.loads((stage / 'frontend/package-lock.json').read_text(encoding='utf-8'))
    for name, metadata in lock['packages'].items():
        if not name or metadata.get('dev'):
            continue
        source = ROOT / 'frontend' / name
        actual = json.loads((source / 'package.json').read_text(encoding='utf-8'))
        assert actual['version'] == metadata['version'], name
        candidates = [p for p in source.iterdir() if p.is_file() and p.name.lower().startswith(('license', 'copying', 'notice'))]
        if not candidates:
            # These pinned npm releases embed their complete MIT notice in README.
            readme = source / 'README.md'
            text = readme.read_text(encoding='utf-8') if readme.is_file() else ''
            start = text.find('Copyright (c)')
            end_marker = 'SOFTWARE.'
            end = text.find(end_marker, start)
            assert start >= 0 and end > start and 'Permission is hereby granted' in text[start:end], f'Missing frontend dependency license: {name}'
            target = stage / 'licenses/javascript' / name.removeprefix('node_modules/') / 'LICENSE.txt'
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = text[start:end + len(end_marker)].encode('utf-8')
            target.write_bytes(raw)
            result.append({'path': str(target), 'sha256': sha(raw), 'size': len(raw), 'origin': f'{name}=={metadata["version"]}/README license'})
        for path in candidates:
            copy_input(path, stage / 'licenses/javascript' / name.removeprefix('node_modules/') / path.name,
                       result, f'{name}=={metadata["version"]}')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-packages', type=Path, required=True)
    parser.add_argument('--build-tools', type=Path, required=True)
    parser.add_argument('--nsis', type=Path)
    args = parser.parse_args()
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    assert sys.version_info[:3] == (3, 12, 14), 'Build requires the recorded Python 3.12.14 runtime'
    tool_versions = {d.metadata['Name'].lower(): d.version for d in importlib.metadata.distributions(path=[str(args.build_tools)])}
    assert tool_versions.get('pyinstaller') == '6.22.3', 'Unexpected PyInstaller version'
    if args.nsis:
        assert subprocess.check_output([str((args.nsis / 'makensis.exe').resolve()), '/VERSION']).decode().strip() == 'v3.12'
    tracked = set(git('ls-files', '-z').decode('utf-8').split('\0')) - {''}
    sources = approved_sources(manifest, tracked)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    # NSIS still opens source files through Windows MAX_PATH-limited APIs.
    run = ROOT / 'tmp' / ('rc01-' + uuid.uuid4().hex[:8])
    stage = run / 'stage'
    stage.mkdir(parents=True)
    records = []
    for name in sources:
        copy_input(ROOT / name, stage / name, records, 'git-tracked' if name in tracked else 'RELEASE-RC-01-approved-new')
    for name in ('manifest.json', 'app.spec', 'version_info.txt', 'build_release.py', 'verify_package.py', 'installer.nsi'):
        copy_input(HERE / name, stage / 'packaging/windows' / name, records, 'RELEASE-RC-01-packaging')
    records.extend(licenses(stage, args.site_packages, manifest, args.build_tools))
    copy_input(HERE / 'licenses/Python-3.12-third-party.txt', stage / 'licenses/Python-3.12-third-party.txt', records,
               'https://docs.python.org/3.12/_sources/license.rst.txt')
    if args.nsis:
        copy_input(args.nsis / 'COPYING', stage / 'licenses/NSIS-COPYING.txt', records, 'NSIS 3.12')
    # A build-only junction resolves approved npm dependencies. It is never bundled.
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path ' + quote(stage / 'frontend/node_modules') +
        ' -Target ' + quote(ROOT / 'frontend/node_modules') + ' | Out-Null'], check=True)
    environment = {key: value for key, value in os.environ.items() if not key.startswith('VITE_')}
    with (run / 'frontend-build.log').open('w', encoding='utf-8') as log:
        subprocess.run(['npm.cmd', 'run', 'build'], cwd=stage / 'frontend', env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    bundle = []
    for name in sources:
        if name.startswith('frontend/'):
            continue
        destination = str(PurePosixPath(name.removeprefix('backend/')).parent)
        bundle.append({'source': name, 'destination': destination})
    # Enumerate only the freshly generated dist and collected licenses, not the workspace.
    for folder in ('frontend/dist-commercial', 'licenses'):
        for path in sorted((stage / folder).rglob('*')):
            if path.is_file():
                name = path.relative_to(stage).as_posix()
                bundle.append({'source': name, 'destination': str(PurePosixPath(name).parent)})
    dirty = bool(git('diff', '--name-only', 'HEAD', '--', *[name for name in sources if name in tracked]).strip())
    provenance = {'version': manifest['version'], 'commit': git('rev-parse', 'HEAD').decode().strip(),
                  'source_state': 'uncommitted_candidate' if dirty or set(sources) - tracked else 'clean_tracked_source',
                  'python_version': '3.12.14', 'pyinstaller_version': tool_versions['pyinstaller'],
                  'installer_tool': 'NSIS 3.12' if args.nsis else None,
                  'build_timestamp': stamp, 'mode': 'onedir', 'source_files': [
                      {**item, 'path': Path(item['path']).relative_to(stage).as_posix()} for item in records]}
    (stage / 'BUILD-MANIFEST.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    bundle.append({'source': 'BUILD-MANIFEST.json', 'destination': '.'})
    (stage / 'bundle-inputs.json').write_text(json.dumps(bundle, ensure_ascii=False), encoding='utf-8')
    environment.update(GONGWEN_BUILD_STAGE=str(stage), GONGWEN_BUILD_SITE_PACKAGES=str(args.site_packages.resolve()),
        PYTHONPATH=os.pathsep.join((str(args.build_tools.resolve()), str(args.site_packages.resolve()))))
    with (run / 'pyinstaller.log').open('w', encoding='utf-8') as log:
        subprocess.run([sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm',
            '--distpath', str(run / 'dist'), '--workpath', str(run / 'work'), str(stage / 'packaging/windows/app.spec')],
            cwd=stage, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    from verify_package import verify_package
    verify_package(run, args.site_packages, manifest)
    (ROOT / 'tmp/release-rc-01/latest-package.txt').write_text(str(run), encoding='utf-8')
    if args.nsis:
        with (run / 'installer-build.log').open('w', encoding='utf-8') as log:
            subprocess.run([str((args.nsis / 'makensis.exe').resolve()), '/INPUTCHARSET', 'UTF8',
                '/DAPP_DIR=' + str(run / 'dist/公文写作助手'),
                '/DOUTPUT_FILE=' + str(run / '公文写作助手-Setup.exe'), str(stage / 'packaging/windows/installer.nsi')],
                stdout=log, stderr=subprocess.STDOUT, check=True)
    print(json.dumps({'build_directory': str(run), 'app': str(run / 'dist/公文写作助手/公文写作助手.exe')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
