"""Analyse d'une partition PDF : repérage des paroles, extraction en texte,
et production d'une copie du PDF sans les paroles.

Fonctionne avec les PDF « numériques » (MuseScore, Sibelius, Finale, LilyPond,
Dorico…) où les paroles sont du vrai texte. Les PDF scannés (images) ne sont
pas pris en charge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

# Polices musicales : leurs glyphes sont des notes, clés, silences… jamais des paroles.
MUSIC_FONT_HINTS = (
    "mscore", "bravura", "leland", "emmentaler", "feta", "petaluma", "maestro",
    "opus", "sonata", "finale", "jazz", "gonville", "musejazz", "november",
    "engraver", "broadway", "smufl", "lilyjazz", "ekmelos", "academico-music",
)


def _is_music_font(font: str) -> bool:
    f = font.lower()
    return any(h in f for h in MUSIC_FONT_HINTS)


def _mostly_private_use(text: str) -> bool:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return True
    pua = sum(1 for c in chars if 0xE000 <= ord(c) <= 0xF8FF)
    return pua * 2 >= len(chars)


@dataclass
class Span:
    id: int
    page: int
    bbox: pymupdf.Rect
    text: str
    font: str
    size: float
    baseline: float
    in_margin: bool = False
    style_key: tuple[str, float] | None = None
    line_key: tuple | None = None   # scans : identifiant de la ligne de texte

    @property
    def style(self) -> tuple[str, float]:
        return self.style_key or (self.font, round(self.size, 1))


@dataclass
class Staff:
    top: float
    bottom: float
    left: float
    system: int = 0   # numéro du système sur la page
    index: int = 0    # rang de la portée dans son système (0 = la plus haute)


@dataclass
class StyleInfo:
    key: tuple[str, float]
    count: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    path: str
    page_count: int
    spans: list[Span]
    styles: dict[tuple[str, float], StyleInfo]
    # Petits traits horizontaux (tirets entre syllabes, traits de prolongation)
    dashes: dict[int, list[pymupdf.Rect]]
    scanned: bool
    staves: dict[int, list[Staff]] = field(default_factory=dict)
    scan: dict | None = None      # partitions scannées : données d'image par page
    ocr_lang: str = ""

    def spans_on(self, page: int) -> list[Span]:
        return [s for s in self.spans if s.page == page]


def _scan_graphics(page) -> tuple[list[Staff], list[pymupdf.Rect]]:
    """Repère les portées (lignes de portée + barres de mesure) et les petits
    traits horizontaux susceptibles d'être des tirets de paroles."""
    W, H = page.rect.width, page.rect.height
    hsegs = []   # (y, x0, x1, épaisseur)
    vsegs = []   # (x, y0, y1)
    for d in page.get_drawings():
        lw = d.get("width") or 0.5
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.3 and abs(a.x - b.x) > 1.0:
                    hsegs.append(((a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x), lw))
                elif abs(a.x - b.x) < 0.3 and abs(a.y - b.y) > 5:
                    vsegs.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.2 and r.width > 1.0:
                    hsegs.append(((r.y0 + r.y1) / 2, r.x0, r.x1, r.height))
                elif r.width < 1.5 and r.height > 5:
                    vsegs.append(((r.x0 + r.x1) / 2, r.y0, r.y1))

    # Lignes de portée : à une même hauteur, longueur cumulée importante.
    total: dict[float, float] = {}
    left: dict[float, float] = {}
    for y, x0, x1, _ in hsegs:
        k = round(y * 2) / 2
        total[k] = total.get(k, 0) + (x1 - x0)
        left[k] = min(left.get(k, x0), x0)
    staff_ys = sorted(y for y, t in total.items() if t > 0.3 * W)
    groups: list[list[float]] = []
    for y in staff_ys:
        if groups and y - groups[-1][-1] <= 0.015 * H:
            groups[-1].append(y)
        else:
            groups.append([y])
    staves = [Staff(g[0], g[-1], min(left[y] for y in g)) for g in groups if len(g) in (1, 4, 5, 6)]

    # Systèmes : deux portées voisines appartiennent au même système si leurs
    # barres de mesure tombent aux mêmes abscisses.
    def barlines(st: Staff) -> set[float]:
        return {round(x, 0) for x, y0, y1 in vsegs
                if y0 <= st.top + 1 and y1 >= st.bottom - 1 and x > st.left + 5}
    bars = [barlines(st) for st in staves]
    system = 0
    idx = 0
    for i, st in enumerate(staves):
        if i > 0:
            shared = bars[i] & bars[i - 1]
            if len(shared) >= 2:
                idx += 1
            else:
                system += 1
                idx = 0
        st.system, st.index = system, idx

    staff_line_ys = {round(y * 2) / 2 for g in groups for y in g}
    dashes = [pymupdf.Rect(x0, y - lw / 2, x1, y + lw / 2) for y, x0, x1, lw in hsegs
              if round(y * 2) / 2 not in staff_line_ys and (x1 - x0) < 0.4 * W]
    return staves, dashes


