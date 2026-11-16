"""« Paroles » tab: find the lyrics in a PDF score, extract them as text and
produce a copy of the PDF without them.

The analysis itself lives in paroles_core.py (digital PDFs) and paroles_scan.py
(scanned PDFs). They need PyMuPDF, numpy, SciPy and Pillow; without them the
tab reports that it is unavailable and the rest of the application still works.
"""
import concurrent.futures
import json
import shutil
import threading
import time
import uuid

try:
    import pymupdf
    import paroles_core as pc
except ImportError:  # optional libraries not installed
    pymupdf = pc = None
try:
    import paroles_scan as pscan
except ImportError:
    pscan = None

RETENTION = 2 * 3600  # an analysis is kept two hours after its last use
MAX_ZOOM = 3.0
SESSIONS = {}
LOCK = threading.Lock()
# Scanned pages take about ten seconds each: one analysis at a time, apart from the conversions.
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def available():
    return pc is not None


def status():
    """What the tab can do on this computer."""
    tesseract = bool(pscan and pscan.tesseract_available())
    return {'available': available(), 'scans': pscan is not None, 'tesseract': tesseract,
            'french': tesseract and 'fra' in pscan.tesseract_langs()}


def folder(root, session_id):
    return root / 'paroles' / session_id


def cleanup(root):
    now = time.time()
    with LOCK:
        expired = [k for k, v in SESSIONS.items() if v['state'] != 'analysing' and now - v['used'] > RETENTION]
        for key in expired:
            del SESSIONS[key]
    for key in expired:
        shutil.rmtree(folder(root, key), ignore_errors=True)


def create(root, content, name, owner=None):
    """Store the PDF and start its analysis. Returns the session id."""
    cleanup(root)
    session_id = uuid.uuid4().hex
    path = folder(root, session_id)
    path.mkdir(parents=True)
    (path / 'source.pdf').write_bytes(content)
    with LOCK:
        SESSIONS[session_id] = {'state': 'analysing', 'message': 'Lecture du PDF…', 'name': name[:200],
                                'owner': owner, 'used': time.time(), 'analysis': None, 'pages': {}}
    POOL.submit(_analyse, session_id, str(path / 'source.pdf'))
    return session_id


def _set(session_id, **values):
    with LOCK:
        if session_id in SESSIONS:
            SESSIONS[session_id].update(values)


def _analyse(session_id, path):
    try:
        an = pc.analyze(path)
        if an.scanned:
            if pscan is None:
                return _set(session_id, state='error', message='Partition scannée : le traitement des images '
                                                                '(numpy, Pillow, SciPy) n’est pas installé.')
            _set(session_id, message='Partition scannée : analyse de l’image (environ 10 secondes par page)…')
            an = pscan.analyze_scan(path, lambda text: _set(session_id, message=text))
        if not an.spans:
            message = ('Aucune ligne de paroles repérée sous les portées.' if an.scan is not None
                       else 'Aucun texte trouvé dans ce PDF.')
            return _set(session_id, state='error', message=message)
        message = f'{len(an.spans)} textes repérés.'
        if an.scan is not None:
            message = (f'Partition scannée : {len(an.spans)} mots repérés et lus par reconnaissance de texte. '
                       'Relisez le texte : la reconnaissance fait des erreurs.' if an.ocr_lang else
                       'Partition scannée : les paroles sont repérées et peuvent être effacées, mais le texte '
                       'ne peut pas être lu (Tesseract avec le français n’est pas installé).')
        _set(session_id, state='ready', message=message, analysis=an)
    except Exception as error:  # noqa: BLE001 -- any unreadable PDF ends here
        _set(session_id, state='error', message=f'Impossible de lire ce PDF : {error}')


def get(session_id, owner=None):
    """The session if it exists and belongs to `owner` (shared mode), else None."""
    with LOCK:
        session = SESSIONS.get(session_id)
        if session is None or session['owner'] != owner:
            return None
        session['used'] = time.time()
        return session


def style_id(key):
    return f'{key[0]}|{key[1]}'


