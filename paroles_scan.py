"""Partitions scannées (une image par page) : repérage des paroles sur l'image,
lecture par OCR (Tesseract, si installé) et effacement dans l'image.

Produit le même objet `Analysis` que paroles_core.analyze(), ce qui permet à
l'interface de fonctionner de la même façon pour les deux sortes de PDF.

Principe :
  1. les portées sont repérées bande verticale par bande verticale, ce qui
     tolère une page légèrement penchée ou courbée ;
  2. sous chaque portée, les petites taches d'encre isolées (qui ne touchent
     pas la portée) sont regroupées en lignes de texte puis en mots ;
  3. chaque mot est lu par Tesseract ;
  4. pour effacer, on repeint les pixels du mot avec la couleur du fond
     environnant (blanc, ou jaune s'il est surligné).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np
import pymupdf
from PIL import Image
from scipy import ndimage

import paroles_core as pc

INK = 140            # niveau de gris en dessous duquel un pixel est de l'encre
SCAN_STYLE = ("Paroles (partition scannée)", 0.0)
N_STRIPS = 10
OCR_HEIGHT = 48      # hauteur (px) à laquelle chaque mot est présenté à Tesseract
# caractères possibles dans des paroles : interdit les « © », « 0 », « | »… parasites
OCR_CHARS = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
             "àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ'’,.;:!?-() ")


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def tesseract_langs() -> set[str]:
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                             text=True, timeout=20).stdout
        return {ln.strip() for ln in out.splitlines()[1:] if ln.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()


@dataclass
class ScanPage:
    image: Image.Image                 # image RGB d'origine
    labels: np.ndarray                 # composantes connexes de l'encre
    px_to_pt: float                    # facteur pixels -> points
    origin: tuple[float, float]        # coin haut-gauche de l'image sur la page (pt)
    span_comps: dict[int, list[int]] = field(default_factory=dict)      # span.id -> composantes
    span_row: dict[int, int] = field(default_factory=dict)              # span.id -> n° de ligne
    row_dash_comps: dict[int, list[int]] = field(default_factory=dict)  # n° de ligne -> tirets


# ---------------------------------------------------------------- portées

@dataclass
class _Staff:
    xs: np.ndarray       # abscisses des centres de bandes où la portée a été vue
    tops: np.ndarray
    bottoms: np.ndarray
    space: float
    left: int = 0
    right: int = 0
    bars: list[int] = field(default_factory=list)
    system: int = 0
    index: int = 0

    def top_at(self, x):
        return np.interp(x, self.xs, self.tops)

    def bottom_at(self, x):
        return np.interp(x, self.xs, self.bottoms)


def _staves_in_strip(prof: np.ndarray) -> list[tuple[float, float, float]]:
    """(haut, bas, interligne) des portées vues dans une bande verticale."""
    rows = np.where(prof > 0.5)[0]
    lines = []
    if len(rows):
        start = prev = rows[0]
        for r in list(rows[1:]) + [None]:
            if r is not None and r <= prev + 1:
                prev = r
                continue
            lines.append(((start + prev) / 2, prev - start + 1))
            if r is not None:
                start = prev = r
    out = []
    i = 0
    while i + 4 < len(lines):
        ys = [lines[i + k][0] for k in range(5)]
        gaps = np.diff(ys)
        g = float(np.median(gaps))
        if 6 <= g <= 60 and all(abs(x - g) <= 0.2 * g for x in gaps) \
                and all(lines[i + k][1] <= 0.5 * g for k in range(5)):
            out.append((ys[0], ys[4], g))
            i += 5
        else:
            i += 1
    return out


def _find_staves(ink: np.ndarray) -> list[_Staff]:
    H, W = ink.shape
    edges = np.linspace(0, W, N_STRIPS + 1).astype(int)
    per_strip = []
    for a, b in zip(edges[:-1], edges[1:]):
        per_strip.append(((a + b) / 2, _staves_in_strip(ink[:, a:b].mean(1))))

    # bande de référence : celle qui voit le plus de portées
    ref = max(range(len(per_strip)), key=lambda i: len(per_strip[i][1]))
    staves = []
    for top, bottom, g in per_strip[ref][1]:
        pts = {ref: (top, bottom)}
        for direction in (-1, 1):  # suivre la portée de proche en proche
            last = (top, bottom)
            j = ref + direction
            while 0 <= j < len(per_strip):
                cands = [(t, b) for t, b, _ in per_strip[j][1] if abs(t - last[0]) <= 0.8 * g]
                if cands:
                    last = min(cands, key=lambda c: abs(c[0] - last[0]))
                    pts[j] = last
                j += direction
        idx = sorted(pts)
        staves.append(_Staff(np.array([per_strip[j][0] for j in idx]),
                             np.array([pts[j][0] for j in idx]),
                             np.array([pts[j][1] for j in idx]), g))

    cols = np.arange(W)
    for st in staves:
        # étendue horizontale : la ligne du milieu, suivie le long de la courbe
        mid = ((st.top_at(cols) + st.bottom_at(cols)) / 2).astype(int)
        hit = np.zeros(W, bool)
        for d in range(-3, 4):
            yy = np.clip(mid + d, 0, H - 1)
            hit |= ink[yy, cols]
        hit = ndimage.binary_closing(hit, structure=np.ones(15, bool))
        lab, n = ndimage.label(hit)
        if n:
            sizes = ndimage.sum(hit, lab, range(1, n + 1))
            k = int(np.argmax(sizes)) + 1
            xs = np.where(lab == k)[0]
            st.left, st.right = int(xs.min()), int(xs.max())
        else:
            st.left, st.right = 0, W
        # barres de mesure : colonnes encrées sur toute la hauteur de la portée
        tops = st.top_at(cols).astype(int) + 2
        bots = st.bottom_at(cols).astype(int) - 1
        full = []
        for x in range(st.left, st.right + 1):
            seg = ink[tops[x]:bots[x], x]
            if len(seg) and seg.mean() > 0.92:
                full.append(x)
        bars, prev = [], None
        for x in full:
            if prev is None or x > prev + 2:
                bars.append(x)
            prev = x
        st.bars = bars

    # systèmes : deux portées voisines dont les barres de mesure s'alignent
    system, idx = 0, 0
    for i, st in enumerate(staves):
        if i > 0:
            prev = staves[i - 1]
            inner = [x for x in st.bars if st.left + 3 * st.space < x < st.right - st.space]
            inner_prev = [x for x in prev.bars if prev.left + 3 * prev.space < x < prev.right - prev.space]
            shared = sum(1 for x in inner if any(abs(x - y) <= 5 for y in inner_prev))
            need = max(2, 0.6 * min(len(inner), len(inner_prev)))
            if inner and inner_prev and shared >= need:
                idx += 1
            else:
                system += 1
                idx = 0
        st.system, st.index = system, idx
    return staves


# ---------------------------------------------------------------- OCR

def _ocr_words(img: Image.Image, boxes: list[tuple[int, int, int, int]], lang: str) -> list[str]:
    """Lit chaque mot séparément, en un seul appel à Tesseract (liste d'images)."""
    if not boxes or not tesseract_available():
        return ["" for _ in boxes]
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            crop = img.crop((x0, y0, x1, y1))
            # hauteur normalisée et marge blanche : le plus fiable d'après les essais
            scale = OCR_HEIGHT / max(1, crop.height)
            crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                               Image.LANCZOS)
            canvas = Image.new("L", (crop.width + 40, crop.height + 40), 255)
            canvas.paste(crop, (20, 20))
            p = os.path.join(tmp, f"{i:05d}.png")
            canvas.save(p)
            paths.append(p)
        lst = os.path.join(tmp, "liste.txt")
        with open(lst, "w") as fh:
            fh.write("\n".join(paths) + "\n")
        # psm 7 (« une ligne ») garde les espaces et lit mieux le « l » que psm 8
        args = ["tesseract", lst, "stdout", "--psm", "7",
                "-c", "tessedit_char_whitelist=" + OCR_CHARS,
                "-c", "preserve_interword_spaces=1"]
        if lang:
            args += ["-l", lang]
        try:
            out = subprocess.run(args + ["tsv"], capture_output=True, text=True,
                                 timeout=600).stdout
        except (OSError, subprocess.SubprocessError):
            return ["" for _ in boxes]
    words: dict[int, list[str]] = {}
    for ln in out.splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) >= 12 and parts[0] == "5" and parts[11].strip():
            words.setdefault(int(parts[1]) - 1, []).append(parts[11].strip())
    return [" ".join(words.get(i, [])) for i in range(len(boxes))]