def _split_merged_systems(all_staves: dict[int, list[Staff]]) -> None:
    """Deux systèmes consécutifs aux mesures identiques peuvent sembler n'en
    faire qu'un : on redécoupe ceux qui font un multiple de la taille usuelle."""
    sizes: dict[int, int] = {}
    for staves in all_staves.values():
        for st in staves:
            sizes[(id(staves), st.system)] = sizes.get((id(staves), st.system), 0) + 1
    if not sizes:
        return
    counts: dict[int, int] = {}
    for n in sizes.values():
        counts[n] = counts.get(n, 0) + 1
    usual = max(counts, key=lambda n: (counts[n], -n))
    if usual < 1:
        return
    for staves in all_staves.values():
        system, prev = -1, None
        for st in staves:
            n = sizes[(id(staves), st.system)]
            if st.system != prev or (n > usual and n % usual == 0 and st.index % usual == 0):
                system += 1
            prev = st.system
            if n > usual and n % usual == 0:
                st.index %= usual
            st.system = system


def _staff_above(staves: list[Staff], y: float) -> Staff | None:
    above = [st for st in staves if st.bottom <= y + 2]
    return max(above, key=lambda st: st.bottom) if above else None


def _in_margin(bbox: pymupdf.Rect, staves: list[Staff]) -> bool:
    """Texte à gauche des portées (noms d'instruments, « S. », « Basse »…)."""
    if not staves:
        return False
    cy = (bbox.y0 + bbox.y1) / 2
    near = min(staves, key=lambda st: min(abs(cy - st.top), abs(cy - st.bottom),
                                          0 if st.top <= cy <= st.bottom else 1e9))
    return bbox.x1 <= near.left + 1


def analyze(path: str) -> Analysis:
    doc = pymupdf.open(path)
    spans: list[Span] = []
    styles: dict[tuple[str, float], StyleInfo] = {}
    dashes: dict[int, list[pymupdf.Rect]] = {}
    all_staves: dict[int, list[Staff]] = {}
    has_images = False

    for pno, page in enumerate(doc):
        if page.get_images():
            has_images = True

        staves, page_dashes = _scan_graphics(page)
        dashes[pno] = page_dashes
        all_staves[pno] = staves

        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for s in line["spans"]:
                    text = s["text"]
                    if not text.strip():
                        continue
                    if _is_music_font(s["font"]) or _mostly_private_use(text):
                        continue
                    bbox = pymupdf.Rect(s["bbox"])
                    sp = Span(
                        id=len(spans), page=pno, bbox=bbox, text=text,
                        font=s["font"], size=s["size"], baseline=s["origin"][1],
                        in_margin=_in_margin(bbox, staves),
                    )
                    spans.append(sp)
                    st = styles.setdefault(sp.style, StyleInfo(sp.style))
                    st.count += 1
                    if len(st.samples) < 12 and text.strip() not in st.samples:
                        st.samples.append(text.strip())

    _split_merged_systems(all_staves)
    scanned = not spans and has_images
    n = len(doc)
    doc.close()
    return Analysis(path, n, spans, styles, dashes, scanned, all_staves)


def guess_lyric_styles(an: Analysis) -> set[tuple[str, float]]:
    """Style le plus fréquent hors marge : en général ce sont les paroles."""
    counts: dict[tuple[str, float], int] = {}
    for s in an.spans:
        if not s.in_margin:
            counts[s.style] = counts.get(s.style, 0) + 1
    if not counts:
        return set()
    best = max(counts, key=counts.get)
    return {best} if counts[best] >= 8 else set()


