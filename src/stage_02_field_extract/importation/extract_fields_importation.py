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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# imports locais (arquivos na mesma pasta)
# - Quando roda como script: `python extract_fields_importation.py` (imports diretos)
# - Quando roda via pipeline (import como módulo): imports relativos
try:
    from .invoice import extract_invoice_fields
    from .packing_list import extract_packing_list_fields
    from .bl import extract_bl_fields
    from .hbl import extract_hbl_fields
    from .di import extract_di_fields
    from .li import extract_li_fields
except ImportError:  # pragma: no cover
    from invoice import extract_invoice_fields
    from packing_list import extract_packing_list_fields
    from bl import extract_bl_fields
    from hbl import extract_hbl_fields
    from di import extract_di_fields
    from li import extract_li_fields


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


def _match_any(text: str, patterns: list[str]) -> bool:
    for p in patterns:
        if re.search(p, text, flags=re.I):
            return True
    return False


def normalize_doc_kind_hint(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    aliases = {
        "invoice": "invoice",
        "commercial_invoice": "invoice",
        "packing_list": "packing_list",
        "packing list": "packing_list",
        "pl": "packing_list",
        "bl": "bl",
        "bill_of_lading": "bl",
        "hbl": "hbl",
        "di": "di",
        "li": "li",
    }
    return aliases.get(s)


def detect_kind(full_text: str) -> str:
    text = (full_text or "").upper()

    # Content-only fallback.
    # UI flows should provide doc_kind_hint and bypass this function.
    if _match_any(text, [r"\bHBL\b", r"HOUSE\s+BILL"]):
        return "hbl"
    if _match_any(text, [r"PACKING\s+LIST", r"\bROMANEIO\b"]):
        return "packing_list"
    if _match_any(
        text,
        [
            r"CONFERENCI[AA]\s+DI",
            r"RASCUNHO\s+DA\s+DI",
            r"RASCUNHO\s+DI",
            r"DECLARA[Ã‡C][AÃƒ]O\s+DE\s+IMPORTA",
            r"\bNR\.?\s*DI\b",
            r"\bN[UÚ]MERO\s+DA\s+DI\b",
        ],
    ):
        return "di"
    if _match_any(
        text,
        [
            r"CONFERENCI[AA]\s+LI",
            r"RASCUNHO\s+LI",
            r"LICEN[Ã‡C]A\s+DE\s+IMPORTA",
            r"\bNR\.?\s*LI\b",
            r"\bN[UÚ]MERO\s+DA\s+LI\b",
            r"\bNREFERENCIA\s+LI\b",
        ],
    ):
        return "li"
    if _match_any(text, [r"COMMERCIAL\s+INVOICE", r"INVOICE", r"PRO[-\s]?FORMA", r"FATTURA"]):
        return "invoice"
    if _match_any(text, [r"BILL\s+OF\s+LADING", r"\bB/L\b", r"\bBL\b"]):
        return "bl"

    return "unknown"

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
        missing = [
            k
            for k, meta in (fields or {}).items()
            if isinstance(meta, dict)
            and bool(meta.get("required"))
            and not bool(meta.get("present"))
        ]
        return fields, missing, (warnings or [])
    if len(res) == 3:
        fields, missing, warnings = res
        return fields, (missing or []), (warnings or [])
    raise ValueError(f"Extractor retornou {len(res)} itens (esperado 2 ou 3)")


def run_stage_02_extraction(
    in_dir: Path, out_dir: Path, verbose: bool = True
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*_extracted.json"))
    if not files:
        return {
            "processed_count": 0,
            "warnings": [f"No *_extracted.json files found in: {in_dir}"],
            "documents": [],
        }

    summary_docs: List[dict] = []
    all_warnings: List[str] = []

    for p in files:
        obj = read_json(p)
        original_file = obj.get("file") or p.name.replace("_extracted.json", ".pdf")
        full_text = join_pages(obj)
        doc_kind_hint = normalize_doc_kind_hint(obj.get("doc_kind_hint"))

        doc_kind = doc_kind_hint or detect_kind(full_text)

        if doc_kind == "invoice":
            res = extract_invoice_fields(full_text)
        elif doc_kind == "packing_list":
            res = extract_packing_list_fields(full_text)
        elif doc_kind == "bl":
            res = extract_bl_fields(full_text)
        elif doc_kind == "hbl":
            res = extract_hbl_fields(full_text)
        elif doc_kind == "di":
            res = extract_di_fields(full_text)
        elif doc_kind == "li":
            res = extract_li_fields(full_text)
        else:
            res = ({}, [f"doc_kind unknown: {doc_kind}"], [])

        fields, missing_required_fields, warnings = unpack_extractor_result(res)

        out_obj = {
            "source": {
                "stage01_file": p.name,
                "original_file": original_file,
                "doc_kind": doc_kind,
                "doc_kind_hint": doc_kind_hint or "",
            },
            "generated_at": now_iso(),
            "fields": fields,
            "missing_required_fields": missing_required_fields,
            "warnings": warnings,
        }

        out_name = p.name.replace("_extracted.json", "_fields.json").replace("__", "_")
        out_path = out_dir / out_name
        write_json(out_path, out_obj)

        summary_docs.append(
            {
                "doc_kind": doc_kind,
                "original_file": original_file,
                "stage01_file": p.name,
                "stage02_file": out_name,
                "missing_required_fields": missing_required_fields,
                "warnings": warnings,
            }
        )

        all_warnings.extend(warnings)

        if verbose:
            print(
                f"OK -> {out_name} | kind={doc_kind} | missing={len(missing_required_fields)} | warnings={len(warnings)}"
            )

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
        "documents": summary_docs,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--in", dest="in_dir", required=True, help="Pasta stage_01_text/importation"
    )
    ap.add_argument(
        "--out", dest="out_dir", required=True, help="Pasta stage_02_fields/importation"
    )
    args = ap.parse_args()

    run_stage_02_extraction(
        in_dir=Path(args.in_dir), out_dir=Path(args.out_dir), verbose=True
    )


if __name__ == "__main__":
    main()
