# -*- coding: utf-8 -*-
"""
Stage 01 - Exportation - Text extraction
- Reads PDFs from an input folder
- Extracts text (direct extraction by default; OCR optional)
- Writes per-file outputs: <NAME>_extracted.txt and <NAME>_extracted.json
- Writes a run summary: _stage01_summary.json

Requires:
  pip install pymupdf
Optional (only if --enable-ocr is used):
  pip install pillow pytesseract
  + install Tesseract OCR (Windows) and set env var TESSERACT_EXE or add to PATH
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import fitz  # PyMuPDF


# -------------------------
# Helpers
# -------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_stem(name: str) -> str:
    """
    Make a filesystem-friendly filename stem.
    Keeps letters/numbers/._- and replaces spaces and other chars with underscore.
    """
    stem = Path(name).stem
    stem = stem.strip()
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem or "document"


def normalize_text(s: str) -> str:
    """
    Light normalization:
    - normalize line endings
    - remove trailing spaces per line
    - collapse excessive blank lines
    """
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    # Collapse 3+ blank lines to 2
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_page_text(page: fitz.Page, mode: str = "blocks") -> str:
    """
    Extract text with either:
      - mode="text": page.get_text("text")
      - mode="blocks": page.get_text("blocks") sorted by reading order (y, x)
    blocks mode often gives a better order for "table-like" PDFs.
    """
    mode = (mode or "blocks").lower()

    if mode == "text":
        return page.get_text("text") or ""

    # blocks
    blocks = page.get_text("blocks") or []
    # Each block: (x0, y0, x1, y1, "text", block_no, block_type)
    blocks_sorted = sorted(blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
    parts: List[str] = []
    for b in blocks_sorted:
        txt = b[4] if len(b) > 4 else ""
        txt = txt.strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def find_tesseract_exe() -> Optional[str]:
    """
    Resolve Tesseract executable path:
    - env var TESSERACT_EXE
    - typical install location
    - PATH (best effort)
    """
    env = os.environ.get("TESSERACT_EXE")
    if env and Path(env).exists():
        return env

    typical = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if Path(typical).exists():
        return typical

    # If user added to PATH, pytesseract can find it; return None here.
    return None


def ocr_page_to_text(page: fitz.Page, dpi: int, lang: str, tesseract_exe: Optional[str]) -> Tuple[str, str]:
    """
    OCR a page image to text. Returns (text, note).
    Only called if --enable-ocr is used.
    """
    try:
        from PIL import Image  # noqa: F401
        import pytesseract
    except Exception as e:
        raise RuntimeError(f"Missing OCR deps. Install pillow+pytesseract. Details: {e}")

    if tesseract_exe:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract_exe

    # render page to image
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")

    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(img_bytes))

    import pytesseract
    text = pytesseract.image_to_string(im, lang=lang)
    return text, f"OCR used (dpi={dpi}, lang={lang})"


# -------------------------
# Data models
# -------------------------

@dataclass
class PageExtraction:
    page: int
    method: str  # "direct" or "ocr"
    text_chars: int
    text: str
    note: str = ""


@dataclass
class FileExtraction:
    file: str
    generated_at: str
    mode: str
    tesseract: Optional[str]
    pages: List[PageExtraction]
    warnings: List[str]


# -------------------------
# Core
# -------------------------

def process_pdf(
    pdf_path: Path,
    mode: str,
    enable_ocr: bool,
    ocr_lang: str,
    ocr_dpi: int,
    min_chars: int,
) -> Tuple[FileExtraction, Dict]:
    warnings: List[str] = []
    tesseract_exe = find_tesseract_exe() if enable_ocr else None

    doc = fitz.open(str(pdf_path))
    pages_out: List[PageExtraction] = []

    direct_pages = 0
    ocr_pages = 0
    ocr_unavailable = 0
    ocr_error = 0

    for i in range(doc.page_count):
        page = doc.load_page(i)
        txt = extract_page_text(page, mode=mode)
        txt_norm = normalize_text(txt)
        if len(txt_norm) >= min_chars:
            pages_out.append(PageExtraction(page=i + 1, method="direct", text_chars=len(txt_norm), text=txt_norm))
            direct_pages += 1
            continue

        # If direct extraction is too short and OCR is enabled, try OCR
        if enable_ocr:
            try:
                ocr_txt, note = ocr_page_to_text(page, dpi=ocr_dpi, lang=ocr_lang, tesseract_exe=tesseract_exe)
                ocr_txt = normalize_text(ocr_txt)
                if ocr_txt:
                    pages_out.append(PageExtraction(page=i + 1, method="ocr", text_chars=len(ocr_txt), text=ocr_txt, note=note))
                    ocr_pages += 1
                else:
                    pages_out.append(PageExtraction(page=i + 1, method="ocr", text_chars=0, text="", note="OCR returned empty text"))
                    ocr_pages += 1
            except Exception as e:
                # OCR failed; keep empty but track
                pages_out.append(PageExtraction(page=i + 1, method="ocr", text_chars=0, text="", note=f"OCR error: {e}"))
                ocr_error += 1
        else:
            # OCR disabled; keep what we have (even if short), but mark note
            note = f"Direct extraction below min_chars ({min_chars}). OCR disabled."
            pages_out.append(PageExtraction(page=i + 1, method="direct", text_chars=len(txt_norm), text=txt_norm, note=note))

    doc.close()

    # If OCR enabled but tesseract isn't properly installed/available, warn
    if enable_ocr:
        # Not a perfect check, but helps explain run outputs
        if not tesseract_exe and not os.environ.get("TESSERACT_EXE"):
            warnings.append("OCR enabled but TESSERACT_EXE not set. If OCR fails, set env var or add tesseract to PATH.")

    extraction = FileExtraction(
        file=pdf_path.name,
        generated_at=now_iso(),
        mode=mode,
        tesseract=tesseract_exe or os.environ.get("TESSERACT_EXE"),
        pages=pages_out,
        warnings=warnings,
    )

    stats = {
        "file": pdf_path.name,
        "pages": len(pages_out),
        "direct_pages": direct_pages,
        "ocr_pages": ocr_pages,
        "ocr_unavailable": ocr_unavailable,
        "ocr_error": ocr_error,
        "total_chars": sum(p.text_chars for p in pages_out),
    }

    return extraction, stats


def write_outputs(extraction: FileExtraction, out_dir: Path) -> Tuple[Path, Path]:
    stem = safe_stem(extraction.file)

    txt_path = out_dir / f"{stem}_extracted.txt"
    json_path = out_dir / f"{stem}_extracted.json"

    # TXT (human-friendly)
    lines: List[str] = []
    lines.append(f"FILE: {extraction.file}")
    lines.append(f"GENERATED_AT: {extraction.generated_at}")
    lines.append(f"MODE: {extraction.mode}")
    lines.append(f"TESSERACT: {extraction.tesseract or ''}")
    lines.append("==== EXTRACTED TEXT ====\n")

    for p in extraction.pages:
        lines.append(f"--- PAGE {p.page:03d} | method={p.method} | chars={p.text_chars} ---")
        if p.note:
            lines.append(f"NOTE: {p.note}")
        lines.append(p.text or "")
        lines.append("")  # blank line

    if extraction.warnings:
        lines.append("==== WARNINGS ====")
        for w in extraction.warnings:
            lines.append(f"- {w}")

    txt_path.write_text("\n".join(lines), encoding="utf-8")

    # JSON (structured)
    payload = {
        "file": extraction.file,
        "generated_at": extraction.generated_at,
        "mode": extraction.mode,
        "tesseract": extraction.tesseract,
        "pages": [asdict(p) for p in extraction.pages],
        "warnings": extraction.warnings,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return txt_path, json_path


def main():
    ap = argparse.ArgumentParser(description="Stage 01 - Exportation: extract text from PDFs (direct; OCR optional).")
    ap.add_argument("--input", "-i", required=True, help="Input folder containing PDFs")
    ap.add_argument("--output", "-o", required=True, help="Output folder to write extracted .txt/.json")
    ap.add_argument("--mode", choices=["blocks", "text"], default="blocks", help="Extraction mode (blocks often better ordering)")
    ap.add_argument("--min-chars", type=int, default=80, help="If direct text chars < this, OCR may be used (if enabled)")
    ap.add_argument("--enable-ocr", action="store_true", help="Enable OCR fallback for low-text pages (optional)")
    ap.add_argument("--ocr-lang", default="eng+por", help="Tesseract OCR languages (e.g., eng+por)")
    ap.add_argument("--ocr-dpi", type=int, default=300, help="OCR rendering DPI")

    args = ap.parse_args()

    in_dir = Path(args.input).expanduser().resolve()
    out_dir = Path(args.output).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists():
        raise SystemExit(f"Input folder does not exist: {in_dir}")

    pdfs = sorted([p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])
    if not pdfs:
        raise SystemExit(f"No PDFs found in input folder: {in_dir}")

    print(f"IN : {in_dir}")
    print(f"OUT: {out_dir}")
    print(f"MODE: {args.mode} | min_chars={args.min_chars} | OCR={'ON' if args.enable_ocr else 'OFF'}")

    summary: List[Dict] = []
    for pdf in pdfs:
        print(f"\nProcessando: {pdf.name}")
        try:
            extraction, stats = process_pdf(
                pdf_path=pdf,
                mode=args.mode,
                enable_ocr=args.enable_ocr,
                ocr_lang=args.ocr_lang,
                ocr_dpi=args.ocr_dpi,
                min_chars=args.min_chars,
            )
            txt_path, json_path = write_outputs(extraction, out_dir)
            summary.append(stats)

            print(
                f"OK -> {txt_path.name} / {json_path.name} "
                f"| direct={stats['direct_pages']} | ocr={stats['ocr_pages']} | ocr_error={stats['ocr_error']} "
                f"| chars={stats['total_chars']}"
            )
        except Exception as e:
            print(f"ERRO em {pdf.name}: {e}")
            summary.append({"file": pdf.name, "error": str(e)})

    # Write summary
    summary_path = out_dir / "_stage01_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nConcluído.")
    print(f"Resumo: {summary_path}")


if __name__ == "__main__":
    main()