_NUM_ONLY = re.compile(r"^\s*\d+\s*[.)]?\s*$")


def verse_number_spans(an: Analysis, lyric_spans: list[Span]) -> list[Span]:
    """Numéros de couplet (« 1. », « 2. »…) placés juste avant une ligne de
    paroles, souvent dans un autre style (gras)."""
    chosen = {s.id for s in lyric_spans}
    out = []
    for pno in range(an.page_count):
        page_ly = [s for s in lyric_spans if s.page == pno]
        if not page_ly:
            continue
        lines = _lines(page_ly)
        for s in an.spans_on(pno):
            if s.id in chosen or not _NUM_ONLY.match(s.text):
                continue
            for ln in lines:
                first = ln[0]
                if (abs(first.baseline - s.baseline) <= 0.4 * first.size
                        and -1 <= first.bbox.x0 - s.bbox.x1 <= 6 * first.size):
                    out.append(s)
                    break
    return out


# ---------------------------------------------------------------- extraction

def _dash_between(dashes, a: Span, x_end: float, extender: bool) -> bool:
    """Y a-t-il un trait entre a.x1 et x_end, au niveau des syllabes ?

    extender=False : tiret de césure (à mi-hauteur des minuscules).
    extender=True  : trait de prolongation (au niveau de la ligne de base).
    """
    h = a.bbox.height
    for r in dashes:
        if r.x0 < a.bbox.x1 - 1 or r.x0 > x_end + 1:
            continue
        y = (r.y0 + r.y1) / 2
        if extender:
            if a.baseline - 1 <= y <= a.baseline + 0.25 * h:
                return True
        elif a.bbox.y0 + 0.2 * h <= y < a.baseline - 0.5:
            return True
    return False


def _lines(spans: list[Span]) -> list[list[Span]]:
    """Regroupe les syllabes en lignes (même ligne de base)."""
    if spans and all(s.line_key is not None for s in spans):
        by: dict[tuple, list[Span]] = {}
        for s in spans:
            by.setdefault(s.line_key, []).append(s)
        out = sorted(by.values(), key=lambda ln: (min(x.baseline for x in ln)))
        for ln in out:
            ln.sort(key=lambda s: s.bbox.x0)
        return out
    out: list[list[Span]] = []
    for s in sorted(spans, key=lambda s: (s.baseline, s.bbox.x0)):
        if out and abs(out[-1][0].baseline - s.baseline) <= 0.35 * s.size:
            out[-1].append(s)
        else:
            out.append([s])
    for ln in out:
        ln.sort(key=lambda s: s.bbox.x0)
    return out


VERSE_NUM = re.compile(r"^\s*\d+\s*[.)]\s*")


def _blocks(an: Analysis, lyric_spans: list[Span]):
    """Lignes de paroles groupées en blocs : un bloc = les couplets superposés
    sous une même portée d'un même système. Renvoie [(voix, [(ligne, coupée)])]."""
    blocks: list[tuple[int, list[tuple[list[Span], bool]]]] = []
    for pno in range(an.page_count):
        page_spans = [s for s in lyric_spans if s.page == pno]
        if not page_spans:
            continue
        dashes = an.dashes.get(pno, [])
        staves = an.staves.get(pno, [])
        prev_key = None
        prev_ln = None
        for ln in _lines(page_spans):
            last = ln[-1]
            cut = last.text.rstrip().endswith("-") or _dash_between(
                dashes, last, last.bbox.x1 + 6 * last.size, extender=False)
            st = _staff_above(staves, min(s.bbox.y0 for s in ln))
            if st is not None:
                key = (pno, st.system, st.index)
                same = key == prev_key
                voice = st.index
            else:  # pas de portée détectée : on se fie à l'écart vertical
                key = None
                same = prev_ln is not None and ln[0].baseline - prev_ln[0].baseline <= 2.2 * ln[0].size
                voice = 0
            if same and blocks:
                blocks[-1][1].append((ln, cut))
            else:
                blocks.append((voice, [(ln, cut)]))
            prev_key, prev_ln = key, ln
    return blocks


