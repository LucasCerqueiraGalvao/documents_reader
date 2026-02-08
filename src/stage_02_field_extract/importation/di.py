# di.py
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

try:
    from .common import (
        build_field,
        find_cnpj,
        find_company_line_before_cnpj,
        find_first,
        find_all,
        parse_number_locale,
        truncate_evidence,
    )
except ImportError:  # pragma: no cover
    from common import (
        build_field,
        find_cnpj,
        find_company_line_before_cnpj,
        find_first,
        find_all,
        parse_number_locale,
        truncate_evidence,
    )

RE_INVOICE_NO = re.compile(
    r"(?is)\b(?:INVOICE|COMMERCIAL\s+INVOICE|FATURA(?:\s+COMERCIAL)?|FATTURA)\b"
    r"[^\w]{0,10}([A-Z0-9][A-Z0-9\-\/\.]{2,})"
)
RE_ANY_DOC_NO = re.compile(r"(?is)\b([A-Z]{1,4}-\d{3,10}(?:-[A-Z])?)\b")
RE_DI_NUMBER = re.compile(r"(?is)\bDI\b\s*(?:NO\.?|Nº|NUMERO)?\s*[:\-]?\s*([0-9\-\.\/]+)")
RE_NET_WEIGHT = re.compile(r"(?is)\bPESO\s+LIQUIDO\b\s*[:\-]?\s*([0-9\.,]+)")
RE_GROSS_WEIGHT = re.compile(r"(?is)\bPESO\s+BRUTO\b\s*[:\-]?\s*([0-9\.,]+)")
RE_FATURA_INLINE = re.compile(
    r"(?is)\bFATURA\s+COMERCIAL\b\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/\.]{2,})"
)
RE_DATE = re.compile(r"(?is)\b(\d{1,2}/\d{1,2}/\d{4})\b")
RE_NCM = re.compile(r"(?is)\bNCM\b\s*(?:NO\.?|Nº|NUMERO)?\s*[:\-]?\s*([0-9]{4,8})")


def _lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _strip_accents(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn"
    )