def _clean_ocr(t: str) -> str:
    t = t.replace("|", "l").replace("—", "-").replace("–", "-")
    return t.strip(" _~.·'\"`")  if len(t) > 1 else t.strip()


# ---------------------------------------------------------------- analyse

def analyze_scan(path: str, progress=None) -> pc.Analysis:
    doc = pymupdf.open(path)
    spans: list[pc.Span] = []
    styles: dict = {}
    dashes: dict[int, list[pymupdf.Rect]] = {}
    staves_pt: dict[int, list[pc.Staff]] = {}
    scan_pages: dict[int, ScanPage] = {}
    langs = tesseract_langs() if tesseract_available() else set()
    lang = "fra" if "fra" in langs else ("eng" if "eng" in langs else "")

    for pno, page in enumerate(doc):
        if progress:
            progress(f"Analyse de l'image, page {pno + 1}/{len(doc)}…")
        infos = page.get_image_info(xrefs=True)
        if not infos:
            continue
        info = max(infos, key=lambda i: pymupdf.Rect(i["bbox"]).get_area())
        bbox = pymupdf.Rect(info["bbox"])
        img = Image.open(io.BytesIO(doc.extract_image(info["xref"])["image"])).convert("RGB")
        gray = img.convert("L")
        ink = np.asarray(gray) < INK
        H, W = ink.shape
        k = bbox.width / W
        org = (bbox.x0, bbox.y0)

        def pt(x, y):
            return org[0] + x * k, org[1] + y * k

        staves = _find_staves(ink)
        staves_pt[pno] = [pc.Staff(pt(0, float(np.median(st.tops)))[1],
                                   pt(0, float(np.median(st.bottoms)))[1],
                                   pt(st.left, 0)[0], st.system, st.index) for st in staves]

        labels, n = ndimage.label(ink, structure=np.ones((3, 3), bool))
        sp = ScanPage(img, labels, k, org)
        scan_pages[pno] = sp
        objs = ndimage.find_objects(labels)
        B = np.array([(sl[1].start, sl[0].start, sl[1].stop, sl[0].stop) if sl else (0, 0, 0, 0)
                      for sl in objs], float).reshape(-1, 4)
        ids = np.arange(1, len(B) + 1)
        cx, cy = (B[:, 0] + B[:, 2]) / 2, (B[:, 1] + B[:, 3]) / 2
        w, h = B[:, 2] - B[:, 0], B[:, 3] - B[:, 1]

        # --- lignes de texte sous chaque portée
        page_rows = []   # dict(staff, letters, small, dash, base, top, space)
        for si, st in enumerate(staves):
            s = st.space
            z0 = st.bottom_at(cx) + 0.6 * s
            z1 = staves[si + 1].top_at(cx) - 0.6 * s if si + 1 < len(staves) else np.full_like(cx, H - 1)
            inside = (B[:, 1] >= z0) & (B[:, 3] <= z1) & (B[:, 2] >= st.left - s) & (B[:, 0] <= st.right + s)
            inside &= (w <= 8 * s) & (h <= 2.2 * s) & (w > 0)
            # « lettres » qui définissent les lignes ; virgules et « i » sont rattachés après
            letters = np.where(inside & (h >= 0.45 * s) & ~((w <= 0.45 * s) & (h <= 0.85 * s)))[0]
            if not len(letters):
                continue
            # hauteur relative à la portée : insensible à la courbure de la page
            dy = cy[letters] - st.bottom_at(cx[letters])
            order = np.argsort(dy)
            groups: list[list[int]] = []
            for o in order:
                if groups and abs(dy[o] - np.median([dy[g] for g in groups[-1]])) <= 0.55 * s:
                    groups[-1].append(o)
                else:
                    groups.append([o])
            for g in groups:
                comps = letters[g]
                span_w = B[comps, 2].max() - B[comps, 0].min()
                if len(comps) < 4 or span_w < 0.2 * (st.right - st.left):
                    continue  # numéro de mesure, nuance…
                rel_base = float(np.median(B[comps, 3] - st.bottom_at(cx[comps])))
                rel_top = float(np.median(B[comps, 1] - st.bottom_at(cx[comps])))
                # hauteur des minuscules (a, o, n…) : les plus petites lettres de la ligne
                xh = float(np.percentile(h[comps], 25))
                page_rows.append({"staff": st, "letters": list(comps), "base": rel_base,
                                  "top": rel_top, "space": s, "xh": xh})

        # --- petites composantes : points, apostrophes, virgules, tirets
        smax = max((st.space for st in staves), default=10.0)
        small = np.where(((h < 0.45 * smax) | ((w <= 0.45 * smax) & (h <= 0.85 * smax)))
                         & (w > 0))[0] if page_rows else []
        for row in page_rows:
            row["small"], row["dash"] = [], []
        for c in small:
            for row in page_rows:
                st, s = row["staff"], row["space"]
                ry = cy[c] - st.bottom_at(cx[c])
                if not (row["top"] - 0.9 * s <= ry <= row["base"] + 0.9 * s):
                    continue
                lo = B[row["letters"], 0].min() - 3 * s
                hi = B[row["letters"], 2].max() + 6 * s
                if not (lo <= B[c, 0] <= hi):
                    continue
                # tiret : plus large que haut, fin (les tirets gravés peuvent être très courts),
                # et à mi-hauteur des minuscules (un accent est au-dessus)
                xh = row["xh"]
                if (h[c] <= 0.4 * s and w[c] >= max(0.3 * s, 1.25 * h[c]) and w[c] <= 25 * s
                        and row["base"] - 0.8 * xh <= ry <= row["base"] + 0.1 * xh):
                    row["dash"].append(c)
                elif w[c] <= 1.2 * s:
                    row["small"].append(c)
                break

        # --- mots : lettres proches, coupés aux tirets
        word_boxes, word_info = [], []
        for ri, row in enumerate(page_rows):
            s = row["space"]
            dash_x = sorted((B[c, 0], B[c, 2]) for c in row["dash"])
            comps = sorted(row["letters"] + row["small"], key=lambda c: B[c, 0])
            words: list[list[int]] = []
            right = None
            for c in comps:
                x0 = B[c, 0]
                split_by_dash = right is not None and any(right - 1 <= a and b <= x0 + 1 for a, b in dash_x)
                if words and x0 <= right + 0.75 * s and not split_by_dash:
                    words[-1].append(c)
                    right = max(right, B[c, 2])
                else:
                    words.append([c])
                    right = B[c, 2]
            sp.row_dash_comps[ri] = [int(ids[c]) for c in row["dash"]]
            for wd in words:
                x0, y0 = B[wd, 0].min(), B[wd, 1].min()
                x1, y1 = B[wd, 2].max(), B[wd, 3].max()
                word_boxes.append((int(max(0, x0 - 3)), int(max(0, y0 - 3)),
                                   int(min(W, x1 + 3)), int(min(H, y1 + 3))))
                word_info.append((ri, wd, x0, y0, x1, y1))

        # --- OCR sur une image « texte seul » (sans portées ni notes)
        if progress and lang:
            progress(f"Lecture du texte (OCR), page {pno + 1}/{len(doc)}…")
        keep = np.zeros(len(B) + 1, bool)
        for _, wd, *_ in word_info:
            keep[ids[wd]] = True
        # niveaux de gris d'origine autour des mots (lit mieux qu'une image noir/blanc),
        # tout le reste (portées, notes) en blanc
        near = ndimage.binary_dilation(keep[labels], iterations=2)
        text_only = Image.fromarray(np.where(near, np.asarray(gray), 255).astype(np.uint8))
        texts = _ocr_words(text_only, word_boxes, lang) if lang else ["" for _ in word_boxes]

        dashes[pno] = []
        for ri, row in enumerate(page_rows):
            for c in row["dash"]:
                a, b = pt(B[c, 0], B[c, 1]), pt(B[c, 2], B[c, 3])
                dashes[pno].append(pymupdf.Rect(a[0], a[1], b[0], b[1]))
        for (ri, wd, x0, y0, x1, y1), txt in zip(word_info, texts):
            row = page_rows[ri]
            st = row["staff"]
            xm = (x0 + x1) / 2
            base = float(st.bottom_at(xm)) + row["base"]
            top = float(st.bottom_at(xm)) + row["top"]
            p0, p1 = pt(x0, min(y0, top)), pt(x1, max(y1, base))
            span = pc.Span(id=len(spans), page=pno, bbox=pymupdf.Rect(p0[0], p0[1], p1[0], p1[1]),
                           text=_clean_ocr(txt), font=SCAN_STYLE[0],
                           size=max(4.0, (row["base"] - row["top"]) * k / 0.55),
                           baseline=pt(0, base)[1], style_key=SCAN_STYLE,
                           line_key=(pno, ri))
            spans.append(span)
            sp.span_comps[span.id] = [int(ids[c]) for c in wd]
            sp.span_row[span.id] = ri
            stl = styles.setdefault(SCAN_STYLE, pc.StyleInfo(SCAN_STYLE))
            stl.count += 1
            if span.text and len(stl.samples) < 12 and span.text not in stl.samples:
                stl.samples.append(span.text)

    pc._split_merged_systems(staves_pt)
    an = pc.Analysis(path, len(doc), spans, styles, dashes, False, staves_pt)
    an.scan = scan_pages
    an.ocr_lang = lang
    doc.close()
    return an