def voices_summary(an: Analysis, lyric_spans: list[Span]) -> dict[int, tuple[int, str]]:
    """{voix: (nombre de lignes, premiers mots)} — voix = rang de la portée dans le système."""
    out: dict[int, tuple[int, str]] = {}
    for voice, blk in _blocks(an, lyric_spans):
        n, sample = out.get(voice, (0, ""))
        if not sample:
            sample = " ".join(s.text.strip() for s in blk[0][0][:6])
        out[voice] = (n + len(blk), sample)
    return dict(sorted(out.items()))


def _line_text(ln: list[Span], dashes, strip_verse_numbers: bool) -> str:
    parts = []
    join_next = False
    for i, s in enumerate(ln):
        t = s.text.strip()
        if t in ("-", "–", "_", "__"):
            join_next = t in ("-", "–") or join_next
            continue
        t = t.replace("‿", " ").replace("~", " ").strip()
        if parts and not join_next:
            parts.append(" ")
        parts.append(t)  # un « - » écrit dans le texte est un vrai trait d'union
        nxt = ln[i + 1] if i + 1 < len(ln) else None
        join_next = t.endswith("-") or (
            nxt is not None and _dash_between(dashes, s, nxt.bbox.x0, extender=False))
    txt = re.sub(r"\s+", " ", "".join(parts)).strip()
    if strip_verse_numbers:
        txt = VERSE_NUM.sub("", txt)
    return txt


def build_text(an: Analysis, lyric_spans: list[Span], by_verse: bool = True,
               strip_verse_numbers: bool = True, voices: set[int] | None = None) -> str:
    blocks = [(v, b) for v, b in _blocks(an, lyric_spans) if voices is None or v in voices]
    multi = len({v for v, _ in blocks}) > 1

    def lt(ln):
        return _line_text(ln, an.dashes.get(ln[0].page, []), strip_verse_numbers)

    if not by_verse:  # dans l'ordre de la page
        out = []
        for _v, b in blocks:
            out.extend(lt(ln) for ln, _ in b)
            out.append("")
        return "\n".join(out).strip() + "\n"

    sections = []
    for voice in sorted({v for v, _ in blocks}):
        vb = [b for v, b in blocks if v == voice]
        n_verses = max(len(b) for b in vb)
        verses: list[list[str]] = [[] for _ in range(n_verses)]
        pending = [False] * n_verses
        for b in vb:
            for k, (ln, cut) in enumerate(b):
                t = lt(ln)
                if pending[k] and verses[k]:
                    verses[k][-1] += t  # mot coupé en fin de système
                else:
                    verses[k].append(t)
                pending[k] = cut
        body = "\n\n".join("\n".join(v) for v in verses if v)
        if multi:
            body = f"=== Portée {voice + 1} ===\n\n" + body
        sections.append(body)
    return "\n\n\n".join(sections).strip() + "\n"


# ---------------------------------------------------------------- suppression

def _lyric_dashes(an: Analysis, lyric_spans: list[Span]) -> dict[int, list[pymupdf.Rect]]:
    """Tirets et traits de prolongation situés sur les lignes de paroles."""
    out: dict[int, list[pymupdf.Rect]] = {}
    for pno in range(an.page_count):
        page_spans = [s for s in lyric_spans if s.page == pno]
        if not page_spans:
            continue
        bands = []
        for ln in _lines(page_spans):
            top = min(s.bbox.y0 for s in ln)
            base = ln[0].baseline
            size = ln[0].size
            # Toute la largeur : un trait de prolongation peut commencer un
            # système, avant la première syllabe de la ligne.
            bands.append((top + 0.15 * size, base + 0.35 * size, -1e9, 1e9))
        sel = []
        for r in an.dashes.get(pno, []):
            y = (r.y0 + r.y1) / 2
            if any(y0 <= y <= y1 and x0 <= r.x0 <= x1 for y0, y1, x0, x1 in bands):
                sel.append(r)
        out[pno] = sel
    return out


# --- petit analyseur de flux de contenu PDF (juste ce qu'il faut pour le texte)

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_EI = re.compile(rb"\sEI(?=[\s]|$)")


