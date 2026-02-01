# -*- coding: utf-8 -*-
"""
Stage 02 - Importation - BL/HBL field extraction (stdlib only)

Return signature:
    fields_dict, missing_required_fields, warnings
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Helpers
# -----------------------------
def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, list) and len(v) == 0:
        return False
    return True


def _mk_field(value: Any, required: bool, evidence: List[str], method: str) -> Dict[str, Any]:
    return {
        "present": bool(_present(value)),
        "required": bool(required),
        "value": value if _present(value) else None,
        "evidence": evidence or [],
        "method": method,
    }


def _parse_number(v: str) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None

    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None

    # 7,980 (milhar com vírgula)
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
        s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    # 7,980.000 (milhar com vírgula + decimal com ponto)
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    # 7.980,00 (milhar com ponto + decimal com vírgula)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None

    # só vírgula
    if "," in s and "." not in s:
        left, right = s.split(",", 1)
        if len(right) == 3 and len(left) <= 3:
            s = left + right  # milhar
        else:
            s = left + "." + right
        try:
            return float(s)
        except ValueError:
            return None

    # só ponto
    if "." in s and "," not in s:
        left, right = s.split(".", 1)
        if len(right) == 3 and len(left) <= 3:
            s = left + right  # milhar
        try:
            return float(s)
        except ValueError:
            return None

    # ambos: decide último separador como decimal
    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    if last_dot > last_comma:
        s = s.replace(",", "")
    else:
        s = s.replace(".", "").replace(",", ".")

    try:
        return float(s)
    except ValueError:
        return None


def _lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _find_shipper(lines: List[str]) -> Tuple[Optional[str], List[str]]:
    """
    BL OCR geralmente vem como:
      "Shipper Booking No. B/L No."
      "SUZUKI MOTOR CORPORATION 258255821A"
      "300 TAKATSUKA-CHO ..."
    A ideia: achar "SHIPPER" e pegar a próxima linha "boa" como nome.
    """
    stop_words = {"CONSIGNEE", "NOTIFY", "BOOKING", "B/L", "B/L NO", "B/L NO."}

    for i, ln in enumerate(lines):
        if re.search(r"\bSHIPPER\b", ln, flags=re.I):
            # pega próximas linhas até achar uma que pareça nome
            for j in range(i + 1, min(i + 8, len(lines))):
                cand = lines[j].strip()
                if not cand:
                    continue
                if any(sw in cand.upper() for sw in stop_words):
                    continue

                # remove trailing token com muito dígito (ex: BL number colado no nome)
                parts = cand.split()
                while parts and re.search(r"\d", parts[-1]) and len(parts[-1]) >= 6:
                    parts.pop()
                name = _clean_spaces(" ".join(parts))
                if name and len(name) >= 3:
                    return name, [cand]

            break

    # fallback: às vezes vem "SHIPPER:" na mesma linha
    for ln in lines:
        m = re.search(r"\bSHIPPER\b\s*:?\s*(.+)$", ln, flags=re.I)
        if m:
            name = _clean_spaces(m.group(1))
            if name:
                return name, [ln]

    return None, []


def _find_consignee_name_cnpj(lines: List[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Muito comum aparecer:
      Consignee
      GHANDI ...
      CNPJ: 03....
    Vamos capturar o nome (linha após Consignee) e o CNPJ.
    """
    evidence: List[str] = []

    name = None
    for i, ln in enumerate(lines):
        if re.search(r"\bCONSIGNEE\b", ln, flags=re.I):
            # próxima linha com texto "empresa"
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = lines[j].strip()
                if not cand:
                    continue
                if re.search(r"\bCNPJ\b", cand, flags=re.I):
                    continue
                if re.search(r"\bNOTIFY\b", cand, flags=re.I):
                    break
                name = _clean_spaces(cand)
                evidence.append(cand)
                break
            break

    cnpj = None
    for ln in lines:
        m = re.search(r"\bCNPJ\s*:?\s*([0-9\.\-\/]+)", ln, flags=re.I)
        if m:
            raw = m.group(1)
            digits = re.sub(r"\D", "", raw)
            if digits:
                cnpj = digits
                evidence.append(ln)
                break

    return name, cnpj, evidence


def _find_ncm(lines: List[str]) -> Tuple[Optional[str], List[str], List[str]]:
    """
    Aceitar 4, 6 ou 8 dígitos (HS/NCM), sem warning para 4 dígitos.
    Gera warning apenas se achar algo com tamanho "estranho".
    """
    warnings: List[str] = []
    for ln in lines:
        m = re.search(r"\bNCM\b\s*(?:NO\.?|Nº)?\s*([0-9]{4,8})", ln, flags=re.I)
        if m:
            code = m.group(1)
            # 4 dígitos: OK (HS 4/6 ou NCM parcial)
            if len(code) not in (4, 6, 8):
                warnings.append(f"NCM/HS encontrado com {len(code)} dígitos ({code}). Verificar.")
            return code, [ln], warnings

    return None, [], warnings


def _find_gross_weight(lines: List[str]) -> Tuple[Optional[float], List[str]]:
    """
    Ex: "Gross Weight 'in kilo's" e logo depois "9,825.000 KG"
    """
    for i, ln in enumerate(lines):
        if re.search(r"\bGROSS\s+WEIGHT\b", ln, flags=re.I):
            # procura nas próximas linhas um número + KG
            for j in range(i, min(i + 8, len(lines))):
                cand = lines[j]
                m = re.search(r"([0-9][0-9\.,]+)\s*KG", cand, flags=re.I)
                if m:
                    num = _parse_number(m.group(1))
                    if num is not None:
                        return num, [cand]
            break

    # fallback: qualquer linha com "KG" e formato do BL
    for ln in lines:
        m = re.search(r"([0-9][0-9\.,]+)\s*KG", ln, flags=re.I)
        if m and re.search(r"\bWEIGHT\b", ln, flags=re.I):
            num = _parse_number(m.group(1))
            if num is not None:
                return num, [ln]

    return None, []


def extract_bl_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    ln = _lines(text)

    warnings: List[str] = []
    missing: List[str] = []

    shipper_name, shipper_ev = _find_shipper(ln)
    consignee_name, consignee_cnpj, consignee_ev = _find_consignee_name_cnpj(ln)
    ncm, ncm_ev, ncm_warn = _find_ncm(ln)
    gross_weight, gross_ev = _find_gross_weight(ln)

    warnings.extend(ncm_warn)

    # Required rules (Karina)
    # - shipper/exportador obrigatório
    # - consignee CNPJ obrigatório
    # - NCM obrigatório (pode ser 4/6/8)
    required = {
        "shipper_name": True,
        "importer_name": True,
        "importer_cnpj": True,
        "ncm": True,
        "gross_weight_kg": True,
    }

    fields: Dict[str, Any] = {}
    fields["shipper_name"] = _mk_field(shipper_name, required["shipper_name"], shipper_ev, "line_block")
    fields["importer_name"] = _mk_field(consignee_name, required["importer_name"], consignee_ev[:1], "line_block")
    fields["importer_cnpj"] = _mk_field(consignee_cnpj, required["importer_cnpj"], consignee_ev, "regex")
    fields["ncm"] = _mk_field(ncm, required["ncm"], ncm_ev, "regex")
    fields["gross_weight_kg"] = _mk_field(gross_weight, required["gross_weight_kg"], gross_ev, "regex")

    for k, meta in fields.items():
        if meta["required"] and not meta["present"]:
            missing.append(k)

    return fields, missing, warnings