# ---------------------------------------------------------------- effacement

def _erase(sp: ScanPage, groups: list[list[int]]) -> np.ndarray:
    """Repeint chaque groupe de composantes : chaque pixel prend la couleur du
    pixel de fond « propre » le plus proche (blanc, ou jaune s'il est surligné)."""
    arr = np.asarray(sp.image).copy()
    H, W = sp.labels.shape
    objs = ndimage.find_objects(sp.labels)
    for comps in groups:
        sls = [objs[c - 1] for c in comps if objs[c - 1] is not None]
        if not sls:
            continue
        m = 24
        Y0 = max(0, min(s[0].start for s in sls) - m)
        Y1 = min(H, max(s[0].stop for s in sls) + m)
        X0 = max(0, min(s[1].start for s in sls) - m)
        X1 = min(W, max(s[1].stop for s in sls) + m)
        lab = sp.labels[Y0:Y1, X0:X1]
        region = arr[Y0:Y1, X0:X1]
        # canal le plus clair : le jaune du surligneur y est aussi clair que le
        # blanc (≈ 250), alors que le gris des bords de lettres est plus sombre.
        bright = region.max(2)
        core = np.isin(lab, comps)
        # à effacer : les lettres, leur bord, et le halo grisâtre un peu plus loin
        mask = ndimage.binary_dilation(core, iterations=3) | (
            ndimage.binary_dilation(core, iterations=10) & (bright < 246))
        # fond propre : clair et loin de toute encre (le halo du scanner est large)
        clean = ~mask & (bright >= 246) & ~ndimage.binary_dilation(lab > 0, iterations=8)
        if not clean.any():
            region[mask] = 255
            continue
        _, (iy, ix) = ndimage.distance_transform_edt(~clean, return_indices=True)
        region[mask] = region[iy[mask], ix[mask]]
    return arr


