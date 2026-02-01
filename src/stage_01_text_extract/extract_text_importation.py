from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional

import fitz  # PyMuPDF
from PIL import Image, ImageOps

try:
    import pytesseract
except Exception:
    pytesseract = None


@dataclass
class PageExtraction:
    page: int
    method: str          # direct | ocr | ocr_unavailable | ocr_error
    text_chars: int
    text: str
    note: str = ""


def clean_text(text: str) -> str:
    text = (text or "").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_page_to_pil(page: fitz.Page, dpi: int) -> Image.Image:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)
    img = img.point(lambda x: 0 if x < 160 else 255, mode="1")
    return img


def try_configure_tesseract() -> Optional[str]:
    """
    Tenta achar o executÃ¡vel do tesseract:
    - ENV: TESSERACT_EXE
    - PATH
    - caminhos comuns no Windows
    """
    if pytesseract is None:
        return None

    env_path = os.getenv("TESSERACT_EXE")
    if env_path and Path(env_path).exists():
        pytesseract.pytesseract.tesseract_cmd = env_path
        return env_path

    which_path = shutil.which("tesseract")
    if which_path:
        pytesseract.pytesseract.tesseract_cmd = which_path
        return which_path

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            pytesseract.pytesseract.tesseract_cmd = c
            return c

    return None


def ocr_image(img: Image.Image, lang: str) -> str:
    if pytesseract is None:
        raise RuntimeError("pytesseract nÃ£o estÃ¡ disponÃ­vel no ambiente.")
    config = "--oem 3 --psm 6"
    return pytesseract.image_to_string(img, lang=lang, config=config)


def extract_pdf_text(pdf_path: Path, ocr_lang: str, ocr_dpi: int, min_chars: int) -> Dict[str, Any]:
    doc = fitz.open(pdf_path)
    pages: List[PageExtraction] = []
    warnings: List[str] = []

    tesseract_path = try_configure_tesseract()

    for i in range(doc.page_count):
        page = doc.load_page(i)

        # 1) extraÃ§Ã£o direta
        direct = clean_text(page.get_text("text"))
        if len(direct) >= min_chars:
            pages.append(PageExtraction(page=i + 1, method="direct", text_chars=len(direct), text=direct))
            continue

        # 2) OCR (PDF escaneado)
        if tesseract_path is None:
            note = "Tesseract nÃ£o encontrado (instale e/ou coloque no PATH, ou defina TESSERACT_EXE)."
            warnings.append(f"{pdf_path.name}: pÃ¡gina {i+1} precisa OCR, mas tesseract nÃ£o estÃ¡ disponÃ­vel.")
            pages.append(PageExtraction(page=i + 1, method="ocr_unavailable", text_chars=0, text="", note=note))
            continue

        try:
            img = render_page_to_pil(page, dpi=ocr_dpi)
            img = preprocess_for_ocr(img)
            ocr_txt = clean_text(ocr_image(img, lang=ocr_lang))
            pages.append(PageExtraction(page=i + 1, method="ocr", text_chars=len(ocr_txt), text=ocr_txt))
        except Exception as e:
            warnings.append(f"{pdf_path.name}: OCR falhou na pÃ¡gina {i+1} -> {e}")
            pages.append(PageExtraction(page=i + 1, method="ocr_error", text_chars=0, text="", note=str(e)))

    doc.close()

    payload: Dict[str, Any] = {
        "file": pdf_path.name,
        "tesseract": tesseract_path or "",
        "pages": [asdict(p) for p in pages],
        "warnings": warnings,
    }
    return payload