_TOK = re.compile(rb"""[\s\x00]*(?:
 (?P<com>%[^\r\n]*)
|(?P<str>\()
|(?P<dopen><<)|(?P<dclose>>>)
|(?P<hex><[^>]*>)
|(?P<aopen>\[)|(?P<aclose>\])
|(?P<name>/[^\s\x00()<>\[\]{}/%]*)
|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))(?![^\s\x00()<>\[\]{}/%])
|(?P<op>[^\s\x00()<>\[\]{}/%]+)
|(?P<other>[{})])
)""", re.X)
_KIND = {"dopen": "<<", "dclose": ">>", "aopen": "[", "aclose": "]"}


def _tokens(data: bytes):
    """Renvoie (type, début, fin) : type ∈ num, name, str, hex, [, ], <<, >>, op."""
    i, n = 0, len(data)
    match = _TOK.match
    while i < n:
        m = match(data, i)
        if m is None or m.end() == i:
            break
        kind = m.lastgroup
        st = m.start(kind)
        i = m.end()
        if kind in ("com", "other"):
            continue
        if kind == "str":  # chaîne littérale, parenthèses imbriquées possibles
            depth = 1
            while i < n and depth:
                ch = data[i]
                if ch == 0x5C:
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                i += 1
            yield "str", st, i
        elif kind == "op":
            if data[st:i] == b"ID":  # image en ligne : sauter les données binaires
                m2 = _EI.search(data, i + 1)
                i = n if m2 is None else m2.end()
            yield "op", st, i
        else:
            yield _KIND.get(kind, kind), st, i


def _ops(data: bytes):
    """Renvoie (opérateur, début, fin, opérandes[(type, bytes)])."""
    operands = []
    start = None
    depth = 0
    for typ, a, b in _tokens(data):
        if start is None:
            start = a
        if typ in ("[", "<<"):
            depth += 1
            continue
        if typ in ("]", ">>"):
            depth -= 1
            if depth == 0:
                operands.append(("array", data[a:b]))
            continue
        if depth > 0:
            continue
        if typ == "op":
            yield data[a:b].decode("latin-1"), start, b, operands
            operands, start = [], None
        else:
            operands.append((typ, data[a:b]))


def _strip_subset(name: str) -> str:
    return name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name


_SHOW = {"Tj", "TJ", "'", '"'}


