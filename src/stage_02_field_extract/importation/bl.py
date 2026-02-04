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

def _digits(s: str) -> str:
    """Mantém só dígitos (útil p/ CNPJ)."""
    return re.sub(r"\D+", "", str(s or ""))

def _clean_company_name(raw: str) -> str:
    """Normaliza nomes de empresa extraídos com ruído.

    Exemplos de ruído visto no BL:
      - texto extra de cláusulas: "... LTDA. Received by the Carrier ..."
      - OCR quebrando palavra: "VEICU LOS" -> "VEICULOS"
    """
    s = _clean_spaces(raw)

    # corrigir OCR comum
    s = re.sub(r"\bVEICU\s+LOS\b", "VEICULOS", s, flags=re.I)

    # cortar em palavras que "invadem" a linha do nome
    upper = s.upper()
    for kw in [" RECEIVED BY", " RECEIVED", " PARTY TO CONTACT", " TEL", " PHONE", " PH:"]:
        idx = upper.find(kw)
        if idx > 0:
            s = s[:idx].strip()
            upper = s.upper()

    # cortar no sufixo societário, se existir
    m = re.search(
        r"\b(LTDA\.?|LTD\.?|S\.?A\.?|S/A|SA|INC\.?|LLC|CORPORATION|CORP\.?)\b",
        s,
        flags=re.I,
    )
    if m:
        s = s[: m.end()].strip()

    s = re.sub(r"[\s,;\-]+$", "", s).strip()
    return s

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
    """Extrai Consignee (nome + CNPJ) de forma robusta.

    Problema real:
      - a linha do nome pode vir com texto extra ("Received by the Carrier...")
      - existem múltiplos CNPJ (Consignee e Notify)
    Solução:
      1) localizar o bloco do CONSIGNEE
      2) dentro das próximas N linhas, achar o primeiro CNPJ
      3) pegar a melhor linha de nome imediatamente antes do CNPJ
      4) limpar o nome
    """
    evidence: List[str] = []

    # acha o início do bloco
    consignee_idx = None
    for i, ln in enumerate(lines):
        if re.search(r"\bCONSIGNEE\b", ln, flags=re.I):
            consignee_idx = i
            break

    def _find_cnpj_in_window(start: int, window: int = 20) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        for k in range(start + 1, min(len(lines), start + 1 + window)):
            ln = lines[k] or ""
            m = re.search(r"CNPJ\s*[:#]?\s*([0-9][0-9\.\-/]{11,})", ln, flags=re.I)
            if m:
                return k, (ln.strip() or None), _digits(m.group(1))
        return None, None, None

    name = None
    cnpj = None
    cnpj_line = None

    if consignee_idx is not None:
        cnpj_idx, cnpj_line, cnpj = _find_cnpj_in_window(consignee_idx, window=25)

        if cnpj_idx is not None:
            # melhor candidato é a linha imediatamente anterior ao CNPJ (voltando até achar uma linha boa)
            for j in range(cnpj_idx - 1, max(consignee_idx, cnpj_idx - 10), -1):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"\b(CON...|NOTIFY|CNPJ|TEL|PHONE)\b", cand, flags=re.I):
                    continue
                name = _clean_company_name(cand)
                evidence.append(cand)
                break

            if cnpj_line:
                evidence.append(cnpj_line)

    # fallback (se não achou pelo bloco): pega o primeiro CNPJ do documento
    if not cnpj:
        for ln in lines:
            m = re.search(r"CNPJ\s*[:#]?\s*([0-9][0-9\.\-/]{11,})", ln, flags=re.I)
            if m:
                cnpj = _digits(m.group(1))
                evidence.append((ln or "").strip())
                break

    return (name or None), (cnpj or None), evidence

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
    kg_re = re.compile(r"([0-9][0-9\.,]+)\s*K\s*[G6S](?:S|M)?\b", flags=re.I)
    m3_re = re.compile(r"([0-9][0-9\.,]+)\s*[A-Z0-9]{0,3}\s*[0-9][0-9\.,]*\s*(?:M3|CBM)\b", flags=re.I)

    for i, ln in enumerate(lines):
        if re.search(r"\bGROSS\s+WEIGHT\b", ln, flags=re.I):
            # procura nas pr?ximas linhas um n?mero + KG
            for j in range(i, min(i + 8, len(lines))):
                cand = lines[j]
                m = kg_re.search(cand)
                if m:
                    num = _parse_number(m.group(1))
                    if num is not None:
                        return num, [cand]
                # fallback: linha de tabela com M3/CBM (KG pode ter sumido no OCR)
                m2 = m3_re.search(cand)
                if m2:
                    num = _parse_number(m2.group(1))
                    if num is not None:
                        return num, [cand]
            break

    # fallback: qualquer linha com "KG" e formato do BL
    for ln in lines:
        m = kg_re.search(ln)
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
