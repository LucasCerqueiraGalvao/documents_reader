# li.py
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
        find_incoterm,
        parse_number_locale,
        truncate_evidence,
    )
except ImportError:  # pragma: no cover
    from common import (
        build_field,
        find_cnpj,
        find_company_line_before_cnpj,
        find_first,
        find_incoterm,
        parse_number_locale,
        truncate_evidence,
    )

RE_LI_NUMBER = re.compile(r"(?is)\bLI\b\s*(?:NO\.?|Nº|NUMERO)?\s*[:\-]?\s*([0-9\-\.\/]+)")
RE_INVOICE_NO = re.compile(
    r"(?is)\b(?:INVOICE|COMMERCIAL\s+INVOICE|FATURA(?:\s+COMERCIAL)?|FATTURA)\b"
    r"[^\w]{0,10}([A-Z0-9][A-Z0-9\-\/\.]{2,})"
)
RE_NET_WEIGHT = re.compile(r"(?is)\bPESO\s+LIQUIDO\b\s*[:\-]?\s*([0-9\.,]+)")
RE_GROSS_WEIGHT = re.compile(r"(?is)\bPESO\s+BRUTO\b\s*[:\-]?\s*([0-9\.,]+)")
RE_NCM = re.compile(r"(?is)\bNCM\b\s*(?:NO\.?|Nº|NUMERO)?\s*[:\-]?\s*([0-9]{4,8})")
RE_COUNTRY_ORIGIN = re.compile(r"(?is)\bPA[IÍ]S\s+DE\s+ORIGEM\b\s*[:\-]?\s*([A-Z][A-Z \-]{2,})")
RE_COUNTRY_PROV = re.compile(r"(?is)\bPA[IÍ]S\s+DE\s+PROCED[ÊE]NCIA\b\s*[:\-]?\s*([A-Z][A-Z \-]{2,})")
RE_REF = re.compile(r"(?is)\bNREFERENCIA(?:\s+LI)?\b\s*[:\-]?\s*([A-Z0-9\-\/\.]+)")
RE_LI_NUMBER2 = re.compile(r"(?is)\bNR\s+LI\b\s*[:\-]?\s*([A-Z0-9\-\/\.]+)")
RE_EXPORTER = re.compile(r"(?is)\bEXPORTADOR\b\s*[:\-]?\s*(.+)$")
RE_COUNTRY_ACQ = re.compile(r"(?is)\bPA[I?]S\s+DE\s+AQUISI[?C][?A]O\b\s*[:\-]?\s*([A-Z0-9 \-]+)")
RE_COUNTRY_PROC = re.compile(r"(?is)\bPA[I?]S\s+PROC\b\s*[:\-]?\s*([A-Z0-9 \-]+)")
RE_QTY = re.compile(r"(?is)\bQUANT\s+MEDIDA\s+ESTAT\b\s*[:\-]?\s*([0-9\.,]+)")
RE_UNIT = re.compile(r"(?is)\bUNID\s+MEDIDA\s+ESTAT\b\s*[:\-]?\s*([A-Z ]{2,})")
RE_INCOTERM = re.compile(r"(?is)\bINCOTERM\b\s*[:\-]?\s*([A-Z]{3})")


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
            # tenta valor na mesma linha ap?s ':'
            if ":" in ln:
                after = ln.split(":", 1)[1].strip().lstrip(":").strip()
                if after and (not require_digit or re.search(r"\d", after)):
                    return after, ln
            # tenta nas pr?ximas linhas
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = (lines[j] or "").strip().lstrip(":").strip()
                if not cand or cand in {":", "-", "--"}:
                    continue
                if require_digit and not re.search(r"\d", cand):
                    continue
                return cand, cand
    return None, None

def _section_slice(lines: List[str], section_ascii: str, max_lines: int = 50) -> List[str]:
    section_ascii = (section_ascii or "").upper()
    for i, ln in enumerate(lines):
        if section_ascii in _strip_accents(ln).upper():
            end = min(i + 1 + max_lines, len(lines))
            for j in range(i + 1, end):
                if "INFORMA" in _strip_accents(lines[j]).upper() and "-" in lines[j]:
                    end = j
                    break
            return lines[i + 1 : end]
    return []




