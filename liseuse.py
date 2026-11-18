"""Mise en page d'une partition PDF pour liseuse (PocketBook / Vivlio InkPad 3).

Chaque page est découpée en blocs : un bloc par système (portées et paroles
ensemble), plus l'en-tête et les textes isolés. Les marges blanches sont
retirées, puis les blocs sont réempilés, dans l'ordre, sur des pages au format
de l'écran (1404 × 1872 pixels à 300 ppp, soit 118,9 × 158,5 mm). Un système
n'est jamais coupé entre deux pages.

Les PDF numériques restent vectoriels (le contenu d'origine est réutilisé,
seulement rogné et mis à l'échelle). Les scans gardent leur image d'origine.

Besoins : PyMuPDF et numpy ; SciPy pour reconnaître les systèmes (sans lui,
la page est découpée sur les grands espaces blancs seulement).

Un MusicXML (.musicxml, .xml ou .mxl compressé) est gravé directement au
format de l'écran par Verovio (bibliothèque libre de gravure musicale) : la
mise en page est refaite pour l'écran, en trois tailles de notes au choix.

Ligne de commande : python3 liseuse.py partition.pdf|partition.musicxml [sortie.pdf]
                    [--taille grande|moyenne|petite]
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import pymupdf

try:
    from paroles_scan import _find_staves
except ImportError:  # SciPy absent : découpage sur les blancs seulement
    _find_staves = None

try:
    import verovio
except ImportError:  # gravure des MusicXML indisponible
    verovio = None

# Écran de l'InkPad 3 : 1404 × 1872 px à 300 ppp, en points PDF (1/72 de pouce).
PAGE_W = 1404 / 300 * 72      # 336,96 pt = 118,9 mm
PAGE_H = 1872 / 300 * 72      # 449,28 pt = 158,5 mm
MARGIN = 8.0                  # ≈ 2,8 mm
GAP = 6.0                     # espace minimal entre deux blocs sur la page produite
MAX_SCALE = 1.6               # une petite partition n'est pas agrandie au-delà
DPI = 150                     # résolution d'analyse (pas celle du résultat)
INK = 160                     # niveau de gris sous lequel un pixel est de l'encre
MIN_GAP_MM = 1.0              # blanc minimal pour séparer deux blocs
TEXT_GAP_MM = 3.0             # pages sans portée : coupe entre paragraphes


@dataclass
class Block:
    page: int
    clip: pymupdf.Rect        # zone de la page d'origine, en points
    system: bool              # un système musical (sinon en-tête, texte…)


def _ink(page) -> np.ndarray:
    zoom = DPI / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return gray < INK


def _gaps(blank: np.ndarray, start: int, stop: int, minimum: int) -> list[tuple[int, int]]:
    """Suites de lignes blanches (début, fin exclue) d'au moins `minimum` pixels dans [start, stop)."""
    out, run = [], None
    for y in range(max(start, 0), min(stop, len(blank))):
        if blank[y]:
            run = y if run is None else run
        else:
            if run is not None and y - run >= minimum:
                out.append((run, y))
            run = None
    if run is not None and min(stop, len(blank)) - run >= minimum:
        out.append((run, min(stop, len(blank))))
    return out


def _cut(rows: np.ndarray, blank: np.ndarray, start: int, stop: int, minimum: int) -> int | None:
    """Où séparer deux blocs entre les lignes `start` et `stop` : au milieu du plus large blanc,
    sinon sur la ligne la moins encrée."""
    if stop <= start:
        return None
    gaps = _gaps(blank, start, stop, minimum)
    if gaps:
        a, b = max(gaps, key=lambda g: (g[1] - g[0], g[0]))
        return (a + b) // 2
    return start + int(np.argmin(rows[start:stop]))


