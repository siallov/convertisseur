#!/usr/bin/env python3
"""Local PDF → MusicXML application. Python standard library only."""
import concurrent.futures
import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import chemins
import installation_config
import paheko_upload
import paroles
import pupitres

try:
    import liseuse
except ImportError:  # PyMuPDF ou numpy absents : l’onglet le signale
    liseuse = None

ROOT = chemins.ROOT
DATA = chemins.DATA
MAX_BYTES = 80 * 1024 * 1024
JOBS = {}
LOCK = threading.Lock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
LOGGER = logging.getLogger(__name__)


def split_command(value):
    """Split AUDIVERIS_CMD; on Windows, backslashes are path separators, not escapes."""
    if not chemins.WINDOWS:
        return shlex.split(value)
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part
            for part in shlex.split(value, posix=False)]


def engine_candidates():
    """Usual install locations of Audiveris, when it is not on the PATH."""
    if chemins.WINDOWS:
        return [folder / 'Audiveris' / 'Audiveris.exe' for folder in chemins.program_files()]
    return [Path('/opt/audiveris/bin/Audiveris'), Path('/opt/audiveris/bin/audiveris'),
            Path('/Applications/Audiveris.app/Contents/MacOS/Audiveris')]


def engine_command():
    value = os.environ.get('AUDIVERIS_CMD')
    if value:
        command = split_command(value)
        if command and (shutil.which(command[0]) or Path(command[0]).is_file()):
            return command
        return None
    executable = shutil.which('audiveris') or shutil.which('Audiveris')
    if not executable:
        for candidate in engine_candidates():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate)
                break
    return [executable] if executable else None


def extract_musicxml(source, destination):
    with zipfile.ZipFile(source) as archive:
        container = ET.fromstring(archive.read('META-INF/container.xml'))
        node = container.find('.//{*}rootfile')
        if node is None:
            raise ValueError('Archive MXL sans partition.')
        info = archive.getinfo(node.attrib['full-path'])
        if info.file_size > MAX_BYTES:
            raise ValueError('MusicXML trop volumineux.')
        content = archive.read(info)
        score = ET.fromstring(content)
        if score.tag.split('}')[-1] not in ('score-partwise', 'score-timewise'):
            raise ValueError('MusicXML invalide.')
        destination.write_bytes(content)


SOURCE_SUFFIX = r'\.(pdf|musicxml|mxl|xml)$'
PARTS = ('Ensemble', 'Soprano', 'Alto', 'Ténor', 'Baryton', 'Basse', 'Autre')


def score_metadata(headers):
    fields = {key: re.sub(r'[\x00-\x1f]', ' ', unquote(headers.get('X-Score-' + key.title(), '')))[:160].strip() for key in ('title', 'composer', 'part')}
    if fields['part'] and fields['part'] not in PARTS:
        raise ValueError('Choisissez un pupitre proposé dans la liste.')
    song_id = headers.get('X-Score-Song', '')
    if song_id and not re.fullmatch(r'[1-9][0-9]{0,9}', song_id):
        raise ValueError('Référence du chant invalide. Revenez au répertoire et sélectionnez le chant.')
    fields['song_id'] = song_id
    return fields


