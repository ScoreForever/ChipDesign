#!/usr/bin/env python3
"""Convert main.pdf into a 16:9 PowerPoint deck (main.pptx) for presenting.

Each PDF page becomes one slide holding a full-bleed high-resolution image of
that page, so fonts, formulas and diagrams look exactly as in the PDF. The
text of each page is copied into the slide notes. Edit the LaTeX source and
re-run this script to change content.

  pip install pymupdf python-pptx
  python3 make_pptx.py [--pdf main.pdf] [--out main.pptx] [--dpi 220]
"""
from __future__ import annotations
import argparse
import io
from pathlib import Path

import pymupdf
from pptx import Presentation
from pptx.util import Emu

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pdf', type=Path, default=HERE / 'main.pdf')
    ap.add_argument('--out', type=Path, default=HERE / 'main.pptx')
    ap.add_argument('--dpi', type=int, default=220)
    args = ap.parse_args()
    doc = pymupdf.open(args.pdf)
    prs = Presentation()
    first = doc[0].rect
    prs.slide_width = Emu(12192000)  # 13.333 in, 16:9
    prs.slide_height = Emu(round(12192000 * first.height / first.width))
    blank = prs.slide_layouts[6]
    for page in doc:
        png = page.get_pixmap(dpi=args.dpi).tobytes('png')
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(io.BytesIO(png), 0, 0, prs.slide_width, prs.slide_height)
        text = page.get_text().strip()
        if text:
            slide.notes_slide.notes_text_frame.text = text
    prs.save(args.out)
    print(f'{args.out.name}: {doc.page_count} slides, {args.out.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