def _joined(ink: np.ndarray, upper, lower) -> bool:
    """Deux portées voisines sont du même système quand le trait vertical (ou l'accolade) qui
    ouvre le système les relie à gauche."""
    a = int(np.median(upper.bottoms)) - 1
    b = int(np.median(lower.tops)) + 2
    if b <= a:
        return True
    x1 = min(upper.left, lower.left) + 4
    x0 = max(0, x1 - 4 - int(2.5 * max(upper.space, lower.space)))
    column = ink[a:b, x0:x1]
    return bool(column.size and column.mean(0).max() > 0.9)


def _systems(ink: np.ndarray, staves) -> list[tuple[int, int]]:
    """(haut, bas) de chaque système de la page, en pixels."""
    if not staves:
        return []
    links = [_joined(ink, u, l) for u, l in zip(staves, staves[1:])]
    if not any(links):
        # aucun trait de début de système : on se fie à l'alignement des barres de mesure
        links = [u.system == l.system for u, l in zip(staves, staves[1:])]
    systems = [[float(np.median(staves[0].tops)), float(np.median(staves[0].bottoms))]]
    for st, joined in zip(staves[1:], links):
        t, b = float(np.median(st.tops)), float(np.median(st.bottoms))
        if joined:
            systems[-1][1] = b
        else:
            systems.append([t, b])
    return [(int(t), int(b) + 1) for t, b in systems]