def describe(session):
    """Everything the page needs once, after the analysis."""
    an = session['analysis']
    info = {'state': session['state'], 'message': session['message'], 'name': session['name']}
    if an is None:
        return info
    doc = pymupdf.open(an.path)
    sizes = [[page.rect.width, page.rect.height] for page in doc]
    doc.close()
    guessed = pc.guess_lyric_styles(an)
    styles = sorted(an.styles.values(), key=lambda s: -s.count)
    info.update(
        pages=sizes, scanned=an.scan is not None, ocr=bool(an.ocr_lang),
        styles=[{'id': style_id(s.key), 'font': s.key[0], 'size': s.key[1], 'count': s.count,
                 'samples': s.samples[:6], 'selected': s.key in guessed} for s in styles],
        spans=[{'id': s.id, 'page': s.page, 'box': [round(v, 2) for v in s.bbox], 'text': s.text.strip(),
                'style': style_id(s.style), 'margin': s.in_margin} for s in an.spans])
    return info


def lyric_spans(an, selection):
    """Apply the user's choices: chosen styles, margin option and clicked words."""
    styles = set(selection.get('styles') or [])
    margin = bool(selection.get('ignore_margin', True))
    overrides = {int(k): bool(v) for k, v in (selection.get('overrides') or {}).items()}

    def chosen(span):
        if span.id in overrides:
            return overrides[span.id]
        return style_id(span.style) in styles and not (margin and span.in_margin)

    base = [s for s in an.spans if chosen(s)]
    numbers = [s for s in pc.verse_number_spans(an, base) if overrides.get(s.id, True)]
    return sorted(base + numbers, key=lambda s: s.id)


def compute(session, selection):
    """Selected spans, staves and generated text for the current choices."""
    an = session['analysis']
    spans = lyric_spans(an, selection)
    voices = pc.voices_summary(an, spans)
    wanted = selection.get('voices')
    wanted = {int(v) for v in wanted} if isinstance(wanted, list) else None
    if an.scan is not None and not an.ocr_lang:
        text = ('(Partition scannée : le texte ne peut pas être lu tant que Tesseract n’est pas installé '
                'avec le français. L’effacement des paroles dans le PDF fonctionne déjà.)\n')
    else:
        text = pc.build_text(an, spans, by_verse=bool(selection.get('by_verse', True)),
                             strip_verse_numbers=bool(selection.get('strip_numbers', True)), voices=wanted)
    return {'lyrics': [s.id for s in spans], 'text': text,
            'voices': [{'voice': v, 'lines': n, 'sample': sample} for v, (n, sample) in voices.items()]}


def page_png(session, number, zoom, selection=None):
    """Render one page; with a selection, render it without the selected lyrics."""
    an = session['analysis']
    if not 0 <= number < an.page_count:
        raise ValueError('page')
    zoom = max(0.5, min(MAX_ZOOM, zoom))
    if selection is None:
        doc, index = pymupdf.open(an.path), number
    else:
        spans = lyric_spans(an, selection)
        if an.scan is not None:
            doc, index = pscan.remove_lyrics_scan(an, spans, pages=[number]), 0
        else:
            doc, index = pc.remove_lyrics(an, spans), number
    try:
        return doc[index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes('png')
    finally:
        doc.close()


def pdf_without_lyrics(session, selection):
    """Returns (pdf bytes, number of syllables that could not be removed)."""
    an = session['analysis']
    spans = lyric_spans(an, selection)
    if not spans:
        raise ValueError('Aucune parole sélectionnée.')
    doc = pc.remove_lyrics(an, spans)
    try:
        left = 0 if an.scan is not None else pc.count_remaining(doc, spans)
        return doc.tobytes(garbage=3, deflate=True), left
    finally:
        doc.close()


def read_selection(handler, limit=1_000_000):
    length = int(handler.headers.get('Content-Length', '0'))
    if not 0 < length <= limit:
        raise ValueError('selection')
    value = json.loads(handler.rfile.read(length))
    if not isinstance(value, dict):
        raise ValueError('selection')
    return value
