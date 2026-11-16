"""Atelier partitions launcher, on Linux and Windows.

Starts the local application if it is not running yet, then opens it in the
browser. Everything stays on this computer: the server only listens on
127.0.0.1.

    lanceur.py                  start if needed and open the browser
    lanceur.py --sans-navigateur
    lanceur.py --arreter        stop the application (used before an update)
"""
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chemins  # noqa: E402 -- needs the program folder on the path

PORT = int(os.environ.get('ATELIER_PORT', '8765'))
URL = f'http://127.0.0.1:{PORT}'
PID_FILE = chemins.HOME / 'atelier.pid'
LOG_FILE = chemins.HOME / 'atelier.log'
TITLE = 'Atelier partitions'


def running():
    """True when our application answers on the port."""
    try:
        with urlopen(URL + '/api/health', timeout=2) as response:
            return 'engine' in json.load(response)
    except (OSError, ValueError):
        return False


def start():
    chemins.HOME.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PORT=str(PORT), PYTHONUTF8='1')
    options = {}
    if chemins.WINDOWS:
        # No console window, and the server outlives the launcher.
        options['creationflags'] = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED, NO_WINDOW, NEW_GROUP
    else:
        options['start_new_session'] = True
    with LOG_FILE.open('a', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', str(HERE / 'app.py')], cwd=HERE, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
    PID_FILE.write_text(str(process.pid), encoding='utf-8')
    for _ in range(60):
        if running():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def stop():
    try:
        pid = int(PID_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    try:
        if chemins.WINDOWS:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    PID_FILE.unlink(missing_ok=True)
    for _ in range(20):
        if not running():
            return True
        time.sleep(0.25)
    return not running()


def tell(message):
    """Show a message even when there is no terminal (desktop icon)."""
    print(message, file=sys.stderr)
    if chemins.WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    elif not sys.stderr.isatty():
        for command in (['zenity', '--error', '--title', TITLE, '--text', message],
                        ['notify-send', TITLE, message]):
            try:
                subprocess.run(command, check=False, timeout=60)
                return
            except (OSError, subprocess.SubprocessError):
                continue


def main(arguments):
    if '--arreter' in arguments:
        print('Application arrêtée.' if stop() else 'L’application ne tournait pas.')
        return 0
    if not running() and not start():
        tell(f'L’application n’a pas pu démarrer. Le port {PORT} est peut-être déjà utilisé par un autre programme.\n'
             f'Détails dans : {LOG_FILE}')
        return 1
    if '--sans-navigateur' not in arguments:
        webbrowser.open(URL)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
