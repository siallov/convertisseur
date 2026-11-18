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

Ligne de commande : python3 liseuse.py partition.pdf [sortie.pdf]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

try:
    from paroles_scan import _find_staves
except ImportError:  # SciPy absent : découpage sur les blancs seulement
    _find_staves = None

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


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print('Usage : python3 liseuse.py partition.pdf [sortie.pdf]', file=sys.stderr)
        return 2
    source = Path(argv[1])
    target = Path(argv[2]) if len(argv) == 3 else source.with_name(source.stem + '_liseuse.pdf')
    try:
        data, info = convert(source)
    except (ValueError, RuntimeError) as error:
        print(f'Impossible de convertir : {error}', file=sys.stderr)
        return 1
    target.write_bytes(data)
    print(f'{target} : {info["pages"]} pages (d’origine : {info["source_pages"]}), '
          f'{info["systems"]} systèmes.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
