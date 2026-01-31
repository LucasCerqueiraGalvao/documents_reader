# -*- coding: utf-8 -*-
"""
Stage 02 - IMPORTATION - BL extractor (Bill of Lading)

Objetivos deste patch:
- Tirar "ruído" do Consignee/Shipper (remover parágrafo 'Received by the Carrier...' e cortar bloco)
- Consertar OCR de palavra quebrada tipo "VEICU LOS" -> "VEICULOS"
- Melhorar extração de gross weight: pegar valores do tipo "9,825.000 KG"
- Manter NCM/HS e warning quando vier com 4 dígitos (HS) e não NCM 8 dígitos
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Helpers básicos
# -----------------------------

def _field(present: bool, required: bool, value: Any, evidence: List[str], method: str) -> Dict[str, Any]:
    return {
        "present": bool(present),
        "required": bool(required),
        "value": value if present else None,
        "evidence": evidence or [],
        "method": method,
    }


def _clean_spaces(s: str) -> str:
    s = s.replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    s = re.sub(r"\n\s+", "\n", s)
    return s.strip()


def _parse_number(v: str) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None

    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None

    # resolve separador decimal
    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    if last_comma != -1 and last_dot != -1:
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif last_comma != -1:
        parts = s.split(",")
        if len(parts) > 2:
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(s)
    except ValueError:
        return None


def _find_block(text: str, start_word: str, stop_words: List[str], max_chars: int = 600) -> Optional[str]:
    """
    Captura um bloco logo após start_word até o primeiro stop_word.
    Limita tamanho para evitar vir com parágrafo gigante.
    """
    t = text
    # procura start
    m = re.search(rf"\b{re.escape(start_word)}\b", t, flags=re.IGNORECASE)
    if not m:
        return None

    start = m.end()
    tail = t[start:start + (max_chars * 4)]  # buffer maior pra achar stop
    # acha stop
    stop_pos = None
    for sw in stop_words:
        ms = re.search(rf"\b{re.escape(sw)}\b", tail, flags=re.IGNORECASE)
        if ms:
            pos = ms.start()
            if stop_pos is None or pos < stop_pos:
                stop_pos = pos

    block = tail[:stop_pos] if stop_pos is not None else tail[:max_chars]
    block = block[:max_chars]
    return _clean_spaces(block)


def _remove_boilerplate(s: str) -> str:
    """
    Remove o texto padrão do BL que vem colado no consignee/shipper em OCR:
    "Received by the Carrier from the Shipper ..."
    """
    if not s:
        return s
    u = s.upper()
    cut_markers = [
        "RECEIVED BY THE CARRIER",
        "RECEIVED BY",
        "IN APPARENT GOOD ORDER",
        "CONDITIONS UNLESS OTHERWISE",
        "CARRIAGE OF GOODS",
    ]
    cut_idx = None
    for mk in cut_markers:
        i = u.find(mk)
        if i != -1:
            cut_idx = i if cut_idx is None else min(cut_idx, i)
    if cut_idx is not None:
        s = s[:cut_idx]
    return _clean_spaces(s)


def _join_split_words_caps(s: str) -> str:
    """
    Heurística pra OCR que quebra palavra no meio:
    "VEICU LOS" -> "VEICULOS"
    Só junta quando:
      - token1 e token2 são alpha
      - token1 2..6 chars e token2 1..3 chars
      - token2 não é sufixo comum (LTDA, SA, CO etc)
    """
    if not s:
        return s
    toks = s.split()
    out = []
    i = 0
    block = {"LTDA", "LTD", "INC", "SA", "CO", "CO.", "S/A"}
    while i < len(toks):
        t1 = toks[i]
        if i + 1 < len(toks):
            t2 = toks[i + 1]
            if t1.isalpha() and t2.isalpha() and 2 <= len(t1) <= 6 and 1 <= len(t2) <= 3 and t2.upper() not in block:
                out.append(t1 + t2)
                i += 2
                continue
        out.append(t1)
        i += 1
    return " ".join(out)


def _pick_company_name(block: str) -> Optional[str]:
    """
    Pega a primeira "linha/núcleo" com cara de razão social.
    """
    if not block:
        return None

    b = _remove_boilerplate(block)

    # corta em CNPJ / endereço (pra não puxar rua etc)
    b = re.split(r"\bCNPJ\b", b, flags=re.IGNORECASE)[0]
    b = re.split(r"\bAV\b|\bRUA\b|\bROAD\b|\bSTREET\b", b, flags=re.IGNORECASE)[0]
    b = _clean_spaces(b)

    # primeira linha “forte”
    lines = [ln.strip(" -:\t") for ln in b.split("\n") if ln.strip()]
    if not lines:
        lines = [b] if b else []

    cand = lines[0] if lines else ""
    cand = _join_split_words_caps(cand.upper())
    cand = cand.strip(" -:\t").strip()
    return cand or None


def _extract_cnpj(text: str) -> Optional[str]:
    # aceita com pontos/barras/hífens
    m = re.search(r"\bCNPJ\b\s*[:\-]?\s*([0-9]{2}\.?[0-9]{3}\.?[0-9]{3}/?[0-9]{4}\-?[0-9]{2})", text, flags=re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1)
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 14 else digits  # deixa passar (às vezes OCR falha), mas ainda ajuda


def _extract_gross_weight_kg(text: str) -> Optional[float]:
    """
    BL costuma ter:
      - "Gross Weight in kilo's" e depois em outra linha "9,825.000 KG"
    Estratégia: pegar TODAS ocorrências de "<numero> KG" e escolher a maior plausível.
    """
    matches = re.findall(r"\b(\d[\d.,]{1,})\s*KG\b", text, flags=re.IGNORECASE)
    vals: List[float] = []
    for m in matches:
        v = _parse_number(m)
        if v is None:
            continue
        if 1.0 <= v <= 50_000_000:  # limite bem alto só pra filtrar lixo
            vals.append(v)
    if not vals:
        return None
    return max(vals)


def _extract_total_packages(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,6})\s*(CARTONS|CTNS|PACKAGES|PKGS)\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_freight_payment(text: str) -> Optional[str]:
    m = re.search(r"\bFREIGHT\s+(COLLECT|PREPAID)\b", text, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # alguns BLs vêm só "COLLECT" / "PREPAID" perto do freight
    m2 = re.search(r"\b(FREIGHT\s+)?(COLLECT|PREPAID)\b", text, flags=re.IGNORECASE)
    if m2:
        return m2.group(2).upper()
    return None


def _extract_ncm_or_hs(text: str) -> Optional[str]:
    # NCM NO.8407 / HS CODE 8407 etc
    patterns = [
        r"\bNCM\s*(?:NO\.?|Nº|NUMBER|:)?\s*([0-9]{4,10})\b",
        r"\bHS\s*(?:CODE|NO\.?|Nº|NUMBER|:)?\s*([0-9]{4,10})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# -----------------------------
# API do extractor
# -----------------------------

def extract_bl_fields(full_text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Retorna: (fields_dict, missing_required_fields, warnings)
    """
    text = _clean_spaces(full_text or "")
    warnings: List[str] = []
    missing: List[str] = []
    fields: Dict[str, Any] = {}

    # blocos
    shipper_block = _find_block(text, "SHIPPER", stop_words=["CONSIGNEE", "BOOKING", "B/L", "BILL OF LADING"], max_chars=500)
    consignee_block = _find_block(text, "CONSIGNEE", stop_words=["NOTIFY", "PARTY TO CONTACT", "VESSEL", "VOY", "PORT OF LOADING"], max_chars=650)

    shipper_name = _pick_company_name(shipper_block or "")
    consignee_name = _pick_company_name(consignee_block or "")

    # CNPJ (idealmente do Consignee, mas OCR às vezes coloca em outro lugar)
    consignee_cnpj = _extract_cnpj(consignee_block or "") or _extract_cnpj(text)

    # pesos / pacotes / freight / ncm
    gross = _extract_gross_weight_kg(text)
    total_pkgs = _extract_total_packages(text)
    freight = _extract_freight_payment(text)
    ncmhs = _extract_ncm_or_hs(text)

    # warning HS vs NCM
    if ncmhs and len(ncmhs) in (4, 6):
        warnings.append(f"NCM/HS encontrado com {len(ncmhs)} dígitos ({ncmhs}). Pode ser HS (4/6) e não NCM completo (8).")

    # fields (regras da Karina: shipper/exportador obrigatório; consignee CNPJ obrigatório; NCM obrigatório no BL)
    fields["shipper_name"] = _field(bool(shipper_name), True, shipper_name, [shipper_block[:160]] if shipper_block else [], "block(shipper)")
    fields["consignee_name"] = _field(bool(consignee_name), True, consignee_name, [consignee_block[:180]] if consignee_block else [], "block(consignee)")
    fields["consignee_cnpj"] = _field(bool(consignee_cnpj), True, consignee_cnpj, [consignee_block[:220]] if consignee_block else [], "regex(cnpj)")

    # alias (pra facilitar o Stage 3, se você trata consignee/importer como equivalente)
    fields["importer_name"] = _field(bool(consignee_name), True, consignee_name, [consignee_block[:180]] if consignee_block else [], "alias(consignee_name)")
    fields["importer_cnpj"] = _field(bool(consignee_cnpj), True, consignee_cnpj, [consignee_block[:220]] if consignee_block else [], "alias(consignee_cnpj)")

    fields["freight_payment"] = _field(bool(freight), True, freight, [freight] if freight else [], "regex(freight)")
    fields["gross_weight_kg"] = _field(gross is not None, True, gross, ["... " + m + " KG ..."] if (m := (re.search(r"\b(\d[\d.,]{1,})\s*KG\b", text, re.I).group(1) if re.search(r"\b(\d[\d.,]{1,})\s*KG\b", text, re.I) else None)) else [], "regex(number+KG,max)")
    fields["total_packages"] = _field(total_pkgs is not None, True, total_pkgs, [str(total_pkgs)] if total_pkgs is not None else [], "regex(packages)")
    fields["ncm_or_hs_code"] = _field(bool(ncmhs), True, ncmhs, [f"NCM/HS {ncmhs}"] if ncmhs else [], "regex(ncm/hs)")

    # missing required
    for k, obj in fields.items():
        if obj.get("required") and not obj.get("present"):
            missing.append(k)

    return fields, missing, warnings
