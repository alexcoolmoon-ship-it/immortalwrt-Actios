#!/usr/bin/env python3
"""Build offline Windows tools from locked EDL/loader sources.

The installer and EDL remain separate executables. Both run in one-directory
mode so a chunk read does not repeatedly unpack Python and the loader database.
No downloaded installer/driver is run on the user's PC during flashing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile
from urllib.parse import quote
from urllib.request import urlopen

PORT = Path(__file__).resolve().parents[1]
MIGRATION = PORT / 'migration'
sys.path.insert(0, str(MIGRATION))
from runtime_manifest import digest, inventory, source_hashes


def run(args, cwd=None, quiet=False):
    return subprocess.run(list(map(str, args)), cwd=cwd, check=True,
                          stdout=subprocess.DEVNULL if quiet else None)


def checkout(url, commit, folder):
    if folder.exists():
        raise ValueError('Use a new empty work directory: ' + str(folder))
    folder.mkdir(parents=True)
    run(['git', 'init', '-q'], folder)
    run(['git', 'config', 'core.autocrlf', 'false'], folder)
    run(['git', 'config', 'core.eol', 'lf'], folder)
    run(['git', 'remote', 'add', 'origin', url], folder)
    run(['git', 'fetch', '--depth=1', '--filter=blob:none', 'origin', commit], folder)
    actual = subprocess.check_output(['git', 'rev-parse', 'FETCH_HEAD'], cwd=folder, text=True).strip()
    if actual != commit:
        raise ValueError('Fetched a different source revision')
    run(['git', 'checkout', '--detach', commit], folder, quiet=True)


def export_loaders(lock, destination):
    # Windows Git can fail to hydrate blobs during a path-only checkout from an
    # unborn partial clone. Fetch only the locked 4 MB subset, with no full DB.
    if lock['repository'].rstrip('/').removesuffix('.git') != 'https://github.com/bkerler/Loaders':
        raise ValueError('Unexpected loader repository')
    commit = lock['commit']
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('Loader revision must be an exact Git SHA')
    for name, expected in lock['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Invalid loader path')
        url = 'https://raw.githubusercontent.com/bkerler/Loaders/' + commit + '/' + quote(name, safe='/')
        with urlopen(url, timeout=30) as response:
            data = response.read(expected['bytes'] + 1)
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('Loader checksum mismatch: ' + name)
        out = destination / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    print('LOCKED_MSM8916_LOADERS_VERIFIED (' + str(len(lock['files'])) + ')')


def prepare(work, lock):
    edl = work / 'edl-source'
    checkout(lock['edl']['repository'], lock['edl']['commit'], edl)
    for key, name in [('requirements_sha256', 'requirements.txt'), ('uv_lock_sha256', 'uv.lock'),
                      ('entry_sha256', 'edl.py'), ('windows_dll_sha256', 'edlclient/Windows/libusb-1.0.dll'),
                      ('zadig_sha256', 'Drivers/Windows/zadig-2.8.exe')]:
        if digest(edl / name) != lock['edl'][key]:
            raise ValueError('EDL source checksum mismatch: ' + name)
    destination = edl / 'Loaders'
    destination.mkdir(exist_ok=True)
    export_loaders(lock['loaders'], destination)
    requirements = work / 'edl-requirements.lock.txt'
    # pylzma is a stale pyproject dependency, absent from requirements.txt and
    # not imported by this pinned EDL tree. Keep all other locked dependencies.
    run(['uv', 'export', '--frozen', '--no-dev', '--no-emit-project', '--no-emit-package', 'pylzma',
         '--format', 'requirements-txt', '--output-file', requirements], edl, quiet=True)
    return edl, requirements


def freeze(entry, name, edl, work, extra):
    import PyInstaller.__main__
    # collect-all runs in the generated spec before Analysis applies --paths.
    # Make the pinned, uninstalled EDL package visible to those collectors too.
    if str(edl) not in sys.path:
        sys.path.insert(0, str(edl))
    PyInstaller.__main__.run([
        '--noconfirm', '--clean', '--onedir', '--console', '--noupx',
        '--name', name, '--distpath', str(work / 'dist'), '--workpath', str(work / 'build' / name),
        '--specpath', str(work / 'spec'), '--paths', str(MIGRATION), '--paths', str(edl),
        *extra, str(entry)])
    return work / 'dist' / name


def smoke(command, marker, cwd):
    p = subprocess.run(list(map(str, command)), cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=45)
    if p.returncode or marker not in p.stdout + p.stderr:
        raise ValueError('Windows smoke test failed: ' + str(command[0]) + '\n' + (p.stdout + p.stderr)[-3000:])


def build(args):
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    lock = json.loads((MIGRATION / 'windows-runtime.lock.json').read_text())
    edl, requirements = prepare(work, lock)
    if args.prepare_only:
        print('LOCKED_EDL_AND_MSM8916_LOADERS_VERIFIED')
        return
    if sys.platform != 'win32':
        raise ValueError('Compile Windows executables on the windows-2022 job')
    if args.output.exists():
        raise ValueError('Runtime output already exists')
    run([sys.executable, '-m', 'pip', 'install', '--require-hashes', '-r', requirements])
    # The EDL package is provided by --paths/collect-all; not installed as a
    # project, so no network resolution can silently replace its pinned files.
    edl_dist = freeze(MIGRATION / 'windows_edl.py', 'UFI-EDL', edl, work, [
        '--collect-all', 'edlclient', '--collect-all', 'usb', '--collect-all', 'Crypto',
        '--collect-all', 'Cryptodome', '--collect-all', 'lxml', '--collect-all', 'serial',
        '--add-data', str(edl / 'edl.py') + os.pathsep + '.',
        '--add-data', str(edl / 'Loaders') + os.pathsep + 'Loaders',
        '--add-data', str(edl / 'edlclient/Windows') + os.pathsep + 'edlclient/Windows'])
    installer_dist = freeze(MIGRATION / 'windows_entry.py', 'UFI001C-Installer', edl, work, [
        '--collect-all', 'usb', '--hidden-import', 'gzip', '--hidden-import', 'zlib',
        '--add-data', str(MIGRATION / 'ufi-install.py') + os.pathsep + '.'])
    shutil.copytree(installer_dist, args.output)
    shutil.copytree(edl_dist, args.output / 'edl')
    (args.output / 'Drivers').mkdir()
    shutil.copyfile(edl / 'Drivers/Windows/zadig-2.8.exe', args.output / 'Drivers/zadig-2.8.exe')
    shutil.copyfile(edl / 'LICENSE', args.output / 'EDL-LICENSE-GPL-3.0.txt')
    shutil.copyfile(requirements, args.output / 'edl-requirements.lock.txt')
    shutil.copyfile(MIGRATION / 'windows-runtime.lock.json', args.output / 'windows-runtime.lock.json')
    with zipfile.ZipFile(args.output / 'EDL-SOURCE.zip', 'x', zipfile.ZIP_DEFLATED) as z:
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=edl).decode().split('\0')
        for name in names:
            if name and (edl / name).is_file(): z.write(edl / name, 'edl/' + name)
        for path in (edl / 'Loaders').rglob('*'):
            if path.is_file(): z.write(path, 'edl/Loaders/' + path.relative_to(edl / 'Loaders').as_posix())
    # The installer deliberately locates data next to the runtime directory.
    from package_stock import package
    package(args.output.parent)
    smoke([args.output / 'UFI001C-Installer.exe', '--self-test'], 'PORTABLE_INSTALLER_SELF_TEST_OK', work)
    smoke([args.output / 'edl/UFI-EDL.exe', '--self-test'], 'PORTABLE_EDL_SELF_TEST_OK', work)
    smoke([args.output / 'edl/UFI-EDL.exe', '--help'], 'Usage:', work)
    from stock_install import VERSION
    manifest = {'format': 1, 'version': VERSION, 'platform': 'windows-x86_64',
                'source_commit': args.commit, 'source_sha256': source_hashes(MIGRATION),
                'edl_commit': lock['edl']['commit'], 'loaders_commit': lock['loaders']['commit'],
                'smoke_tests': {'installer': 'passed', 'edl': 'passed', 'edl_help': 'passed'},
                'files': inventory(args.output)}
    (args.output / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    from runtime_manifest import verify_runtime
    verify_runtime(args.output, MIGRATION, args.commit)
    print('PORTABLE_WINDOWS_RUNTIME_VERIFIED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    build(parser.parse_args())
