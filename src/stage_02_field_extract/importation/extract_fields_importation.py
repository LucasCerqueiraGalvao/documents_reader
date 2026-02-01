# -*- coding: utf-8 -*-
"""
Stage 02 - IMPORTATION - Extract fields from Stage 01 text

Input : data/output/stage_01_text/importation/*_extracted.json
Output: data/output/stage_02_fields/importation/*_fields.json + _stage02_summary.json

Obs: roda como script (não como pacote), então imports devem ser locais (mesma pasta).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# imports locais (arquivos na mesma pasta)
from invoice import extract_invoice_fields
from packing_list import extract_packing_list_fields
from bl import extract_bl_fields


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_json(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: Path, obj: dict) -> None:
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def join_pages(stage01_obj: dict) -> str:
    pages = stage01_obj.get("pages") or []
    parts: List[str] = []
    for pg in pages:
        t = (pg.get("text") or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts).strip()


def detect_kind(original_file: str, full_text: str) -> str:
    name = (original_file or "").upper()

    if "PACKING" in name or "PACKING LIST" in name or re_any(name, [" PL", " P.L", "ROMANEIO"]):
        return "packing_list"
    if "INVOICE" in name and "PACKING" not in name:
        return "invoice"
    if name.startswith("BL") or "BILL OF LADING" in name or "B/L" in name:
        return "bl"

    # fallback por conteúdo
    up = (full_text or "").upper()
    if "PACKING LIST" in up:
        return "packing_list"
    if "INVOICE" in up:
        return "invoice"
    if "BILL OF LADING" in up or "B/L" in up:
        return "bl"

    return "unknown"


def re_any(s: str, needles: List[str]) -> bool:
    s = s.upper()
    return any(n.upper() in s for n in needles)


def unpack_extractor_result(res: Any) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Normaliza retorno dos extractors:
    - (fields, warnings)  -> missing=[]
    - (fields, missing, warnings)
    """
    if not isinstance(res, tuple):
        raise ValueError("Extractor deve retornar tuple")

    if len(res) == 2:
        fields, warnings = res
        return fields, [], (warnings or [])
    if len(res) == 3:
        fields, missing, warnings = res
        return fields, (missing or []), (warnings or [])
    raise ValueError(f"Extractor retornou {len(res)} itens (esperado 2 ou 3)")


def run_stage_02_extraction(
    in_dir: Path,
    out_dir: Path,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Execute Stage 02: Extract structured fields from text
    
    Args:
        in_dir: Directory with Stage 01 *_extracted.json files
        out_dir: Output directory for field extraction results
        verbose: Print progress messages
        
    Returns:
        Dictionary with processing results and warnings
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*_extracted.json"))
    if not files:
        return {
            "processed_count": 0,
            "warnings": [f"No *_extracted.json files found in: {in_dir}"],
            "documents": []
        }

    summary_docs: List[dict] = []
    all_warnings: List[str] = []

    for p in files:
        obj = read_json(p)
        original_file = obj.get("file") or p.name.replace("_extracted.json", ".pdf")
        full_text = join_pages(obj)

        doc_kind = detect_kind(original_file, full_text)

        if doc_kind == "invoice":
            res = extract_invoice_fields(full_text)
        elif doc_kind == "packing_list":
            res = extract_packing_list_fields(full_text)
        elif doc_kind == "bl":
            res = extract_bl_fields(full_text)
        else:
            res = ({}, [f"doc_kind unknown: {doc_kind}"], [])

        fields, missing_required_fields, warnings = unpack_extractor_result(res)

        out_obj = {
            "source": {
                "stage01_file": p.name,
                "original_file": original_file,
                "doc_kind": doc_kind,
            },
            "generated_at": now_iso(),
            "fields": fields,
            "missing_required_fields": missing_required_fields,
            "warnings": warnings,
        }

        out_name = p.name.replace("_extracted.json", "_fields.json").replace("__", "_")
        out_path = out_dir / out_name
        write_json(out_path, out_obj)

        summary_docs.append({
            "doc_kind": doc_kind,
            "original_file": original_file,
            "stage01_file": p.name,
            "stage02_file": out_name,
            "missing_required_fields": missing_required_fields,
            "warnings": warnings,
        })
        
        all_warnings.extend(warnings)

        if verbose:
            print(f"OK -> {out_name} | kind={doc_kind} | missing={len(missing_required_fields)} | warnings={len(warnings)}")

    summary = {
        "generated_at": now_iso(),
        "flow": "importation",
        "input_folder": str(in_dir),
        "output_folder": str(out_dir),
        "documents": summary_docs,
    }
    write_json(out_dir / "_stage02_summary.json", summary)
    
    if verbose:
        print("Completed.")
    
    return {
        "processed_count": len(summary_docs),
        "warnings": all_warnings,
        "documents": summary_docs
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", required=True, help="Stage 01 text folder")
    ap.add_argument("--out", dest="out_dir", required=True, help="Stage 02 fields output folder")
    args = ap.parse_args()

    run_stage_02_extraction(
        in_dir=Path(args.in_dir),
        out_dir=Path(args.out_dir)
    )


if __name__ == "__main__":
    main()