def _find_value_after_label(lines: List[str], label_re: str, require_digit: bool = False) -> Tuple[Optional[str], Optional[str]]:
    for i, ln in enumerate(lines):
        if re.search(label_re, ln, flags=re.I):
            m = re.search(label_re + r"\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m and m.group(1).strip():
                val = m.group(1).strip().lstrip(":").strip()
                if val in {":", "-", "--"}:
                    pass
                elif require_digit and not re.search(r"\d", val):
                    pass
                else:
                    return val, ln
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = (lines[j] or "").strip().lstrip(":").strip()
                if not cand or cand in {":", "-", "--"}:
                    continue
                if re.search(label_re, cand, flags=re.I):
                    continue
                if require_digit and not re.search(r"\d", cand):
                    continue
                return cand, cand
    return None, None


def _find_importer_name(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    # tenta "NOME DO IMPORTADOR" inline
    name, ev = _find_value_after_label(lines, r"NOME\s+DO\s+IMPORTADOR")
    if name:
        return name.lstrip(":").strip(), ev

    for i, ln in enumerate(lines):
        if re.search(r"\bIMPORTADOR\b", ln, flags=re.I):
            if ("IMPORTADOR" in ln.upper()) and ln.upper().startswith("INFORMA"):
                continue
            # valor na mesma linha
            m = re.search(r"\bIMPORTADOR\b\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m and m.group(1).strip():
                return m.group(1).strip(), ln
            # valor na linha seguinte
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"\b(CNPJ|CPF|LI|DI|PROCESSO)\b", cand, flags=re.I):
                    continue
                if re.search(r"\bIMPORTADOR\b", cand, flags=re.I):
                    continue
                return cand.lstrip(":").strip(), cand
    joined = "\n".join(lines)
    return find_company_line_before_cnpj(joined)


def _find_exporter_name(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    for i, ln in enumerate(lines):
        if re.search(r"\bEXPORTADOR\b", ln, flags=re.I):
            if re.search(r"FABRICANTE|PRODUTOR", ln, flags=re.I):
                continue
            m = re.search(r"\bEXPORTADOR\b\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m and m.group(1).strip():
                return m.group(1).strip(), ln
            for j in range(i + 1, min(i + 5, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"EXPORTADOR", cand, flags=re.I):
                    continue
                return cand.lstrip(":").strip(), cand
    return None, None


def extract_li_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    warnings: List[str] = []
    missing: List[str] = []
    fields: Dict[str, Any] = {}

    lines = _lines(text)

    importer_section = _section_slice(lines, "INFORMACOES - IMPORTADOR")
    exporter_section = _section_slice(lines, "EXPORTADOR / FABRICANTE / PRODUTOR")

    importer_name, ev_name = _find_importer_name(lines)
    cnpj, ev_cnpj = find_cnpj(text)

    li_no, li_ev = _find_value_after_label(lines, r"NR\s+LI", require_digit=True)
    if not li_no:
        li_no, li_ev = find_first(RE_LI_NUMBER, text)
    ref_li, ref_ev = _find_value_after_label(lines, r"NREFERENCIA(?:\s+LI)?", require_digit=True)
    inv_no, inv_ev = find_first(RE_INVOICE_NO, text)

    nw_raw, nw_ev = find_first(RE_NET_WEIGHT, text)
    gw_raw, gw_ev = find_first(RE_GROSS_WEIGHT, text)
    nw = parse_number_locale(nw_raw) if nw_raw else None
    gw = parse_number_locale(gw_raw) if gw_raw else None

    ncm, ncm_ev = find_first(RE_NCM, text)
    if ncm and len(ncm) not in (4, 6, 8):
        warnings.append(f"NCM/HS com {len(ncm)} digitos ({ncm}). Verificar.")

    coo, coo_ev = _find_value_after_label_contains(lines, "PAIS DE ORIGEM")
    if not coo:
        coo, coo_ev = find_first(RE_COUNTRY_ORIGIN, text)
    cop, cop_ev = _find_value_after_label_contains(lines, "PAIS DE PROCEDENCIA")
    if not cop:
        cop, cop_ev = find_first(RE_COUNTRY_PROV, text)
    caq, caq_ev = _find_value_after_label_contains(lines, "PAIS DE AQUISICAO", require_digit=True)
    if not caq:
        caq, caq_ev = find_first(RE_COUNTRY_ACQ, text)
    cproc, cproc_ev = _find_value_after_label_contains(lines, "PAIS PROC", require_digit=True)
    if not cproc:
        cproc, cproc_ev = find_first(RE_COUNTRY_PROC, text)
    qty_raw, qty_ev = find_first(RE_QTY, text)
    unit_meas, unit_ev = find_first(RE_UNIT, text)
    incoterm, inc_ev = find_incoterm(text)
    qty = parse_number_locale(qty_raw) if qty_raw else None

    exporter_name, exporter_ev = _find_exporter_name(lines)

    imp_end, imp_end_ev = _find_value_after_label_contains(importer_section, "ENDERECO")
    imp_num, imp_num_ev = _find_value_after_label_contains(importer_section, "NUMERO", require_digit=True)
    imp_comp, imp_comp_ev = _find_value_after_label_contains(importer_section, "COMPLEMENTO")
    imp_city, imp_city_ev = _find_value_after_label_contains(importer_section, "CIDADE")
    imp_country, imp_country_ev = _find_value_after_label_contains(importer_section, "PAIS")

    exp_end, exp_end_ev = _find_value_after_label_contains(exporter_section, "ENDERECO")
    exp_city, exp_city_ev = _find_value_after_label_contains(exporter_section, "CIDADE")
    exp_country, exp_country_ev = _find_value_after_label_contains(exporter_section, "PAIS")

    urf_desp, urf_desp_ev = _find_value_after_label_contains(lines, "URF DESPACHO", require_digit=True)
    urf_ent, urf_ent_ev = _find_value_after_label_contains(lines, "URF ENTRADA", require_digit=True)
    moeda, moeda_ev = _find_value_after_label_contains(lines, "MOEDA NEGOCIADA")
    cond_venda, cond_ev = _find_value_after_label_contains(lines, "CONDICAO DE VENDA")
    unidade_com, unidade_com_ev = _find_value_after_label_contains(lines, "UNIDADE COMERC")

    fields["importer_name"] = build_field(bool(importer_name), True, importer_name, [ev_name] if ev_name else [], "heuristic")
    fields["importer_cnpj"] = build_field(bool(cnpj), True, cnpj, [ev_cnpj] if ev_cnpj else [], "regex")
    fields["li_number"] = build_field(bool(li_no), False, li_no, [li_ev] if li_ev else [], "regex")
    fields["li_reference"] = build_field(bool(ref_li), False, ref_li, [ref_ev] if ref_ev else [], "regex")
    fields["invoice_number"] = build_field(bool(inv_no), False, inv_no, [inv_ev] if inv_ev else [], "regex")
    fields["net_weight_kg"] = build_field(nw is not None, False, nw, [nw_ev] if nw_ev else [], "regex_number")
    fields["gross_weight_kg"] = build_field(gw is not None, False, gw, [gw_ev] if gw_ev else [], "regex_number")
    fields["ncm"] = build_field(bool(ncm), False, ncm, [ncm_ev] if ncm_ev else [], "regex")
    fields["ncm_or_hs"] = build_field(bool(ncm), False, ncm, [ncm_ev] if ncm_ev else [], "alias")
    fields["country_of_origin"] = build_field(bool(coo), False, coo, [coo_ev] if coo_ev else [], "regex")
    fields["country_of_provenance"] = build_field(bool(cop), False, cop, [cop_ev] if cop_ev else [], "regex")
    fields["country_of_acquisition"] = build_field(bool(caq), False, caq, [caq_ev] if caq_ev else [], "regex")
    fields["country_proc"] = build_field(bool(cproc), False, cproc, [cproc_ev] if cproc_ev else [], "regex")
    fields["exporter_name"] = build_field(bool(exporter_name), False, exporter_name, [exporter_ev] if exporter_ev else [], "label")
    fields["quantity"] = build_field(qty is not None, False, qty, [qty_ev] if qty_ev else [], "regex_number")
    fields["unit_measure"] = build_field(bool(unit_meas), False, unit_meas, [unit_ev] if unit_ev else [], "regex")
    fields["incoterm"] = build_field(bool(incoterm), False, incoterm, [inc_ev] if inc_ev else [], "regex")

    fields["importer_address"] = build_field(bool(imp_end), False, imp_end, [imp_end_ev] if imp_end_ev else [], "label")
    fields["importer_number"] = build_field(bool(imp_num), False, imp_num, [imp_num_ev] if imp_num_ev else [], "label")
    fields["importer_complement"] = build_field(bool(imp_comp), False, imp_comp, [imp_comp_ev] if imp_comp_ev else [], "label")
    fields["importer_city"] = build_field(bool(imp_city), False, imp_city, [imp_city_ev] if imp_city_ev else [], "label")
    fields["importer_country"] = build_field(bool(imp_country), False, imp_country, [imp_country_ev] if imp_country_ev else [], "label")

    fields["exporter_address"] = build_field(bool(exp_end), False, exp_end, [exp_end_ev] if exp_end_ev else [], "label")
    fields["exporter_city"] = build_field(bool(exp_city), False, exp_city, [exp_city_ev] if exp_city_ev else [], "label")
    fields["exporter_country"] = build_field(bool(exp_country), False, exp_country, [exp_country_ev] if exp_country_ev else [], "label")

    fields["dispatch_urf"] = build_field(bool(urf_desp), False, urf_desp, [urf_desp_ev] if urf_desp_ev else [], "label")
    fields["entry_urf"] = build_field(bool(urf_ent), False, urf_ent, [urf_ent_ev] if urf_ent_ev else [], "label")
    fields["currency"] = build_field(bool(moeda), False, moeda, [moeda_ev] if moeda_ev else [], "label")
    fields["purchase_condition"] = build_field(bool(cond_venda), False, cond_venda, [cond_ev] if cond_ev else [], "label")
    fields["unit_commercial"] = build_field(bool(unidade_com), False, unidade_com, [unidade_com_ev] if unidade_com_ev else [], "label")

    for k, meta in fields.items():
        if meta["required"] and not meta["present"]:
            missing.append(k)

    return fields, missing, warnings
