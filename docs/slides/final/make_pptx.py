#!/usr/bin/env python3
"""Convert main.pdf into a 16:9 PowerPoint deck (main.pptx) with speaker notes.

Each PDF page becomes one slide holding a full-bleed image of that page, so
fonts and diagrams look exactly as in the PDF. The matching "## Slide NN"
section of speaker_notes.md is written into each slide's notes.

  python3 make_pptx.py [--pdf main.pdf] [--notes speaker_notes.md] [--out main.pptx] [--dpi 220]
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pymupdf
from pptx import Presentation
from pptx.util import Emu

HERE = Path(__file__).resolve().parent
_XML_INVALID_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff￾￿]')
_HEADING_RE = re.compile(r'^##\s+Slide\s+(\d+)\s*$', re.M)


def load_notes(path: Path) -> dict[int, str]:
    text = path.read_text(encoding='utf-8')
    marks = list(_HEADING_RE.finditer(text))
    notes = {}
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        notes[int(m.group(1))] = _XML_INVALID_RE.sub('', text[m.end():end]).strip()
    return notes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pdf', type=Path, default=HERE / 'main.pdf')
    ap.add_argument('--notes', type=Path, default=HERE / 'speaker_notes.md')
    ap.add_argument('--out', type=Path, default=HERE / 'main.pptx')
    ap.add_argument('--dpi', type=int, default=220)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    notes = load_notes(args.notes)
    missing = [n for n in range(1, doc.page_count + 1) if not notes.get(n)]
    if missing:
        raise SystemExit(f'speaker_notes.md has no section for slide(s) {missing}')
    extra = sorted(set(notes) - set(range(1, doc.page_count + 1)))
    if extra:
        raise SystemExit(f'speaker_notes.md has sections beyond the PDF page count: {extra}')

    prs = Presentation()
    first = doc[0].rect
    prs.slide_width = Emu(12192000)  # 13.333 in, 16:9
    prs.slide_height = Emu(round(12192000 * first.height / first.width))
    blank = prs.slide_layouts[6]
    for n, page in enumerate(doc, start=1):
        png = page.get_pixmap(dpi=args.dpi).tobytes('png')
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(io.BytesIO(png), 0, 0, prs.slide_width, prs.slide_height)
        slide.notes_slide.notes_text_frame.text = notes[n]
    prs.save(args.out)
    print(f'{args.out.name}: {doc.page_count} slides with notes, {args.out.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