def _find_value_after_label_contains(
    lines: List[str], label_ascii: str, require_digit: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    label_ascii = (label_ascii or "").upper()
    for i, ln in enumerate(lines):
        if label_ascii in _strip_accents(ln).upper():
            if ":" in ln:
                after = ln.split(":", 1)[1].strip().lstrip(":").strip()
                if after and (not require_digit or re.search(r"\d", after)):
                    return after, ln
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = (lines[j] or "").strip().lstrip(":").strip()
                if not cand or cand in {":", "-", "--"}:
                    continue
                if require_digit and not re.search(r"\d", cand):
                    continue
                return cand, cand
    return None, None


def _section_slice(lines: List[str], section_ascii: str, max_lines: int = 40) -> List[str]:
    section_ascii = (section_ascii or "").upper()
    for i, ln in enumerate(lines):
        if section_ascii in _strip_accents(ln).upper():
            end = min(i + 1 + max_lines, len(lines))
            for j in range(i + 1, end):
                if "INFORMA" in _strip_accents(lines[j]).upper():
                    end = j
                    break
            return lines[i + 1 : end]
    return []


def _find_importer_name(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    # 1) tenta "IMPORTADOR" com linha seguinte
    for i, ln in enumerate(lines):
        if re.search(r"\bIMPORTADOR\b", ln, flags=re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"\b(CNPJ|CPF|DI|LI|PROCESSO)\b", cand, flags=re.I):
                    continue
                if re.search(r"\bIMPORTADOR\b", cand, flags=re.I):
                    continue
                if ("IMPORTADOR" in cand.upper()) and cand.upper().startswith("INFORMA"):
                    continue
                return cand, cand

    # 2) fallback pela linha antes do CNPJ
    joined = "\n".join(lines)
    return find_company_line_before_cnpj(joined)


def _find_invoice_numbers(text: str) -> Tuple[List[str], List[str]]:
    numbers: List[str] = []
    evidence: List[str] = []

    lines = _lines(text)

    importer_section = _section_slice(lines, "INFORMACOES - IMPORTADOR")
    if not importer_section:
        importer_section = lines

    def _normalize_candidate(s: str) -> Optional[str]:
        s = s.strip()
        if not s:
            return None
        # se tiver espa?os, tenta primeiro token
        if " " in s:
            token = s.split()[0]
            if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-\/]+", token) or token.isdigit():
                s = token
            else:
                return None
        if not re.search(r"\d", s):
            return None
        if s.isdigit() and len(s) < 4:
            return None
        if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-\/]+", s) or s.isdigit():
            return s
        return None

    # FATURA COMERCIAL (mesma linha ou linha seguinte)
    for i, ln in enumerate(lines):
        if re.search(r"FATURA\s+COMERCIAL", ln, flags=re.I):
            m = RE_FATURA_INLINE.search(ln)
            if m:
                cand = _normalize_candidate(m.group(1))
                if cand:
                    numbers.append(cand)
                    evidence.append(ln)
                continue
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"\bFATURA\b", cand, flags=re.I):
                    continue
                if cand.upper() in {"S/N", "SN"}:
                    continue
                cand_norm = _normalize_candidate(cand)
                if cand_norm:
                    numbers.append(cand_norm)
                    evidence.append(ln)
                    evidence.append(cand_norm)
                    break

    # INVOICE / COMMERCIAL INVOICE (mesma linha ou pr?xima)
    for i, ln in enumerate(lines):
        if re.search(r"(INVOICE|COMMERCIAL\s+INVOICE)", ln, flags=re.I):
            if re.search(r"ITEM\s+INVOICE", ln, flags=re.I):
                continue
            m = re.search(r"(INVOICE|COMMERCIAL\s+INVOICE)\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            cand = _normalize_candidate(m.group(2)) if m else None
            if cand:
                numbers.append(cand)
                evidence.append(ln)
                continue
            if not re.search(r"(INVOICE|COMMERCIAL\s+INVOICE)\s*(?:[:\-]|NO\.?|N?|N?)", ln, flags=re.I):
                continue
            for j in range(i + 1, min(i + 3, len(lines))):
                cand = (lines[j] or "").strip()
                cand_norm = _normalize_candidate(cand) if cand else None
                if cand_norm:
                    numbers.append(cand_norm)
                    evidence.append(ln)
                    evidence.append(cand_norm)
                    break

    # unique preserving order
    seen = set()
    uniq = []
    for n in numbers:
        if n not in seen:
            uniq.append(n)
            seen.add(n)

    return uniq, truncate_evidence(evidence[:4])


def _find_value_after_label(
    lines: List[str], label_re: str, require_digit: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    for i, ln in enumerate(lines):
        if re.search(label_re, ln, flags=re.I):
            m = re.search(label_re + r"\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m and m.group(1).strip():
                val = m.group(1).strip()
                if val in {":", "-", "--"} or not re.search(r"[A-Za-z0-9]", val):
                    pass
                elif require_digit and not re.search(r"\d", val):
                    pass
                else:
                    return val, ln
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand or cand in {":", "-", "--"}:
                    continue
                if require_digit and not re.search(r"\d", cand):
                    continue
                return cand, cand
    return None, None


def extract_di_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    warnings: List[str] = []
    missing: List[str] = []
    fields: Dict[str, Any] = {}

    lines = _lines(text)

    importer_section = _section_slice(lines, "INFORMACOES - IMPORTADOR")
    if not importer_section:
        importer_section = lines

    importer_name, ev_name = _find_importer_name(lines)
    cnpj, ev_cnpj = find_cnpj(text)

    inv_numbers, inv_ev = _find_invoice_numbers(text)
    inv_first = inv_numbers[0] if inv_numbers else None

    di_no, di_ev = find_first(RE_DI_NUMBER, text)
    nossa_ref, nossa_ev = _find_value_after_label(lines, r"NOSSA\s+REFER[ÊE]NCIA")
    sua_ref, sua_ev = _find_value_after_label(lines, r"SUA\s+REFER[ÊE]NCIA")
    bl_no, bl_ev = _find_value_after_label(lines, r"CONHECIMENTO", require_digit=True)
    via_transporte, via_ev = _find_value_after_label(lines, r"VIA\s+TRANSPORTE")
    local_embarque, loc_ev = _find_value_after_label(lines, r"LOCAL\s+DE\s+EMBARQUE")
    data_embarque, emb_ev = _find_value_after_label(lines, r"DATA\s+DE\s+EMBARQUE")
    data_chegada, cheg_ev = _find_value_after_label(lines, r"DATA\s+DE\s+CHEGADA")

    tipo_declaracao, tipo_ev = _find_value_after_label_contains(lines, "TIPO DE DECLARACAO")
    unidade_operacional, un_ev = _find_value_after_label_contains(lines, "UNIDADE OPERACIONAL")
    urf_despacho, urf_ev = _find_value_after_label_contains(lines, "URF DESPACHO", require_digit=True)
    modalidade_desp, mod_ev = _find_value_after_label_contains(lines, "MODALIDADE DESP")
    transportador, transp_ev = _find_value_after_label_contains(lines, "TRANSPORTADOR")
    urf_entrada, urf_ent_ev = _find_value_after_label_contains(lines, "URF DE ENTRADA", require_digit=True)
    pais_proced, pais_proc_ev = _find_value_after_label_contains(lines, "PAIS DE PROCED", require_digit=True)

    imp_endereco, imp_end_ev = _find_value_after_label_contains(importer_section, "ENDERECO IMPORTADOR")
    imp_numero, imp_num_ev = _find_value_after_label_contains(importer_section, "NUMERO", require_digit=True)
    imp_comp, imp_comp_ev = _find_value_after_label_contains(importer_section, "COMPLEMENTO")
    imp_bairro, imp_bairro_ev = _find_value_after_label_contains(importer_section, "BAIRRO")
    imp_cep, imp_cep_ev = _find_value_after_label_contains(importer_section, "CEP", require_digit=True)
    imp_cidade_uf, imp_cid_ev = _find_value_after_label_contains(importer_section, "CIDADE/UF")
    imp_pais, imp_pais_ev = _find_value_after_label_contains(importer_section, "PAIS")

    nw_raw, nw_ev = find_first(RE_NET_WEIGHT, text)
    gw_raw, gw_ev = find_first(RE_GROSS_WEIGHT, text)
    nw = parse_number_locale(nw_raw) if nw_raw else None
    gw = parse_number_locale(gw_raw) if gw_raw else None

    ncm, ncm_ev = find_first(RE_NCM, text)
    if ncm and len(ncm) not in (4, 6, 8):
        warnings.append(f"NCM/HS com {len(ncm)} digitos ({ncm}). Verificar.")

    fields["importer_name"] = build_field(bool(importer_name), True, importer_name, [ev_name] if ev_name else [], "heuristic")
    fields["importer_cnpj"] = build_field(bool(cnpj), True, cnpj, [ev_cnpj] if ev_cnpj else [], "regex")
    fields["invoice_numbers"] = build_field(bool(inv_numbers), True, inv_numbers, inv_ev, "regex_list")
    fields["invoice_number"] = build_field(bool(inv_first), False, inv_first, inv_ev[:1], "alias")
    fields["net_weight_kg"] = build_field(nw is not None, False, nw, [nw_ev] if nw_ev else [], "regex_number")
    fields["gross_weight_kg"] = build_field(gw is not None, False, gw, [gw_ev] if gw_ev else [], "regex_number")
    fields["ncm"] = build_field(bool(ncm), False, ncm, [ncm_ev] if ncm_ev else [], "regex")
    fields["ncm_or_hs"] = build_field(bool(ncm), False, ncm, [ncm_ev] if ncm_ev else [], "alias")
    fields["di_number"] = build_field(bool(di_no), False, di_no, [di_ev] if di_ev else [], "regex")
    fields["reference_internal"] = build_field(bool(nossa_ref), False, nossa_ref, [nossa_ev] if nossa_ev else [], "label")
    fields["reference_client"] = build_field(bool(sua_ref), False, sua_ref, [sua_ev] if sua_ev else [], "label")
    fields["bl_number"] = build_field(bool(bl_no), False, bl_no, [bl_ev] if bl_ev else [], "label")
    fields["transport_mode"] = build_field(bool(via_transporte), False, via_transporte, [via_ev] if via_ev else [], "label")
    fields["port_of_loading"] = build_field(bool(local_embarque), False, local_embarque, [loc_ev] if loc_ev else [], "label")
    fields["shipment_date"] = build_field(bool(data_embarque), False, data_embarque, [emb_ev] if emb_ev else [], "label")
    fields["arrival_date"] = build_field(bool(data_chegada), False, data_chegada, [cheg_ev] if cheg_ev else [], "label")

    fields["declaration_type"] = build_field(bool(tipo_declaracao), False, tipo_declaracao, [tipo_ev] if tipo_ev else [], "label")
    fields["operational_unit"] = build_field(bool(unidade_operacional), False, unidade_operacional, [un_ev] if un_ev else [], "label")
    fields["dispatch_urf"] = build_field(bool(urf_despacho), False, urf_despacho, [urf_ev] if urf_ev else [], "label")
    fields["dispatch_modality"] = build_field(bool(modalidade_desp), False, modalidade_desp, [mod_ev] if mod_ev else [], "label")
    fields["transport_carrier"] = build_field(bool(transportador), False, transportador, [transp_ev] if transp_ev else [], "label")
    fields["entry_urf"] = build_field(bool(urf_entrada), False, urf_entrada, [urf_ent_ev] if urf_ent_ev else [], "label")
    fields["country_of_provenance"] = build_field(bool(pais_proced), False, pais_proced, [pais_proc_ev] if pais_proc_ev else [], "label")

    fields["importer_address"] = build_field(bool(imp_endereco), False, imp_endereco, [imp_end_ev] if imp_end_ev else [], "label")
    fields["importer_number"] = build_field(bool(imp_numero), False, imp_numero, [imp_num_ev] if imp_num_ev else [], "label")
    fields["importer_complement"] = build_field(bool(imp_comp), False, imp_comp, [imp_comp_ev] if imp_comp_ev else [], "label")
    fields["importer_neighborhood"] = build_field(bool(imp_bairro), False, imp_bairro, [imp_bairro_ev] if imp_bairro_ev else [], "label")
    fields["importer_cep"] = build_field(bool(imp_cep), False, imp_cep, [imp_cep_ev] if imp_cep_ev else [], "label")
    fields["importer_city_uf"] = build_field(bool(imp_cidade_uf), False, imp_cidade_uf, [imp_cid_ev] if imp_cid_ev else [], "label")
    fields["importer_country"] = build_field(bool(imp_pais), False, imp_pais, [imp_pais_ev] if imp_pais_ev else [], "label")

    for k, meta in fields.items():
        if meta["required"] and not meta["present"]:
            missing.append(k)

    return fields, missing, warnings