def save_outputs(out_dir: Path, pdf_path: Path, payload: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{pdf_path.stem}_extracted.txt"
    out_json = out_dir / f"{pdf_path.stem}_extracted.json"

    parts: List[str] = []
    parts.append(f"FILE: {pdf_path.name}\n")
    if payload.get("tesseract"):
        parts.append(f"TESSERACT: {payload['tesseract']}\n")
    else:
        parts.append("TESSERACT: (nÃ£o detectado)\n")
    parts.append("==== EXTRACTED TEXT ====\n")

    for p in payload["pages"]:
        parts.append(f"\n--- PAGE {p['page']:03d} | method={p['method']} | chars={p['text_chars']} ---\n")
        if p.get("note"):
            parts.append(f"NOTE: {p['note']}\n")
        parts.append(p.get("text", ""))
        parts.append("\n")

    if payload.get("warnings"):
        parts.append("\n==== WARNINGS ====\n")
        for w in payload["warnings"]:
            parts.append(f"- {w}\n")

    out_txt.write_text("".join(parts), encoding="utf-8")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_stage_01_extraction(
    in_dir: Path,
    out_dir: Path,
    ocr_lang: str = "eng+por",
    ocr_dpi: int = 300,
    min_chars: int = 80,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Execute Stage 01: PDF text extraction with OCR fallback
    
    Args:
        in_dir: Directory containing PDF files
        out_dir: Output directory for extracted text
        ocr_lang: OCR language codes (e.g. "eng+por")
        ocr_dpi: DPI for OCR rendering
        min_chars: Minimum characters for direct text extraction
        verbose: Print progress messages
        
    Returns:
        Dictionary with processing results and statistics
    """
    if not in_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {in_dir}")
    
    pdfs = sorted(in_dir.glob("*.pdf"))
    if not pdfs:
        return {
            "processed_count": 0,
            "warnings": [f"No PDF files found in: {in_dir}"],
            "files": []
        }
    
    if verbose:
        print(f"OCR: lang={ocr_lang} | dpi={ocr_dpi} | min_chars={min_chars}")
        print(f"IN : {in_dir}")
        print(f"OUT: {out_dir}")
    
    results = []
    all_warnings = []
    
    for pdf in pdfs:
        if verbose:
            print(f"\nProcessing: {pdf.name}")
        
        payload = extract_pdf_text(pdf, ocr_lang=ocr_lang, ocr_dpi=ocr_dpi, min_chars=min_chars)
        save_outputs(out_dir, pdf, payload)
        
        direct_pages = sum(1 for p in payload["pages"] if p["method"] == "direct")
        ocr_pages = sum(1 for p in payload["pages"] if p["method"] == "ocr")
        ocr_missing = sum(1 for p in payload["pages"] if p["method"] == "ocr_unavailable")
        ocr_error = sum(1 for p in payload["pages"] if p["method"] == "ocr_error")
        
        file_result = {
            "file": pdf.name,
            "output_txt": str(out_dir / f"{pdf.stem}_extracted.txt"),
            "output_json": str(out_dir / f"{pdf.stem}_extracted.json"),
            "direct_pages": direct_pages,
            "ocr_pages": ocr_pages,
            "ocr_unavailable": ocr_missing,
            "ocr_error": ocr_error
        }
        results.append(file_result)
        all_warnings.extend(payload.get("warnings", []))
        
        if verbose:
            print(f"OK -> {pdf.stem}_extracted.txt/.json | direct={direct_pages} | ocr={ocr_pages} | ocr_unavailable={ocr_missing} | ocr_error={ocr_error}")
    
    if verbose:
        print("\nCompleted.")
    
    return {
        "processed_count": len(results),
        "warnings": all_warnings,
        "files": results
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 01 - Extract text from PDFs (direct + OCR fallback).")
    parser.add_argument("--in", dest="in_dir", required=True, help="Input directory with PDFs")
    parser.add_argument("--out", dest="out_dir", required=True, help="Output directory (txt/json)")
    parser.add_argument("--lang", default="eng+por", help="OCR languages (e.g. eng, por, eng+por)")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for OCR rendering")
    parser.add_argument("--min-chars", type=int, default=80, help="Minimum chars for direct text extraction")
    args = parser.parse_args()
    
    result = run_stage_01_extraction(
        in_dir=Path(args.in_dir),
        out_dir=Path(args.out_dir),
        ocr_lang=args.lang,
        ocr_dpi=args.dpi,
        min_chars=args.min_chars
    )
    
    if result["warnings"]:
        print(f"\n⚠ {len(result['warnings'])} warnings")


if __name__ == "__main__":
    main()
