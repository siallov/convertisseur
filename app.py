#!/usr/bin/env python3
"""Local PDF → MusicXML application. Python standard library only."""
import concurrent.futures
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import uuid
import zipfile
import paheko_upload
import installation_config
import pupitres
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlparse, urlencode, parse_qs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MAX_BYTES = 80 * 1024 * 1024
JOBS = {}
LOCK = threading.Lock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def engine_command():
    value = os.environ.get('AUDIVERIS_CMD')
    if value:
        command = shlex.split(value)
        if command and shutil.which(command[0]):
            return command
        return None
    executable = shutil.which('audiveris') or shutil.which('Audiveris')
    if not executable:
        for candidate in ('/opt/audiveris/bin/Audiveris', '/opt/audiveris/bin/audiveris'):
            if os.access(candidate, os.X_OK):
                executable = candidate
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
            result.update(json.loads((folder / name).read_text()))
        except (OSError, ValueError):
            pass
    return result


def score_stem(job):
    title = job.get('title') or re.sub(r'\.pdf$', '', job.get('name', ''), flags=re.I)
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
                (DATA / job_id / "result.json").write_text(json.dumps(values, ensure_ascii=False))
            except OSError:
                pass


def pdf_resolution_options(source):
    """Keep rendered pages below Audiveris's 20 million pixel limit."""
    if not shutil.which('pdfinfo'):
        return []
    result = subprocess.run(['pdfinfo', '-f', '1', '-l', '999999', str(source)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return []
    sizes = re.findall(r'(?:Page\s+\d+\s+size:|Page size:)\s*([\d.]+)\s+x\s+([\d.]+)\s+pts', result.stdout)
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


def convert(job_id, command):
    folder = DATA / job_id
    update(job_id, status='running', message='Reconnaissance de la partition en cours…')
    try:
        log = folder / 'conversion.log'
        resolution_options = pdf_resolution_options(folder / 'source.pdf')
        environment = os.environ.copy()
        environment['JAVA_TOOL_OPTIONS'] = (environment.get('JAVA_TOOL_OPTIONS', '') + ' -Djava.awt.headless=true').strip()
        with log.open('w') as stream:
            result = subprocess.run(command + ['-batch', '-transcribe', '-export'] + resolution_options + ['-output', str(folder / 'output'), '--', str(folder / 'source.pdf')], stdout=stream, stderr=subprocess.STDOUT, timeout=1800, env=environment)
        if result.returncode:
            code, message, action = diagnose(log.read_text(errors='replace'))
            update(job_id, status='error', error_code=code, message=message + ' Consultez le journal de conversion.', action=action)
            return
        files = []
        for index, source in enumerate(sorted((folder / 'output').rglob('*.mxl')), 1):
            target = folder / f'partition-{index}.mxl'
            shutil.copyfile(source, target)
            xml = folder / f'partition-{index}.musicxml'
            extract_musicxml(target, xml)
            files.extend([target.name, xml.name])
        if not files:
            raise RuntimeError('Audiveris n’a produit aucune partition MXL. Consultez le journal.')
        with LOCK:
            job = dict(JOBS[job_id])
        for name in files:
            if name.endswith('.musicxml') and (job.get('title') or job.get('composer')):
                tree = ET.parse(folder / name)
                root = tree.getroot()
                if job.get('title'):
                    work = root.find('work')
                    if work is None:
                        work = ET.Element('work')
                        root.insert(0, work)
                    title = work.find('work-title')
                    if title is None:
                        title = ET.SubElement(work, 'work-title')
                    title.text = job['title']
                if job.get('composer'):
                    identity = root.find('identification')
                    if identity is None:
                        identity = ET.Element('identification')
                        root.insert(1 if root.find('work') is not None else 0, identity)
                    creator = identity.find("creator[@type='composer']")
                    if creator is None:
                        creator = ET.Element('creator', {'type': 'composer'})
                        identity.insert(0, creator)
                    creator.text = job['composer']
                tree.write(folder / name, encoding='utf-8', xml_declaration=True)
        update(job_id, status='review', message='Reconnaissance terminée. Vérifiez la partition et écoutez-la avant de la déposer dans Paheko.', files=files, upload='pending', reader_links=[], action='Ouvrez « Vérifier et écouter », puis validez le dépôt.')

    except subprocess.TimeoutExpired:
        update(job_id, status='error', message='La conversion a dépassé la limite de 30 minutes.', error_code='timeout', action='Découpez le PDF en plusieurs parties et réimportez une partie.')
    except Exception as exc:
        update(job_id, status='error', message='Le traitement de la partition a échoué.', error_code='processing', action='Consultez le journal et réimportez le PDF. Contactez l’administrateur si le problème persiste.')


def publish_job(job_id):
    folder = DATA / job_id
    with LOCK:
        job = dict(JOBS[job_id])
    try:
        payload = [(download_name(job, name), folder / name) for name in job['files']]
        stem = score_stem(job) or 'partition'
        payload.append((stem + '.pdf', folder / 'source.pdf'))
        metadata = folder / 'classification.json'
        classification={k: job.get(k, '') for k in ('title', 'composer', 'part', 'song_id')}
        if (folder/'pupitres.json').is_file():classification['pupitres']=json.loads((folder/'pupitres.json').read_text())
        metadata.write_text(json.dumps(classification, ensure_ascii=False))
        payload.append((stem + '.partition.json', metadata))
        try:
            published = paheko_upload.publish(payload, detailed=True)
        except Exception:
            published = {'state': 'failed', 'message': 'Envoi vers Paheko impossible.', 'sent': []}
        published = published or {'state': 'disabled', 'message': 'Dépôt Paheko non configuré.', 'sent': []}
        # Only newly uploaded MusicXML files can be linked: existing files may be different scores.
        links = [{'name': name, 'url': reader_url(re.sub(r'\.musicxml$', '', name))} for name in published.get('sent', []) if name.endswith('.musicxml')]
        action = '' if published['state'] == 'sent' else 'Téléchargez vos résultats et ajoutez-les dans Documents/Partitions, ou contactez l’administrateur.'
        if published['state'] == 'existing':
            action = 'Des fichiers de même nom existent déjà. Vérifiez-les dans Documents ou changez le titre avant une nouvelle conversion.'
        update(job_id, status='done', message='Conversion terminée. ' + published['message'], files=job['files'],
               upload=published['state'], reader_links=links, action=action,
               song_url=(reader_url(stem).split('?')[0] + '?' + urlencode({'view': 'songs', 'song': job['song_id']})) if job.get('song_id') and reader_url(stem) else None,
               error_code='upload_failed' if published['state'] == 'failed' else '')
    except Exception:
        update(job_id, status='review', upload='failed', message='Dépôt impossible. Vos résultats restent disponibles.', error_code='upload_failed', action='Réessayez le dépôt ou téléchargez les fichiers.')


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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/config':
            return self.json_response(200, installation_config.public())
        if path == '/api/health':
            return self.json_response(200, {'engine': bool(engine_command())})
        if path == '/api/jobs':
            with LOCK:
                values = [dict(id=k, **v) for k, v in reversed(list(JOBS.items()))]
            return self.json_response(200, values)
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/([a-z0-9.-]+))?', path)
        if match:
            job_id, filename = match.groups()
            with LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                return self.json_response(404, {'error': 'Conversion introuvable.'})
            if filename:
                if filename == 'pupitres':
                    source = parse_qs(urlparse(self.path).query).get('file',[''])[0]
                    if source not in job.get('files',[]):
                        return self.json_response(404,{'error':'Partition introuvable.'})
                    try:return self.json_response(200,pupitres.describe(DATA/job_id,source))
                    except (ValueError,OSError):return self.json_response(400,{'error':'MusicXML illisible.'})
                if filename not in job.get('files', []) + ['source.pdf', 'conversion.log']:
                    return self.json_response(404, {'error': 'Fichier introuvable.'})
                mime = 'application/pdf' if filename.endswith('.pdf') else 'text/plain; charset=utf-8' if filename.endswith('.log') else 'application/vnd.recordare.musicxml+xml' if filename.endswith('.musicxml') else 'application/vnd.recordare.musicxml'
                return self.file_response(DATA / job_id / filename, mime, filename != 'source.pdf', download_name(job, filename))
            return self.json_response(200, dict(id=job_id, **job))
        assets = {'/score-engine.js': ('score-engine.js', 'text/javascript; charset=utf-8'), '/preview': ('preview.html', 'text/html; charset=utf-8'), '/review.js': ('review.js', 'text/javascript; charset=utf-8'), '/vendor/osmd.js': ('vendor/opensheetmusicdisplay.min.js', 'text/javascript; charset=utf-8'), '/admin.js': ('admin.js', 'text/javascript; charset=utf-8'), '/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in assets:
            filename, mime = assets[path]
            return self.file_response(ROOT / 'static' / filename, mime)
        return self.json_response(404, {'error': 'Page introuvable.'})

    def do_POST(self):
        # Only browser requests from this local server may start work.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.json_response(403, {'error': 'Origine refusée.'})
        naming_match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/pupitres', urlparse(self.path).path)
        if naming_match:
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=524288:raise ValueError()
                values=json.loads(self.rfile.read(length))
                if not isinstance(values,dict):raise ValueError()
                with LOCK:
                    job=JOBS.get(naming_match[1])
                    if not job:return self.json_response(404,{'error':'Conversion introuvable.'})
                    if job.get('status')!='review' and not (job.get('status')=='done' and job.get('upload')=='failed'):
                        return self.json_response(409,{'error':'La partition doit être en cours de vérification.'})
                    if values.get('file') not in job.get('files',[]):raise ValueError()
                    result=pupitres.save(DATA/naming_match[1],values['file'],values.get('labels'),values.get('corrections'))
                return self.json_response(200,result)
            except (ValueError,TypeError,KeyError,StopIteration,zipfile.BadZipFile,ET.ParseError):
                return self.json_response(400,{'error':'Pupitres invalides. Vérifiez les noms et les lignes reconnues.'})
            except OSError:return self.json_response(500,{'error':'Impossible d’enregistrer les pupitres.'})
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
        (folder / 'score.json').write_text(json.dumps(details, ensure_ascii=False))
        (folder / 'name.txt').write_text(JOBS[job_id]['name'], encoding='utf-8')
        POOL.submit(convert, job_id, command)
        return self.json_response(202, {'id': job_id})


if __name__ == '__main__':
    DATA.mkdir(exist_ok=True)
    # Restore completed results after restarting the app.
    for folder in DATA.iterdir():
        if folder.is_dir() and re.fullmatch('[a-f0-9]{32}', folder.name):
            files = sorted(p.name for p in folder.iterdir() if p.suffix in ('.musicxml', '.mxl'))
            JOBS[folder.name] = {'name': stored_name(folder), 'status': 'done' if files else 'error', 'message': 'Résultats restaurés.' if files else 'Conversion interrompue. Importez de nouveau le PDF.', 'files': files, **job_details(folder)}
    port = int(os.environ.get('PORT', '8765'))
    print(f'Application disponible sur http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