def remove_lyrics_scan(an: pc.Analysis, lyric_spans: list[pc.Span],
                       jpeg_quality: int = 88, pages: list[int] | None = None) -> pymupdf.Document:
    src = pymupdf.open(an.path)
    out = pymupdf.open()
    for pno, page in enumerate(src):
        if pages is not None and pno not in pages:
            continue
        sp = an.scan.get(pno)
        if sp is None:
            out.insert_pdf(src, from_page=pno, to_page=pno)
            continue
        newp = out.new_page(width=page.rect.width, height=page.rect.height)
        sel = [s for s in lyric_spans if s.page == pno and s.id in sp.span_comps]
        groups = [sp.span_comps[s.id] for s in sel]
        for r in {sp.span_row[s.id] for s in sel}:
            groups += [[c] for c in sp.row_dash_comps.get(r, [])]
        arr = _erase(sp, groups) if groups else np.asarray(sp.image)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "JPEG", quality=jpeg_quality, optimize=True)
        W, H = sp.image.size
        rect = pymupdf.Rect(sp.origin[0], sp.origin[1],
                            sp.origin[0] + W * sp.px_to_pt, sp.origin[1] + H * sp.px_to_pt)
        newp.insert_image(rect, stream=buf.getvalue())
    src.close()
    return out
