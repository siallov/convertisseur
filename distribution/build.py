#!/usr/bin/env python3
"""Build the archive to install « Atelier partitions » on another PC.

    python3 distribution/build.py   ->  dist/atelier-partitions-<version>-linux.tar.gz
                                        dist/atelier-partitions-<version>-windows.zip

The archive holds the installer and a `programme/` folder with the local
application only (no online-service files, no data, no secrets).
"""
import io
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
VERSION = (ROOT / 'distribution' / 'VERSION').read_text(encoding='utf-8').strip()

# Local application: conversion, review, Paheko deposit and lyrics.
PROGRAM = ['app.py', 'chemins.py', 'lanceur.py', 'installation_config.py', 'paheko_upload.py', 'pupitres.py',
           'paroles.py', 'paroles_core.py', 'paroles_scan.py', 'liseuse.py', 'setup_paheko.py', 'requirements-paroles.txt']
EXCLUDED = ('__pycache__', '.pyc')


def program_files():
    files = [ROOT / name for name in PROGRAM]
    files += sorted(p for p in (ROOT / 'static').rglob('*') if p.is_file() and not any(x in str(p) for x in EXCLUDED))
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        sys.exit('Fichiers manquants : ' + ', '.join(missing))
    return files


def add(archive, source, name, executable=False):
    info = archive.gettarinfo(str(source), name)
    info.mode = 0o755 if executable else 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    with source.open('rb') as stream:
        archive.addfile(info, stream)


def add_text(archive, name, text):
    data = text.encode('utf-8')
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o644
    archive.addfile(info, io.BytesIO(data))


def build_linux():
    DIST.mkdir(exist_ok=True)
    top = 'atelier-partitions'
    target = DIST / f'atelier-partitions-{VERSION}-linux.tar.gz'
    with tarfile.open(target, 'w:gz') as archive:
        add(archive, ROOT / 'distribution' / 'linux' / 'installer.sh', f'{top}/installer.sh', executable=True)
        add(archive, ROOT / 'distribution' / 'linux' / 'LISEZMOI.txt', f'{top}/LISEZMOI.txt')
        for source in program_files():
            add(archive, source, f'{top}/programme/{source.relative_to(ROOT).as_posix()}')
        add(archive, ROOT / 'distribution' / 'linux' / 'desinstaller.sh', f'{top}/programme/desinstaller.sh', executable=True)
        add_text(archive, f'{top}/programme/VERSION', VERSION + '\n')
    return target


def windows_text(path):
    """Windows line endings; PowerShell 5.1 needs a UTF-8 BOM to read accents correctly."""
    text = path.read_text(encoding='utf-8').replace('\r\n', '\n').replace('\n', '\r\n')
    if path.suffix == '.cmd' and not text.isascii():
        sys.exit(f'{path.name} doit rester en ASCII (cmd.exe ne lit pas l’UTF-8).')
    return ('﻿' if path.suffix == '.ps1' else '') + text


def build_windows():
    DIST.mkdir(exist_ok=True)
    top = 'atelier-partitions'
    windows = ROOT / 'distribution' / 'windows'
    target = DIST / f'atelier-partitions-{VERSION}-windows.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('installer.cmd', 'installer.ps1', 'installer-fenetre.ps1', 'LISEZMOI.txt'):
            archive.writestr(f'{top}/{name}', windows_text(windows / name).encode('utf-8'))
        for source in program_files():
            archive.write(source, f'{top}/programme/{source.relative_to(ROOT).as_posix()}')
        for name in ('desinstaller.cmd', 'desinstaller.ps1', 'Atelier partitions.cmd'):
            archive.writestr(f'{top}/programme/{name}', windows_text(windows / name).encode('utf-8'))
        archive.writestr(f'{top}/programme/VERSION', VERSION + '\r\n')
    return target


if __name__ == '__main__':
    for path in (build_linux(), build_windows()):
        print(f'{path.relative_to(ROOT)}  ({path.stat().st_size // 1024} Ko)')
