# common.py
import json
import re
from pathlib import Path
from typing import Optional, Tuple

INCOTERMS = [
    "EXW",
    "FCA",
    "FAS",
    "FOB",
    "CFR",
    "CIF",
    "CPT",
    "CIP",
    "DAP",
    "DPU",
    "DDP",
    "DAT",
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
    if "hbl" in n:
        return "hbl"
    if "conferencia di" in n or "rascunho di" in n or re.search(r"\bdi\b", n):
        return "di"
    if (
        "conferencia li" in n
        or "rascunho li" in n
        or "licenca" in n
        or re.search(r"\bli\b", n)
    ):
        return "li"
    if "invoice" in n:
        return "invoice"
    if "packing" in n:
        return "packing_list"
    if n.startswith("bl_") or "bill" in n or "lading" in n or "bl" in n:
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


def parse_number_locale(s: str, prefer_thousands_sep: str = ",") -> Optional[float]:
    """
    Robust parser for mixed locale numbers.

    Rules:
    - "7,980" -> 7980
    - "9,825.000" -> 9825.0
    - "5.009,00" -> 5009.0
    - "53.772" -> 53.772
    - "1.234.567" -> 1234567
    """
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None

    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {"-", ",", "."}:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        # decimal separator is the right-most separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    if has_comma and not has_dot:
        # comma as thousands in patterns like 7,980
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None

    if has_dot and not has_comma:
        # multiple dots can be thousands separators
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            if s.count(".") > 1:
                s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None

    try:
        return float(s)
    except ValueError:
        return None


def parse_mixed_number(s: str) -> Optional[float]:
    """
    Converts:
      9,825.000 -> 9825.0
      5.009,00  -> 5009.0
      7,980.00  -> 7980.0
    """
    return parse_number_locale(s)


def truncate_evidence(evidence: list[str], max_chars: int = 220) -> list[str]:
    out: list[str] = []
    for ev in evidence or []:
        if ev is None:
            continue
        s = str(ev).strip()
        if not s:
            continue
        s = normalize_spaces(s)
        if len(s) > max_chars:
            s = s[: max_chars - 3].rstrip() + "..."
        out.append(s)
    return out


def build_field(
    present: bool, required: bool, value, evidence: list[str], method: str
) -> dict:
    return {
        "present": bool(present),
        "required": bool(required),
        "value": value,
        "evidence": truncate_evidence(evidence or []),
        "method": method,
    }


def find_first(
    regex: re.Pattern, text: str, group: int = 1
) -> Tuple[Optional[str], Optional[str]]:
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
    Simple heuristic:
    pick the first non-empty line right before a CNPJ line.
    """
    lines = [normalize_spaces(x).strip() for x in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if re.search(r"\bCNPJ\b", line, re.IGNORECASE):
            j = i - 1
            while j >= 0 and not lines[j]:
                j -= 1
            if j >= 0:
                return lines[j], lines[j]
    return None, None


def find_cnpj(text: str) -> Tuple[Optional[str], Optional[str]]:
    text = text or ""
    cnpj_like = re.compile(r"(\d{2}[.\s]?\d{3}[.\s]?\d{3}[\/\s]?\d{4}[-\s]?\d{2})")

    # Labeled ids (CNPJ / P.IVA / Partita IVA / Cod. Fiscale P.IVA)
    labeled = re.finditer(
        r"(?is)\b(?:CNPJ|P\.?\s*IVA|PARTITA\s+I\.?\s*V\.?\s*A\.?|COD\.?\s*FISCALE(?:\s*P\.?\s*IVA)?)\b\s*[:\-]?\s*([A-Z0-9\.\-\/\s]{8,30})",
        text,
    )
    for m in labeled:
        chunk = m.group(1) or ""
        hit = cnpj_like.search(chunk) or cnpj_like.search(m.group(0))
        if not hit:
            continue
        digits = digits_only(hit.group(1))
        if len(digits) == 14:
            return digits, m.group(0)

    # Unlabeled CNPJ pattern anywhere in text
    any_cnpj = cnpj_like.search(text)
    if any_cnpj:
        digits = digits_only(any_cnpj.group(1))
        if len(digits) == 14:
            return digits, any_cnpj.group(0)

    return None, None


def find_incoterm(text: str) -> Tuple[Optional[str], Optional[str]]:
    text = text or ""

    # Long forms commonly present in EU invoices
    phrase_map = [
        (r"\bDELIVERED\s+AT\s+PLACE\s+UNLOADED\b", "DPU"),
        (r"\bDELIVERED\s+DUTY\s+PAID\b", "DDP"),
        (r"\bDELIVERED\s+AT\s+PLACE\b", "DAP"),
        (r"\bCARRIAGE\s+AND\s+INSURANCE\s+PAID\s+TO\b", "CIP"),
        (r"\bCARRIAGE\s+PAID\s+TO\b", "CPT"),
        (r"\bCOST\s+INSURANCE\s+AND\s+FREIGHT\b", "CIF"),
        (r"\bCOST\s+AND\s+FREIGHT\b", "CFR"),
        (r"\bFREE\s+ON\s+BOARD\b", "FOB"),
        (r"\bFREE\s+ALONGSIDE\s+SHIP\b", "FAS"),
        (r"\bFREE\s+CARRIER\b", "FCA"),
        (r"\bEX\s+WORKS\b", "EXW"),
    ]
    for pattern, code in phrase_map:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return code, m.group(0)

    # F.C.A. / F C A / FCA
    m = re.search(r"(?is)\bF\.?\s*C\.?\s*A\.?\b", text)
    if m:
        return "FCA", m.group(0)

    # Direct Incoterm token
    m2 = re.search(r"(?is)\b(" + "|".join(INCOTERMS) + r")\b", text)
    if not m2:
        return None, None
    return m2.group(1).upper(), m2.group(0)