def job_details(folder):
    result = {}
    for name in ('score.json', 'result.json'):
        try:
            result.update(json.loads((folder / name).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    return result


def score_stem(job):
    title = job.get('title') or re.sub(SOURCE_SUFFIX, '', job.get('name', ''), flags=re.IGNORECASE)
    if job.get('title'):
        title = ' — '.join(x for x in (title, job.get('composer'), job.get('part') if job.get('part') != 'Ensemble' else '') if x)
    return re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', title).strip(' .')[:170]


def reader_url(stem):
    config = paheko_upload.load_config()
    if not config:
        return None
    parsed = urlparse(config['url'])
    module_path = config.get('module_path', '/m/chorale/')
    return f'{parsed.scheme}://{parsed.netloc}{module_path}?' + urlencode({'view': 'reader', 'score': stem})


def diagnose(log):
    text = log.lower()
    if 'too large image' in text:
        return 'page_too_large', 'Page trop grande pour le moteur.', 'Réexportez le PDF à 300 dpi ou réduisez ses dimensions, puis réimportez-le.'
    if any(x in text for x in ('outofmemory', 'java heap space', 'cannot allocate memory')):
        return 'memory', 'Mémoire insuffisante pour cette partition.', 'Découpez le PDF en quelques pages et relancez la conversion.'
    if any(x in text for x in ('password', 'encrypted', 'invalidpassword')):
        return 'protected_pdf', 'Le PDF est protégé.', 'Exportez une copie sans mot de passe, puis importez cette copie.'
    if any(x in text for x in ('invalid pdf', 'error loading', 'cannot read', 'could not load', 'header signature')):
        return 'unreadable_pdf', 'Le PDF ne peut pas être lu.', 'Ouvrez le PDF et réexportez-le, puis réimportez la nouvelle copie.'
    if any(x in text for x in ('too low interline', 'no staves', 'no staff', 'sheet ignored')):
        return 'recognition', 'Les portées n’ont pas pu être reconnues.', 'Utilisez un scan droit et net, idéalement à 300 dpi, puis réimportez-le.'
    return 'recognition', 'La reconnaissance musicale a échoué.', 'Consultez le journal et essayez une page nette de la partition.'


def update(job_id, **values):
    with LOCK:
        JOBS[job_id].update(values)
        if values.get("status") in ("done", "error", "review", "publishing"):
            try:
                (DATA / job_id / "result.json").write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
            except OSError:
                pass


def ocr_language_options():
    """Read lyrics as French by default; AUDIVERIS_OCR_LANGUAGES overrides, e.g. fra+eng."""
    languages = os.environ.get('AUDIVERIS_OCR_LANGUAGES', 'fra').strip()
    if not re.fullmatch(r'[a-z]{3}(\+[a-z]{3})*', languages):
        return []
    return ['-constant', f'org.audiveris.omr.text.Language.defaultSpecification={languages}']


def page_sizes(source):
    """Page sizes in points: PyMuPDF when installed (also on Windows), else poppler's pdfinfo."""
    if paroles.pymupdf is not None:
        try:
            with paroles.pymupdf.open(source) as doc:
                return [(page.rect.width, page.rect.height) for page in doc]
        except Exception:  # noqa: BLE001 -- unreadable here: let pdfinfo or Audiveris report it
            pass
    if not shutil.which('pdfinfo'):
        return []
    result = subprocess.run(['pdfinfo', '-f', '1', '-l', '999999', str(source)],
                            check=False, capture_output=True, text=True, timeout=30)
    if result.returncode:
        return []
    found = re.findall(r'(?:Page\s+\d+\s+size:|Page size:)\s*([\d.]+)\s+x\s+([\d.]+)\s+pts', result.stdout)
    return [(float(w), float(h)) for w, h in found]


def pdf_resolution_options(source):
    """Keep rendered pages below Audiveris's 20 million pixel limit."""
    sizes = page_sizes(source)
    if not sizes:
        return []
    area = max(float(w) * float(h) for w, h in sizes)
    dpi = min(300, math.floor(72 * math.sqrt(18_000_000 / area)))
    if dpi < 1:
        raise ValueError('Dimensions du PDF trop grandes pour être traitées.')
    return ['-constant', f'org.audiveris.omr.image.ImageLoading.pdfResolution={dpi}'] if dpi < 300 else []


def stored_name(folder):
    try:
        return (folder / 'name.txt').read_text(encoding='utf-8')[:200] or 'Partition enregistrée'
    except OSError:
        return 'Partition enregistrée'


def download_name(job, filename):
    """Name results after the imported PDF; keep the stored name for other files."""
    match = re.fullmatch(r'partition-(\d+)\.(mxl|musicxml)', filename)
    stem = score_stem(job)
    stem = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', stem).strip(' .')
    if not match or not stem or job.get('name') == 'Partition enregistrée':
        return filename
    return f'{stem}.{match[2]}' if match[1] == '1' else f'{stem}-{match[1]}.{match[2]}'


CONVERSION_TIMEOUT = 1800  # seconds, all attempts included
MAX_ATTEMPTS = 3
ATTEMPT_MARK = '=== Nouvelle tentative'
# Tesseract (text recognition) sometimes crashes the whole engine; the notes can still be read without it.
WITHOUT_TEXT = ['-constant', 'org.audiveris.omr.text.tesseract.TesseractOCR.useOCR=false']


def last_attempt(text):
    """The part of the log written by the most recent engine run."""
    return text.rsplit(ATTEMPT_MARK, 1)[-1]


def attempt_count(text):
    return text.count(ATTEMPT_MARK) + 1


def engine_stop(text):
    """Where Audiveris stopped if it quit in the middle of a page, else None."""
    if 'End of Book' in text or 'Exporting sheet' in text:
        return None
    steps = STEP_PATTERN.findall(text)
    if not steps:
        return None
    sheets = re.search(r'\|\s*(\d+) sheets? in ', text)
    return {'sheet': int(steps[-1][0]), 'sheets': int(sheets[1]) if sheets else 0, 'step': steps[-1][1]}


def stop_description(stop):
    where = f'pendant l’étape « {STEP_LABELS.get(stop["step"], "Détection des notes").lower()} »'
    if stop['sheets'] > 1:
        where += f', page {stop["sheet"]} sur {stop["sheets"]}'
    return where


def failure(text, attempts):
    """Return (code, message, action) for a failed conversion, from the last engine run."""
    code, message, action = diagnose(text)
    stop = engine_stop(text)
    if code != 'recognition' or 'portées' in message or not stop:
        return code, message + ' Consultez le journal de conversion.', action
    tries = f', même après {attempts} tentatives' if attempts > 1 else ''
    return ('engine_crash', f'Le moteur Audiveris s’est arrêté {stop_description(stop)}{tries}.',
            'Relancez la conversion plus tard. Si le problème persiste, découpez le PDF pour isoler cette page '
            'ou contactez l’administrateur.')


def run_engine(command, folder, options, deadline, attempt):
    """One Audiveris run, appended to conversion.log. Returns the exit code."""
    shutil.rmtree(folder / 'output', ignore_errors=True)
    environment = os.environ.copy()
    environment['JAVA_TOOL_OPTIONS'] = (environment.get('JAVA_TOOL_OPTIONS', '') + ' -Djava.awt.headless=true').strip()
    arguments = command + ['-batch', '-transcribe', '-export'] + options
    arguments += ['-output', str(folder / 'output'), '--', str(folder / 'source.pdf')]
    with (folder / 'conversion.log').open('a' if attempt > 1 else 'w', encoding='utf-8') as stream:
        if attempt > 1:
            without = ' sans reconnaissance du texte' if WITHOUT_TEXT[1] in options else ''
            stream.write(f'\n{ATTEMPT_MARK} ({attempt}/{MAX_ATTEMPTS}){without} ===\n')
            stream.flush()
        timeout = max(1, deadline - time.monotonic())
        return subprocess.run(arguments, check=False, stdout=stream, stderr=subprocess.STDOUT,
                              timeout=timeout, env=environment).returncode


def collect_results(folder):
    """Copy each exported movement as partition-N.mxl and its uncompressed MusicXML."""
    files = []
    for index, source in enumerate(sorted((folder / 'output').rglob('*.mxl')), 1):
        target = folder / f'partition-{index}.mxl'
        shutil.copyfile(source, target)
        xml = folder / f'partition-{index}.musicxml'
        extract_musicxml(target, xml)
        files.extend([target.name, xml.name])
    return files


def write_identity(path, title, composer):
    """Store the title and composer chosen at import inside the MusicXML."""
    tree = ET.parse(path)
    root = tree.getroot()
    if title:
        work = root.find('work')
        if work is None:
            work = ET.Element('work')
            root.insert(0, work)
        work_title = work.find('work-title')
        if work_title is None:
            work_title = ET.SubElement(work, 'work-title')
        work_title.text = title
    if composer:
        identity = root.find('identification')
        if identity is None:
            identity = ET.Element('identification')
            root.insert(1 if root.find('work') is not None else 0, identity)
        creator = identity.find("creator[@type='composer']")
        if creator is None:
            creator = ET.Element('creator', {'type': 'composer'})
            identity.insert(0, creator)
        creator.text = composer
    tree.write(path, encoding='utf-8', xml_declaration=True)


def write_mxl(xml, target):
    """Compressed copy of a MusicXML file, as Audiveris exports it."""
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                 f'<rootfile full-path="{xml.name}" media-type="application/vnd.recordare.musicxml+xml"/>'
                 '</rootfiles></container>\n')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.recordare.musicxml')
        archive.writestr('META-INF/container.xml', container)
        archive.write(xml, xml.name)


def read_musicxml(content, destination):
    """Store an uploaded MusicXML (.musicxml/.xml, or compressed .mxl) after checking it is a score."""
    if content.startswith(b'PK\x03\x04'):
        archive = destination.with_suffix('.upload.mxl')
        archive.write_bytes(content)
        try:
            extract_musicxml(archive, destination)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
            raise ValueError('Archive MXL illisible.') from error
        finally:
            archive.unlink(missing_ok=True)
        return
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError('Ce fichier n’est pas un MusicXML lisible.') from error
    if root.tag.split('}')[-1] not in ('score-partwise', 'score-timewise'):
        raise ValueError('Ce fichier n’est pas un MusicXML.')
    destination.write_bytes(content)


def import_musicxml(job_id, content):
    """A MusicXML that is already correct (MuseScore export…) skips recognition and goes straight to review."""
    folder = DATA / job_id
    xml = folder / 'partition-1.musicxml'
    read_musicxml(content, xml)
    with LOCK:
        job = dict(JOBS[job_id])
    if job.get('title') or job.get('composer'):
        write_identity(xml, job.get('title'), job.get('composer'))
    write_mxl(xml, folder / 'partition-1.mxl')
    update(job_id, status='review', imported=True, files=['partition-1.mxl', 'partition-1.musicxml'], upload='pending',
           reader_links=[], message='MusicXML importé sans reconnaissance. Vérifiez la partition et écoutez-la avant de la déposer dans Paheko.',
           action='Ouvrez « Vérifier et écouter », puis validez le dépôt.')


def convert(job_id, command):
    folder = DATA / job_id
    update(job_id, status='running', message='Reconnaissance de la partition en cours…', started=time.time())
    try:
        options = pdf_resolution_options(folder / 'source.pdf') + ocr_language_options()
        deadline = time.monotonic() + CONVERSION_TIMEOUT
        without_text = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            returncode = run_engine(command, folder, options + (WITHOUT_TEXT if without_text else []), deadline, attempt)
            if returncode == 0 and any((folder / 'output').rglob('*.mxl')):
                break
            text = last_attempt(read_log(folder / 'conversion.log'))
            stop = engine_stop(text)
            if stop is None or attempt == MAX_ATTEMPTS:
                code, message, action = failure(text, attempt)
                update(job_id, status='error', error_code=code, message=message, action=action)
                return
            # A second crash while reading text: try once more without text recognition.
            without_text = without_text or (stop['step'] == 'TEXTS' and attempt >= 2)
            update(job_id, message=f'Le moteur s’est arrêté {stop_description(stop)}. '
                                   f'Nouvelle tentative automatique ({attempt + 1}/{MAX_ATTEMPTS})…')
        files = collect_results(folder)
        with LOCK:
            job = dict(JOBS[job_id])
        if job.get('title') or job.get('composer'):
            for name in files:
                if name.endswith('.musicxml'):
                    write_identity(folder / name, job.get('title'), job.get('composer'))
        (folder / 'reprises').unlink(missing_ok=True)
        message = 'Reconnaissance terminée. Vérifiez la partition et écoutez-la avant de la déposer dans Paheko.'
        if without_text:
            message = ('Reconnaissance terminée sans les paroles ni les textes : le moteur s’arrêtait en les lisant. '
                       'Les notes sont là ; vérifiez la partition et écoutez-la avant de la déposer dans Paheko.')
        update(job_id, status='review', message=message, files=files, upload='pending', reader_links=[],
               without_text=without_text, action='Ouvrez « Vérifier et écouter », puis validez le dépôt.')
    except subprocess.TimeoutExpired:
        update(job_id, status='error', message='La conversion a dépassé la limite de 30 minutes.', error_code='timeout',
               action='Découpez le PDF en plusieurs parties et réimportez une partie.')
    except Exception:
        LOGGER.exception('Conversion worker failed for job %s', job_id)
        update(job_id, status='error', message='Le traitement de la partition a échoué.', error_code='processing',
               action='Consultez le journal et réimportez le PDF. Contactez l’administrateur si le problème persiste.')


def publish_job(job_id):
    folder = DATA / job_id
    with LOCK:
        job = dict(JOBS[job_id])
    try:
        payload = [(download_name(job, name), folder / name) for name in job['files']]
        stem = score_stem(job) or 'partition'
        if (folder / 'source.pdf').is_file():  # an imported MusicXML has no PDF
            payload.append((stem + '.pdf', folder / 'source.pdf'))
        metadata = folder / 'classification.json'
        classification = {k: job.get(k, '') for k in ('title', 'composer', 'part', 'song_id')}
        if job.get('owner'):
            classification['requested_by'] = job['owner']
        if (folder / 'pupitres.json').is_file():
            classification['pupitres'] = json.loads((folder / 'pupitres.json').read_text(encoding='utf-8'))
        metadata.write_text(json.dumps(classification, ensure_ascii=False), encoding='utf-8')
        payload.append((stem + '.partition.json', metadata))
        try:
            published = paheko_upload.publish(payload, detailed=True)
        except Exception:
            LOGGER.exception('Unexpected Paheko publication failure for job %s', job_id)
            published = {'state': 'failed', 'message': 'Envoi vers Paheko impossible.', 'sent': []}
        published = published or {'state': 'disabled', 'message': 'Dépôt Paheko non configuré.', 'sent': []}
        # Only newly uploaded MusicXML files can be linked: existing files may be different scores.
        links = [{'name': name, 'url': reader_url(re.sub(r'\.musicxml$', '', name))} for name in published.get('sent', []) if name.endswith('.musicxml')]
        action = '' if published['state'] == 'sent' else 'Téléchargez vos résultats et ajoutez-les dans Documents/Partitions, ou contactez l’administrateur.'
        if published['state'] == 'existing':
            action = 'Des fichiers de même nom existent déjà. Vérifiez-les dans Documents ou changez le titre avant une nouvelle conversion.'
        score_url = reader_url(stem)
        song_id = job.get('song_id')
        song_url = (score_url.split('?')[0] + '?' + urlencode({'view': 'songs', 'song': song_id})) if song_id and score_url else None
        update(job_id, status='done', message='Conversion terminée. ' + published['message'], files=job['files'],
               upload=published['state'], reader_links=links, action=action,
               song_url=song_url,
               error_code='upload_failed' if published['state'] == 'failed' else '')
    except Exception:
        LOGGER.exception('Unexpected publication worker failure for job %s', job_id)
        update(job_id, status='review', upload='failed', message='Dépôt impossible. Vos résultats restent disponibles.', error_code='upload_failed', action='Réessayez le dépôt ou téléchargez les fichiers.')


# Audiveris processes each page through these steps, in this order.
STEPS = ('LOAD', 'BINARY', 'SCALE', 'GRID', 'HEADERS', 'STEM_SEEDS', 'BEAMS', 'LEDGERS', 'HEADS', 'STEMS',
         'REDUCTION', 'CUE_BEAMS', 'TEXTS', 'MEASURES', 'CHORDS', 'CURVES', 'SYMBOLS', 'LINKS', 'RHYTHMS', 'PAGE')
STEP_LABELS = {'LOAD': 'Lecture de l’image', 'BINARY': 'Lecture de l’image', 'SCALE': 'Repérage des portées',
               'GRID': 'Repérage des portées', 'HEADERS': 'Clés et armures', 'TEXTS': 'Lecture des paroles',
               'MEASURES': 'Mesures et accords', 'CHORDS': 'Mesures et accords', 'CURVES': 'Liaisons et symboles',
               'SYMBOLS': 'Liaisons et symboles', 'LINKS': 'Liaisons et symboles', 'RHYTHMS': 'Rythmes',
               'PAGE': 'Mise en page'}
STEP_PATTERN = re.compile(r'\[[^\]\s]*#(\d+)\]\s+StepMonitoring\s+\d+\s*\|\s*([A-Z_]+)')


def log_progress(text):
    """Estimate progress from the Audiveris log: pages count equally, export takes the last 5 %."""
    sheets = re.search(r'\|\s*(\d+) sheets? in ', text)
    total = int(sheets[1]) if sheets else 0
    if 'Exporting sheet' in text or 'ScoreExporter' in text:
        return {'percent': 97, 'step': 'Export du MusicXML', 'sheet': total, 'sheets': total}
    steps = STEP_PATTERN.findall(text)
    if not total or not steps:
        return {'percent': 0, 'step': 'Démarrage du moteur', 'sheet': 0, 'sheets': total}
    sheet, step = int(steps[-1][0]), steps[-1][1]
    index = STEPS.index(step) if step in STEPS else 0
    done = min(total, sheet - 1 + index / len(STEPS))
    return {'percent': min(95, round(95 * done / total)), 'step': STEP_LABELS.get(step, 'Détection des notes'),
            'sheet': min(sheet, total), 'sheets': total}


def read_log(path, head=16384, tail=65536):
    """Return the start and end of a log without loading a huge file."""
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(0)
            if size <= head + tail:
                return stream.read().decode('utf-8', 'replace')
            start = stream.read(head)
            stream.seek(size - tail)
            return (start + b'\n' + stream.read()).decode('utf-8', 'replace')
    except OSError:
        return ''


def job_views(items):
    """Public job list: running jobs get progress and elapsed time, queued ones their place in line."""
    with LOCK:
        order = [k for k, v in JOBS.items() if v.get('status') in ('queued', 'running')]
        views = [dict(id=k, **v) for k, v in items]
    for view in views:
        if view.get('status') == 'running':
            text = read_log(DATA / view['id'] / 'conversion.log')
            progress = log_progress(last_attempt(text))
            progress['attempt'] = attempt_count(text)
            elapsed = max(0, time.time() - (view.get('started') or time.time()))
            progress['elapsed'] = round(elapsed)
            if progress['percent'] >= 10 and progress['step'] != 'Export du MusicXML':
                progress['remaining'] = round(elapsed * (100 - progress['percent']) / progress['percent'])
            view['progress'] = progress
        elif view.get('status') == 'queued' and view['id'] in order:
            view['ahead'] = order.index(view['id'])
    return views


ACTIVE = ('queued', 'running', 'publishing')


def retry_job(job_id, automatic=False):
    """Run the stored PDF through the engine again after a failure or interruption."""
    command = engine_command()
    if not command:
        return 503, {'error': 'Audiveris est indisponible. Contactez l’administrateur.'}
    folder = DATA / job_id
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return 404, {'error': 'Conversion introuvable.'}
        if job.get('status') != 'error':
            return 409, {'error': 'Seule une conversion échouée ou interrompue peut être relancée.'}
        if not (folder / 'source.pdf').is_file():
            return 410, {'error': 'Le PDF d’origine n’est plus disponible. Importez-le de nouveau.'}
        if sum(j['status'] in ('queued', 'running') for j in JOBS.values()) >= 4:
            return 429, {'error': 'La file est pleine. Attendez la fin d’une conversion.'}
        job.update(status='queued', message='En attente du moteur OMR…', files=[], action='', error_code='', upload='', reader_links=[], started=None)
    shutil.rmtree(folder / 'output', ignore_errors=True)
    stale = [*folder.glob('partition-*'), folder / 'result.json', folder / 'conversion.log']
    if not automatic:
        stale.append(folder / 'reprises')
    for path in stale:
        path.unlink(missing_ok=True)
    POOL.submit(convert, job_id, command)
    return 202, {'id': job_id, 'status': 'queued'}


def delete_job(job_id):
    """Remove a finished conversion and every file stored for it."""
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return 404, {'error': 'Conversion introuvable.'}
        if job.get('status') in ACTIVE:
            return 409, {'error': 'Attendez la fin du traitement avant de supprimer cette conversion.'}
        del JOBS[job_id]
    shutil.rmtree(DATA / job_id, ignore_errors=True)
    return 200, {'deleted': job_id}


MAX_RESUMES = 2  # automatic restarts of the same interrupted conversion


def restore_job(folder, extra=None):
    """Rebuild a job from its folder after a restart."""
    files = sorted(p.name for p in folder.iterdir() if p.suffix in ('.musicxml', '.mxl'))
    details = job_details(folder)
    job = {**(extra or {}), 'name': stored_name(folder), 'status': 'done' if files else 'error',
           'message': 'Résultats restaurés.' if files else 'Conversion interrompue. Cliquez sur « Relancer la conversion ».',
           'files': files, **details}
    # No final result was ever written: the service stopped during the conversion.
    if not files and 'status' not in details and (folder / 'source.pdf').is_file():
        job['interrupted'] = True
    return job


def resume_interrupted():
    """Put conversions stopped by a restart back in the queue, at most MAX_RESUMES times each."""
    if not engine_command():
        return []
    resumed = []
    for job_id, job in list(JOBS.items()):
        if not job.pop('interrupted', False):
            continue
        counter = DATA / job_id / 'reprises'
        try:
            count = int(counter.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            count = 0
        if count >= MAX_RESUMES:
            job['message'] = 'Conversion interrompue plusieurs fois. Cliquez sur « Relancer la conversion ».'
            continue
        counter.write_text(str(count + 1))
        if retry_job(job_id, automatic=True)[0] == 202:
            resumed.append(job_id)
    return resumed


def cleanup_published(days):
    """Local mode: remove conversions already deposited in Paheko, `days` after the deposit."""
    limit = time.time() - days * 86400
    with LOCK:
        expired = []
        for job_id, job in JOBS.items():
            result = DATA / job_id / 'result.json'
            if job.get('status') == 'done' and job.get('upload') == 'sent' and result.is_file() and result.stat().st_mtime < limit:
                expired.append(job_id)
        for job_id in expired:
            del JOBS[job_id]
    for job_id in expired:
        shutil.rmtree(DATA / job_id, ignore_errors=True)
    return expired


ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/preview': ('preview.html', 'text/html; charset=utf-8'),
    '/review.js': ('review.js', 'text/javascript; charset=utf-8'),
    '/score-engine.js': ('score-engine.js', 'text/javascript; charset=utf-8'),
    '/admin.js': ('admin.js', 'text/javascript; charset=utf-8'),
    '/vendor/piano.json': ('vendor/piano.json', 'application/json'),
    '/vendor/oohs.json': ('vendor/voice_oohs.json', 'application/json'),
    '/vendor/choir.json': ('vendor/choir_aahs.json', 'application/json'),
    '/vendor/osmd.js': ('vendor/opensheetmusicdisplay.min.js', 'text/javascript; charset=utf-8'),
    '/paroles': ('paroles.html', 'text/html; charset=utf-8'),
    '/icone.svg': ('icone.svg', 'image/svg+xml'),
    '/paroles.js': ('paroles.js', 'text/javascript; charset=utf-8'),
    '/liseuse': ('liseuse.html', 'text/html; charset=utf-8'),
    '/liseuse.js': ('liseuse.js', 'text/javascript; charset=utf-8'),
}
LISEUSE_LOCK = threading.Semaphore(1)  # une mise en page à la fois : elle charge tout le PDF en mémoire
PAROLES_PATH = re.compile(r'/api/paroles/([a-f0-9]{32})(?:/(page|apercu)/(\d{1,4})|/(calcul|pdf))?')
FILE_TYPES = {
    '.pdf': 'application/pdf',
    '.log': 'text/plain; charset=utf-8',
    '.musicxml': 'application/vnd.recordare.musicxml+xml',
    '.mxl': 'application/vnd.recordare.musicxml',
}


def can_edit_pupitres(job):
    """Pupitres are named during review, or after a deposit that failed."""
    return job.get('status') == 'review' or (job.get('status') == 'done' and job.get('upload') == 'failed')


class Handler(BaseHTTPRequestHandler):
    def json_response(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def file_response(self, path, mime, download=False, download_name=None):
        if not path.is_file():
            return self.json_response(404, {'error': 'Fichier introuvable.'})
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if download:
            name = download_name or path.name
            fallback = re.sub(r'[^A-Za-z0-9._ -]', '_', name)
            self.send_header('Content-Disposition', f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        with path.open('rb') as stream:
            shutil.copyfileobj(stream, self.wfile)

    def bytes_response(self, body, mime, download_name=None, headers=None):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if download_name:
            fallback = re.sub(r'[^A-Za-z0-9._ -]', '_', download_name)
            self.send_header('Content-Disposition', f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        self.wfile.write(body)

    def paroles_request(self, post):
        """Routes of the « Paroles » tab. Returns True when the request was handled."""
        url = urlparse(self.path)
        if not url.path.startswith('/api/paroles'):
            return False
        if url.path == '/api/paroles/statut' and not post:
            self.json_response(200, paroles.status())
            return True
        if not paroles.available():
            self.json_response(503, {'error': 'L’extracteur de paroles n’est pas installé sur cet ordinateur.'})
            return True
        owner = getattr(self, 'user', None)
        if url.path == '/api/paroles' and post:
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BYTES:
                self.json_response(413, {'error': 'Sélectionnez un PDF de moins de 80 Mo.'})
                return True
            content = self.rfile.read(length)
            if len(content) != length or not content.startswith(b'%PDF-'):
                self.json_response(400, {'error': 'Ce fichier n’est pas un PDF valide.'})
                return True
            name = unquote(self.headers.get('X-Filename', 'partition.pdf'))
            self.json_response(200, {'id': paroles.create(DATA, content, name, owner)})
            return True
        match = PAROLES_PATH.fullmatch(url.path)
        session = paroles.get(match[1], owner) if match else None
        if session is None:
            self.json_response(404, {'error': 'Analyse introuvable ou expirée. Importez de nouveau le PDF.'})
            return True
        kind, number, action = match[2], match[3], match[4]
        if not kind and not action and not post:
            self.json_response(200, paroles.describe(session))
            return True
        if session['state'] != 'ready':
            self.json_response(409, {'error': 'L’analyse n’est pas terminée.'})
            return True
        zoom = parse_qs(url.query).get('zoom', ['1.5'])[0]
        try:
            selection = paroles.read_selection(self) if post else None
            if kind == 'page' and not post:
                self.bytes_response(paroles.page_png(session, int(number), float(zoom)), 'image/png')
            elif kind == 'apercu' and post:
                self.bytes_response(paroles.page_png(session, int(number), float(zoom), selection), 'image/png')
            elif action == 'calcul' and post:
                self.json_response(200, paroles.compute(session, selection))
            elif action == 'pdf' and post:
                body, left = paroles.pdf_without_lyrics(session, selection)
                stem = re.sub(r'\.pdf$', '', session['name'], flags=re.IGNORECASE) or 'partition'
                self.bytes_response(body, 'application/pdf', f'{stem}_sans_paroles.pdf', {'X-Remaining': str(left)})
            else:
                self.json_response(404, {'error': 'Page introuvable.'})
        except (ValueError, TypeError, KeyError) as error:
            message = str(error) if str(error).startswith('Aucune') else 'Requête invalide.'
            self.json_response(400, {'error': message})
        return True

    def liseuse_request(self, post):
        """Routes of the « Format liseuse » tab. Returns True when the request was handled."""
        path = urlparse(self.path).path
        if path == '/api/liseuse/statut' and not post:
            self.json_response(200, {'available': liseuse is not None,
                                     'systems': liseuse is not None and liseuse._find_staves is not None,
                                     'musicxml': liseuse is not None and liseuse.verovio is not None})
            return True
        if path != '/api/liseuse' or not post:
            return False
        if liseuse is None:
            self.json_response(503, {'error': 'La mise en page pour liseuse n’est pas installée sur cet ordinateur.'})
            return True
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BYTES:
            self.json_response(413, {'error': 'Sélectionnez un fichier de moins de 80 Mo.'})
            return True
        content = self.rfile.read(length)
        kind = liseuse.kind(content) if len(content) == length else None
        if kind is None:
            self.json_response(400, {'error': 'Choisissez un PDF ou un MusicXML (.musicxml, .xml, .mxl).'})
            return True
        if kind != 'pdf' and liseuse.verovio is None:
            self.json_response(503, {'error': 'La gravure des MusicXML n’est pas installée sur cet ordinateur (bibliothèque verovio).'})
            return True
        size = self.headers.get('X-Note-Size', 'moyenne')
        if size not in liseuse.SIZES:
            self.json_response(400, {'error': 'Taille de notes inconnue.'})
            return True
        name = unquote(self.headers.get('X-Filename', 'partition.pdf'))[:200]
        with LISEUSE_LOCK:
            try:
                body, info = liseuse.convert_any(content, size)
            except (ValueError, RuntimeError) as error:
                message = str(error) if isinstance(error, ValueError) else 'Impossible de lire ce fichier.'
                self.json_response(400, {'error': message})
                return True
        stem = re.sub(SOURCE_SUFFIX, '', name, flags=re.IGNORECASE) or 'partition'
        self.bytes_response(body, 'application/pdf', f'{stem}_liseuse.pdf',
                            {'X-Pages': str(info['pages']), 'X-Systems': str(info['systems']),
                             'X-Engraved': '1' if info.get('engraved') else '0'})
        return True

    def import_request(self):
        """POST /api/jobs/musicxml: a MusicXML file joins the conversions, ready for review."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BYTES:
            return self.json_response(413, {'error': 'Sélectionnez un MusicXML de moins de 80 Mo.'})
        content = self.rfile.read(length)
        if len(content) != length:
            return self.json_response(400, {'error': 'Envoi incomplet. Réessayez.'})
        try:
            details = score_metadata(self.headers)
        except ValueError as exc:
            return self.json_response(400, {'error': str(exc)})
        job_id = uuid.uuid4().hex
        folder = DATA / job_id
        folder.mkdir(parents=True)
        name = unquote(self.headers.get('X-Filename', 'partition.musicxml'))[:200]
        with LOCK:
            JOBS[job_id] = {'name': name, 'status': 'queued', 'message': 'Lecture du MusicXML…', 'files': [], **details}
        try:
            import_musicxml(job_id, content)
        except ValueError as exc:
            with LOCK:
                del JOBS[job_id]
            shutil.rmtree(folder, ignore_errors=True)
            return self.json_response(400, {'error': str(exc)})
        (folder / 'score.json').write_text(json.dumps(details, ensure_ascii=False), encoding='utf-8')
        (folder / 'name.txt').write_text(name, encoding='utf-8')
        return self.json_response(202, {'id': job_id})

    def do_GET(self):
        if self.paroles_request(post=False) or self.liseuse_request(post=False):
            return
        path = urlparse(self.path).path
        if path == '/api/config':
            return self.json_response(200, installation_config.public())
        if path == '/api/health':
            return self.json_response(200, {'engine': bool(engine_command())})
        if path == '/api/jobs':
            with LOCK:
                items = list(reversed(list(JOBS.items())))
            values = job_views(items)
            return self.json_response(200, values)
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/([a-z0-9.-]+))?', path)
        if match:
            job_id, filename = match.groups()
            with LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                return self.json_response(404, {'error': 'Conversion introuvable.'})
            if filename == 'pupitres':
                source = parse_qs(urlparse(self.path).query).get('file', [''])[0]
                if source not in job.get('files', []):
                    return self.json_response(404, {'error': 'Partition introuvable.'})
                try:
                    return self.json_response(200, pupitres.describe(DATA / job_id, source))
                except (ValueError, OSError):
                    return self.json_response(400, {'error': 'MusicXML illisible.'})
            if filename:
                if filename not in job.get('files', []) + ['source.pdf', 'conversion.log']:
                    return self.json_response(404, {'error': 'Fichier introuvable.'})
                mime = FILE_TYPES[Path(filename).suffix]
                download = filename != 'source.pdf'
                return self.file_response(DATA / job_id / filename, mime, download, download_name(job, filename))
            return self.json_response(200, dict(id=job_id, **job))
        if path in ASSETS:
            filename, mime = ASSETS[path]
            return self.file_response(ROOT / 'static' / filename, mime)
        return self.json_response(404, {'error': 'Page introuvable.'})

    def do_POST(self):
        # Only browser requests from this local server may start work.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.json_response(403, {'error': 'Origine refusée.'})
        if self.paroles_request(post=True) or self.liseuse_request(post=True):
            return
        naming_match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/pupitres', urlparse(self.path).path)
        if naming_match:
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 524288:
                    raise ValueError()
                values = json.loads(self.rfile.read(length))
                if not isinstance(values, dict):
                    raise TypeError()
                with LOCK:
                    job = JOBS.get(naming_match[1])
                    if not job:
                        return self.json_response(404, {'error': 'Conversion introuvable.'})
                    if not can_edit_pupitres(job):
                        return self.json_response(409, {'error': 'La partition doit être en cours de vérification.'})
                    if values.get('file') not in job.get('files', []):
                        raise ValueError()
                    result = pupitres.save(DATA / naming_match[1], values['file'], values.get('labels'), values.get('corrections'))
                return self.json_response(200, result)
            except (ValueError, TypeError, KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError):
                return self.json_response(400, {'error': 'Pupitres invalides. Vérifiez les noms et les lignes reconnues.'})
            except OSError:
                return self.json_response(500, {'error': 'Impossible d’enregistrer les pupitres.'})
        publish_match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/publish', urlparse(self.path).path)
        if publish_match:
            with LOCK:
                job = JOBS.get(publish_match[1])
                if not job:
                    return self.json_response(404, {'error':'Conversion introuvable.'})
                if job.get('status') != 'review':
                    return self.json_response(409, {'error':'Conversion déjà déposée ou non terminée.'})
                job.update(status='publishing', message='Dépôt dans Paheko en cours…')
            POOL.submit(publish_job, publish_match[1])
            return self.json_response(200, {'status':'publishing'})
        manage_match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(retry|delete)', urlparse(self.path).path)
        if manage_match:
            job_id, operation = manage_match.groups()
            code, value = retry_job(job_id) if operation == 'retry' else delete_job(job_id)
            return self.json_response(code, value)
        if self.path == '/api/jobs/musicxml':
            return self.import_request()
        if self.path != '/api/jobs':
            return self.json_response(404, {'error': 'Page introuvable.'})
        command = engine_command()
        if not command:
            return self.json_response(503, {'error': 'Audiveris est absent. Installez-le puis configurez AUDIVERIS_CMD (voir README). '})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BYTES:
            return self.json_response(413, {'error': 'Sélectionnez un PDF de moins de 80 Mo.'})
        with LOCK:
            if sum(j['status'] in ('queued', 'running') for j in JOBS.values()) >= 4:
                return self.json_response(429, {'error': 'La file est pleine. Attendez la fin d’une conversion.'})
        content = self.rfile.read(length)
        if len(content) != length or not content.startswith(b'%PDF-'):
            return self.json_response(400, {'error': 'Ce fichier n’est pas un PDF valide.'})
        try:
            details = score_metadata(self.headers)
        except ValueError as exc:
            return self.json_response(400, {'error': str(exc)})
        job_id = uuid.uuid4().hex
        folder = DATA / job_id
        folder.mkdir(parents=True)
        (folder / 'source.pdf').write_bytes(content)
        name = self.headers.get('X-Filename', 'partition.pdf')
        with LOCK:
            JOBS[job_id] = {'name': unquote(name)[:200], 'status': 'queued', 'message': 'En attente du moteur OMR…', 'files': [], **details}
        (folder / 'score.json').write_text(json.dumps(details, ensure_ascii=False), encoding='utf-8')
        (folder / 'name.txt').write_text(JOBS[job_id]['name'], encoding='utf-8')
        POOL.submit(convert, job_id, command)
        return self.json_response(202, {'id': job_id})


if __name__ == '__main__':
    DATA.mkdir(parents=True, exist_ok=True)
    # Restore results after restarting the app, then resume what the restart interrupted.
    for folder in sorted(DATA.iterdir(), key=lambda f: f.stat().st_mtime):
        if folder.is_dir() and re.fullmatch('[a-f0-9]{32}', folder.name):
            JOBS[folder.name] = restore_job(folder)
    resume_interrupted()
    retention = float(os.environ.get('LOCAL_RETENTION_DAYS', '7'))

    def janitor():
        while True:
            cleanup_published(retention)
            time.sleep(6 * 3600)
    threading.Thread(target=janitor, daemon=True).start()
    port = int(os.environ.get('PORT', '8765'))
    print(f'Application disponible sur http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
