# common.py
import json
import re
from pathlib import Path
from typing import Optional, Tuple

INCOTERMS = [
    "EXW","FCA","FAS","FOB","CFR","CIF","CPT","CIP","DAP","DPU","DDP","DAT"
]

def load_stage01_extracted_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def join_pages_text(stage01_data: dict) -> str:
    pages = stage01_data.get("pages") or []
    texts = []
    for p in pages:
        t = p.get("text") or ""
        texts.append(t)
    return "\n\n".join(texts)

def detect_doc_kind_from_filename(name: str) -> str:
    n = name.lower()
    if "invoice" in n:
        return "invoice"
    if "packing" in n:
        return "packing_list"
    if n.startswith("bl_") or "bill" in n or "lading" in n or "bl" in n:
        # seu arquivo é BL_extracted.json
        if n.startswith("bl_"):
            return "bl"
        if "bl_" in n:
            return "bl"
    if n.startswith("bl"):
        return "bl"
    return "unknown"

def normalize_spaces(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s

def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def parse_mixed_number(s: str) -> Optional[float]:
    """
    Converte:
      9,825.000 -> 9825.0
      5.009,00  -> 5009.0
      7,980.00  -> 7980.0
    """
    if not s:
        return None
    s = s.strip()
    s = re.sub(r"\s+", "", s)

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma and not has_dot:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return None

def build_field(present: bool, required: bool, value, evidence: list[str], method: str) -> dict:
    return {
        "present": bool(present),
        "required": bool(required),
        "value": value,
        "evidence": evidence or [],
        "method": method,
    }

def find_first(regex: re.Pattern, text: str, group: int = 1) -> Tuple[Optional[str], Optional[str]]:
    m = regex.search(text or "")
    if not m:
        return None, None
    return (m.group(group).strip() if m.group(group) else None, m.group(0))

def find_all(regex: re.Pattern, text: str, group: int = 1) -> list[str]:
    out = []
    for m in regex.finditer(text or ""):
        g = m.group(group)
        if g:
            out.append(g.strip())
    return out

def find_company_line_before_cnpj(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Heurística simples e efetiva para seus docs:
    pega a linha imediatamente anterior ao 'CNPJ: ...'
    """
    lines = [normalize_spaces(x).strip() for x in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if re.search(r"\bCNPJ\b", line, re.IGNORECASE):
            # sobe até achar uma linha não vazia
            j = i - 1
            while j >= 0 and not lines[j]:
                j -= 1
            if j >= 0:
                return lines[j], lines[j]
    return None, None

def find_cnpj(text: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.search(r"(?i)\bCNPJ\b\s*[:\-]?\s*([0-9\.\-\/]{11,20})", text or "")
    if not m:
        return None, None
    raw = m.group(1)
    return digits_only(raw), m.group(0)

def find_incoterm(text: str) -> Tuple[Optional[str], Optional[str]]:
    # aceita F.C.A. / F C A / FCA etc
    # primeiro tenta achar versões com pontos
    m = re.search(r"(?is)\bF\.?\s*C\.?\s*A\.?\b", text or "")
    if m:
        return "FCA", m.group(0)

    # depois termos diretos
    m2 = re.search(r"(?is)\b(" + "|".join(INCOTERMS) + r")\b", text or "")
    if not m2:
        return None, None
    return m2.group(1).upper(), m2.group(0)
