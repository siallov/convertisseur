"""Where the application keeps its files, on Linux and on Windows.

Existing installations (a `private/` or `data/` folder next to the program) keep
their files there. A new installation writes in the user's profile, because the
program folder may be read-only (C:\\Program Files, /opt…):

- Linux:   ~/.local/share/atelier-partitions (or $XDG_DATA_HOME/atelier-partitions)
- Windows: %LOCALAPPDATA%\\AtelierPartitions

PARTITIONS_HOME chooses another folder.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS = os.name == 'nt'


def user_folder():
    if WINDOWS:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'AtelierPartitions'
    base = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    return Path(base) / 'atelier-partitions'


def home():
    override = os.environ.get('PARTITIONS_HOME')
    if override:
        return Path(override).expanduser()
    if (ROOT / 'private').is_dir() or (ROOT / 'data').is_dir():
        return ROOT
    return user_folder()


HOME = home()
DATA = HOME / 'data'
PRIVATE = HOME / 'private'


def program_files():
    """Windows program folders, in search order."""
    names = ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)')
    folders = [Path(os.environ[name]) for name in names if os.environ.get(name)]
    if os.environ.get('LOCALAPPDATA'):
        folders.append(Path(os.environ['LOCALAPPDATA']) / 'Programs')
    return list(dict.fromkeys(folders))