def _filter_page_text(doc, page, targets: dict[str, list[pymupdf.Rect]]) -> None:
    """Supprime de la page les affichages de texte dont la police (nom de base)
    est dans `targets` et dont le point d'origine tombe dans l'un des rectangles."""
    page.clean_contents()
    xrefs = page.get_contents()
    if not xrefs:
        return
    xref = xrefs[0]
    data = doc.xref_stream(xref)
    fonts = {f[4]: _strip_subset(f[3]) for f in page.get_fonts()}
    tp = page.transformation_matrix
    to_page = (tp.a, tp.b, tp.c, tp.d, tp.e, tp.f)

    # Index spatial : par police, rectangles rangés par tranches de 10 pt en y.
    index: dict[str, dict[int, list[tuple[float, float, float, float]]]] = {}
    for fname, rects in targets.items():
        buckets = index.setdefault(fname, {})
        for r in rects:
            for k in range(int(r.y0 // 10), int(r.y1 // 10) + 1):
                buckets.setdefault(k, []).append((r.x0, r.y0, r.x1, r.y1))

    def M(a, b, c, d, e, f):
        return (a, b, c, d, e, f)

    def mul(m, n):  # m puis n (convention PDF)
        return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3],
                m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
                m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])

    ctm = M(1, 0, 0, 1, 0, 0)
    font, rise, leading, tr = None, 0.0, 0.0, 0
    stack = []
    tm = tlm = M(1, 0, 0, 1, 0, 0)
    ops = list(_ops(data))
    edits = []  # (début, fin, remplacement)

    def num(x):
        return float(x[1])

    for k, (op, a, b, args) in enumerate(ops):
        try:
            if op == "q":
                stack.append((ctm, font, rise, leading, tr))
            elif op == "Q":
                if stack:
                    ctm, font, rise, leading, tr = stack.pop()
            elif op == "cm" and len(args) == 6:
                ctm = mul(M(*map(num, args)), ctm)
            elif op == "BT":
                tm = tlm = M(1, 0, 0, 1, 0, 0)
            elif op == "Tf" and args:
                font = fonts.get(args[0][1][1:].decode("latin-1"))
            elif op == "Ts" and args:
                rise = num(args[0])
            elif op == "TL" and args:
                leading = num(args[0])
            elif op == "Tr" and args:
                tr = int(num(args[0]))
            elif op in ("Td", "TD") and len(args) == 2:
                tx, ty = num(args[0]), num(args[1])
                if op == "TD":
                    leading = -ty
                tm = tlm = mul(M(1, 0, 0, 1, tx, ty), tlm)
            elif op == "Tm" and len(args) == 6:
                tm = tlm = M(*map(num, args))
            elif op == "T*":
                tm = tlm = mul(M(1, 0, 0, 1, 0, -leading), tlm)
            elif op in _SHOW:
                if op in ("'", '"'):
                    tm = tlm = mul(M(1, 0, 0, 1, 0, -leading), tlm)
                buckets = index.get(font)
                if not buckets:
                    continue
                m = mul(mul(tm, ctm), to_page)
                ox = rise * m[2] + m[4]
                oy = rise * m[3] + m[5]
                if not any(x0 <= ox <= x1 and y0 <= oy <= y1
                           for x0, y0, x1, y1 in buckets.get(int(oy // 10), ())):
                    continue
                nxt = ops[k + 1][0] if k + 1 < len(ops) else "ET"
                if op == "'":
                    rep = b"T*"
                elif op == '"':
                    rep = args[0][1] + b" Tw " + args[1][1] + b" Tc T*"
                elif nxt in _SHOW:  # garder l'avancée du texte : rendu invisible
                    rep = b"3 Tr " + data[a:b] + b" %d Tr" % tr
                else:
                    rep = b""
                edits.append((a, b, rep))
        except (ValueError, IndexError):
            continue

    if not edits:
        return
    out, pos = [], 0
    for a, b, rep in edits:
        out.append(data[pos:a])
        out.append(rep)
        pos = b
    out.append(data[pos:])
    doc.update_stream(xref, b"".join(out))


def remove_lyrics(an: Analysis, lyric_spans: list[Span]) -> pymupdf.Document:
    """Renvoie une copie du document sans les paroles (non enregistrée)."""
    if an.scan is not None:
        import paroles_scan
        return paroles_scan.remove_lyrics_scan(an, lyric_spans)
    doc = pymupdf.open(an.path)
    dashes = _lyric_dashes(an, lyric_spans)
    for pno, page in enumerate(doc):
        page_spans = [s for s in lyric_spans if s.page == pno]
        if not page_spans:
            continue
        # 1) le texte des paroles, retiré instruction par instruction
        targets: dict[str, list[pymupdf.Rect]] = {}
        for s in page_spans:
            targets.setdefault(s.font, []).append(
                pymupdf.Rect(s.bbox.x0 - 1, s.bbox.y0 - 1, s.bbox.x1 + 1, s.bbox.y1 + 1))
        _filter_page_text(doc, page, targets)
        # 2) les tirets : uniquement les traits entièrement couverts
        if dashes.get(pno):
            for r in dashes[pno]:
                page.add_redact_annot(pymupdf.Rect(r.x0 - 0.8, r.y0 - 0.8, r.x1 + 0.8, r.y1 + 0.8),
                                      fill=False, cross_out=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_NONE)
    return doc


def count_remaining(doc: pymupdf.Document, lyric_spans: list[Span]) -> int:
    """Nombre de syllabes encore présentes dans `doc` (contrôle après suppression)."""
    left = 0
    by_page: dict[int, list[Span]] = {}
    for s in lyric_spans:
        by_page.setdefault(s.page, []).append(s)
    for pno, spans in by_page.items():
        buckets: dict[int, list] = {}
        for w in doc[pno].get_text("words"):
            for k in range(int(w[1] // 10), int(w[3] // 10) + 1):
                buckets.setdefault(k, []).append(w)
        for s in spans:
            cx, cy = (s.bbox.x0 + s.bbox.x1) / 2, (s.bbox.y0 + s.bbox.y1) / 2
            if any(w[0] <= cx <= w[2] and w[1] <= cy <= w[3] and w[4].strip() in s.text
                   for w in buckets.get(int(cy // 10), ())):
                left += 1
    return left


def save_without_lyrics(an: Analysis, lyric_spans: list[Span], out_path: str) -> None:
    doc = remove_lyrics(an, lyric_spans)
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