def page_blocks(page, number: int) -> list[Block]:
    """Découpe une page en blocs, de haut en bas."""
    ink = _ink(page)
    H, W = ink.shape
    k = 72 / DPI
    rows = ink.sum(1)
    cols = ink.sum(0)
    blank = rows < 2                       # une poussière isolée ne compte pas
    inked = np.where(~blank)[0]
    used_cols = np.where(cols >= 2)[0]
    if not len(inked) or not len(used_cols):
        return []
    top, bottom = int(inked[0]), int(inked[-1]) + 1
    left, right = int(used_cols[0]), int(used_cols[-1]) + 1
    px_mm = DPI / 25.4

    systems: list[tuple[int, int]] = []
    if _find_staves is not None:
        try:
            staves = _find_staves(ink)
        except (ValueError, IndexError):
            staves = []
        systems = _systems(ink, staves)

    minimum = max(1, round(MIN_GAP_MM * px_mm))
    kinds: list[bool] = []
    if systems:
        cuts = [top]
        kinds = []
        # en-tête au-dessus du premier système (titre, auteurs…)
        if _gaps(blank, top, systems[0][0], minimum):
            cut = _cut(rows, blank, top, systems[0][0], minimum)
            cuts.append(cut)
            kinds.append(False)
        for (_, b1), (t2, _) in zip(systems, systems[1:]):
            cuts.append(_cut(rows, blank, b1, t2, minimum) or b1)
            kinds.append(True)
        # pied sous le dernier système (copyright, numéro de page…) : séparé seulement par un
        # grand blanc, pour que les paroles sous la dernière portée restent avec elle
        kinds.append(True)
        footer = _gaps(blank, systems[-1][1], bottom, max(1, round(TEXT_GAP_MM * px_mm)))
        if footer:
            a, b = max(footer, key=lambda g: (g[1] - g[0], g[0]))
            cuts.append((a + b) // 2)
            kinds.append(False)
        cuts.append(bottom)
    else:
        text_min = max(1, round(TEXT_GAP_MM * px_mm))
        cuts = [top] + [(a + b) // 2 for a, b in _gaps(blank, top, bottom, text_min)] + [bottom]
        kinds = [False] * (len(cuts) - 1)

    blocks = []
    for (a, b), system in zip(zip(cuts, cuts[1:]), kinds):
        part = np.where(~blank[a:b])[0]
        if not len(part):
            continue
        y0, y1 = a + int(part[0]), a + int(part[-1]) + 1
        clip = pymupdf.Rect((left - 2) * k, (y0 - 2) * k, (right + 2) * k, (y1 + 2) * k) & page.rect
        if not system and page.get_text('text', clip=clip).strip().isdigit():
            continue  # numéro de page seul
        blocks.append(Block(number, clip, system))
    return blocks


def _running_heads(blocks: list[Block], page_heights: list[float]) -> list[Block]:
    """Retire, à partir de la 2e page, les petits textes au-dessus du premier système ou sous
    le dernier : numéros de page, titres courants, mentions répétées."""
    keep = []
    by_page: dict[int, list[Block]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    for page, items in by_page.items():
        sys_idx = [i for i, b in enumerate(items) if b.system]
        for i, b in enumerate(items):
            small = b.clip.height < 0.03 * page_heights[page]
            outside = sys_idx and (i < sys_idx[0] or i > sys_idx[-1])
            if page > 0 and not b.system and small and outside:
                continue
            keep.append(b)
    return keep


def layout(blocks: list[Block]) -> list[list[tuple[Block, pymupdf.Rect]]]:
    """Place les blocs sur des pages d'écran. Renvoie, pour chaque page, (bloc, cible)."""
    usable_w, usable_h = PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN
    width = max(b.clip.width for b in blocks)
    base = min(usable_w / width, MAX_SCALE)
    pages: list[list[tuple[Block, float]]] = [[]]
    used = 0.0
    for b in blocks:
        scale = min(base, usable_h / b.clip.height)
        h = b.clip.height * scale
        need = h + (GAP if pages[-1] else 0)
        if pages[-1] and used + need > usable_h:
            pages.append([])
            used, need = 0.0, h
        pages[-1].append((b, scale))
        used += need

    out = []
    for n, items in enumerate(pages):
        heights = [b.clip.height * s for b, s in items]
        free = usable_h - sum(heights) - GAP * (len(items) - 1)
        # répartit le blanc entre les systèmes, sauf sur la dernière page (le morceau finit en haut)
        extra = 0.0
        if n < len(pages) - 1 and len(items) > 1:
            extra = min(free / (len(items) - 1), 4 * GAP)
        y = MARGIN
        placed = []
        for (b, s), h in zip(items, heights):
            w = b.clip.width * s
            x = MARGIN + (usable_w - w) / 2 if s < base else MARGIN
            placed.append((b, pymupdf.Rect(x, y, x + w, y + h)))
            y += h + GAP + extra
        out.append(placed)
    return out


def convert(source: bytes | str | Path) -> tuple[bytes, dict]:
    """PDF d'origine → PDF au format liseuse. Renvoie (octets, résumé)."""
    src = pymupdf.open(stream=source, filetype='pdf') if isinstance(source, bytes) else pymupdf.open(source)
    try:
        if src.needs_pass:
            raise ValueError('Ce PDF est protégé par un mot de passe.')
        blocks: list[Block] = []
        for number, page in enumerate(src):
            blocks += page_blocks(page, number)
        blocks = _running_heads(blocks, [p.rect.height for p in src])
        if not blocks:
            raise ValueError('Ce PDF ne contient rien à afficher.')
        pages = layout(blocks)
        out = pymupdf.open()
        for items in pages:
            page = out.new_page(width=PAGE_W, height=PAGE_H)
            for block, target in items:
                page.show_pdf_page(target, src, block.page, clip=block.clip)
        meta = dict(src.metadata or {})
        out.set_metadata({key: meta.get(key) or '' for key in ('title', 'author', 'subject')}
                         | {'creator': 'Atelier partitions — format liseuse'})
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data, {'source_pages': len(src), 'pages': len(pages),
                      'systems': sum(b.system for b in blocks), 'systems_found': _find_staves is not None}
    finally:
        src.close()


# ---------------------------------------------------------------- MusicXML

# Taille des notes : part de la page virtuelle de Verovio ramenée à l'écran. À 1, une portée mesure
# 7,2 mm comme sur une partition imprimée ; en dessous, plus de mesures tiennent sur chaque page.
SIZES = {'grande': 1.0, 'moyenne': 0.8, 'petite': 0.65}
VEROVIO_W, VEROVIO_H = 1189, 1585   # écran en dixièmes de millimètre, l'unité de Verovio
VEROVIO_MARGIN = 50
# Notes des indications de tempo, caractères SMuFL (« metronome marks » et « individual notes ») : les polices
# de texte n'ont pas ♩, on écrit donc la note en toutes lettres (« noire = 100 », « noire pointée = 72 »).
TEMPO_NOTES = {'\ueca2': 'ronde', '\ueca3': 'blanche', '\ueca4': 'blanche', '\ueca5': 'noire', '\ueca6': 'noire',
               '\ueca7': 'croche', '\ueca8': 'croche', '\ueca9': 'double croche', '\uecaa': 'double croche',
               '\uecb7': ' pointée', '\ue1d2': 'ronde', '\ue1d3': 'blanche', '\ue1d5': 'noire', '\ue1d7': 'croche',
               '\ue1e7': ' pointée'}


def kind(data: bytes) -> str | None:
    """'pdf', 'mxl' (MusicXML compressé), 'musicxml', ou None pour un autre fichier."""
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if data.startswith(b'PK\x03\x04'):
        return 'mxl'
    head = data[:4096].lstrip(b'\xef\xbb\xbf \t\r\n')
    if head.startswith(b'<') and re.search(rb'<score-(partwise|timewise)\b', data[:65536]):
        return 'musicxml'
    return None


def musicxml_text(data: bytes) -> str:
    """Le MusicXML lui-même (décompressé s'il vient d'un .mxl), vérifié."""
    if kind(data) == 'mxl':
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                node = ET.fromstring(archive.read('META-INF/container.xml')).find('.//{*}rootfile')
                if node is None:
                    raise ValueError('Archive MXL sans partition.')
                info = archive.getinfo(node.attrib['full-path'])
                if info.file_size > 80 * 1024 * 1024:
                    raise ValueError('MusicXML trop volumineux.')
                data = archive.read(info)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
            raise ValueError('Archive MXL illisible.') from error
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('MusicXML illisible.') from error
    if root.tag.split('}')[-1] not in ('score-partwise', 'score-timewise'):
        raise ValueError('Ce fichier n’est pas un MusicXML.')
    return data.decode('utf-8-sig') if isinstance(data, bytes) else data


def musicxml_title(text: str) -> str:
    root = ET.fromstring(text.encode('utf-8'))
    for path in ('work/work-title', 'movement-title', "credit[credit-type='title']/credit-words"):
        node = root.find(path)
        if node is not None and (node.text or '').strip():
            return node.text.strip()
    return ''


def _svg_page(svg: str) -> pymupdf.Document:
    """Une page SVG de Verovio en PDF vectoriel. MuPDF ignore les règles CSS de Verovio et le
    SVG intérieur sans dimensions : on les remplace par des attributs."""
    size = re.match(r'<svg width="([\d.]+)px" height="([\d.]+)px"', svg)
    if not size:
        raise RuntimeError('Page gravée inattendue.')
    svg = svg.replace('<svg class="definition-scale"',
                      f'<svg width="{size[1]}" height="{size[2]}" stroke="black" class="definition-scale"', 1)
    svg = svg.replace('<text ', '<text stroke="none" ')   # le contour des traits ne doit pas épaissir les paroles
    # Repères internes de Verovio, masqués par sa feuille de style que MuPDF ignore : ils s'afficheraient en points.
    svg = re.sub(r'<g [^>]*class="[^"]*\bplaceholder\b[^"]*"[^>]*>.*?</g>', '', svg, flags=re.S)
    # Les notes des indications de tempo (« ♩ = 100 ») sont des caractères de la police musicale embarquée en CSS,
    # que MuPDF ne charge pas : on les écrit en toutes lettres, à la taille du texte voisin.
    # Verovio dessine ces notes à 720 quand le texte voisin est à 405.
    svg = re.sub(r'<tspan font-family="Leipzig" font-size="([\d.]+)px">([^<]*)</tspan>',
                 lambda m: f'<tspan font-size="{float(m[1]) * 405 / 720:g}px">'
                           + ''.join(TEMPO_NOTES.get(c, '') for c in m[2]) + '</tspan>', svg)
    with pymupdf.open(stream=svg.encode('utf-8'), filetype='svg') as page:
        return pymupdf.open(stream=page.convert_to_pdf(), filetype='pdf')


def convert_musicxml(data: bytes, size: str = 'moyenne') -> tuple[bytes, dict]:
    """MusicXML → PDF au format liseuse, gravé page par page par Verovio."""
    if verovio is None:
        raise RuntimeError('La gravure des MusicXML (bibliothèque verovio) n’est pas installée.')
    factor = SIZES.get(size)
    if factor is None:
        raise ValueError('Taille de notes inconnue.')
    text = musicxml_text(data)
    try:
        verovio.enableLog(verovio.LOG_OFF)
    except AttributeError:
        pass
    # Verovio ne trouve ses polices tout seul que dans le fil principal ; le serveur répond dans d'autres fils.
    toolkit = verovio.toolkit(False)
    if not toolkit.setResourcePath(str(Path(verovio.__file__).parent / 'data')):
        raise RuntimeError('Les polices musicales de Verovio sont introuvables.')
    toolkit.setOptions({'pageWidth': round(VEROVIO_W / factor), 'pageHeight': round(VEROVIO_H / factor),
                        'pageMarginLeft': VEROVIO_MARGIN, 'pageMarginRight': VEROVIO_MARGIN,
                        'pageMarginTop': VEROVIO_MARGIN, 'pageMarginBottom': VEROVIO_MARGIN,
                        'breaks': 'auto', 'footer': 'none', 'scale': 40})
    if not toolkit.loadData(text):
        raise ValueError('Verovio ne sait pas lire ce MusicXML.')
    count = toolkit.getPageCount()
    if not count:
        raise ValueError('Ce MusicXML ne contient aucune mesure.')
    out = pymupdf.open()
    systems = 0
    for number in range(1, count + 1):
        svg = toolkit.renderToSVG(number)
        systems += svg.count('class="system"')
        with _svg_page(svg) as page:
            out.new_page(width=PAGE_W, height=PAGE_H).show_pdf_page(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), page, 0)
    out.set_metadata({'title': musicxml_title(text), 'creator': 'Atelier partitions — format liseuse (Verovio)'})
    body = out.tobytes(garbage=3, deflate=True)
    out.close()
    return body, {'source_pages': 0, 'pages': count, 'systems': systems, 'systems_found': True, 'engraved': True}


def convert_any(data: bytes, size: str = 'moyenne') -> tuple[bytes, dict]:
    """PDF ou MusicXML, selon le contenu du fichier."""
    found = kind(data)
    if found == 'pdf':
        return convert(data)
    if found in ('mxl', 'musicxml'):
        return convert_musicxml(data, size)
    raise ValueError('Choisissez un PDF ou un MusicXML (.musicxml, .xml, .mxl).')


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    size = 'moyenne'
    if '--taille' in args:
        index = args.index('--taille')
        if index + 1 >= len(args):
            args = []
        else:
            size = args[index + 1]
            del args[index:index + 2]
    if not 1 <= len(args) <= 2 or size not in SIZES:
        print('Usage : python3 liseuse.py partition.pdf|partition.musicxml [sortie.pdf] '
              '[--taille grande|moyenne|petite]', file=sys.stderr)
        return 2
    source = Path(args[0])
    target = Path(args[1]) if len(args) == 2 else source.with_name(source.stem + '_liseuse.pdf')
    try:
        data, info = convert_any(source.read_bytes(), size)
    except (ValueError, RuntimeError, OSError) as error:
        print(f'Impossible de convertir : {error}', file=sys.stderr)
        return 1
    target.write_bytes(data)
    origin = 'gravé depuis le MusicXML' if info.get('engraved') else f'd’origine : {info["source_pages"]} pages'
    print(f'{target} : {info["pages"]} pages ({origin}), {info["systems"]} systèmes.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
