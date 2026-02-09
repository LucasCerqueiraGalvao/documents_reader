# -*- coding: utf-8 -*-
"""
Stage 02 - Importation - Packing List field extraction (stdlib only)

Fixes:
- aceita "CARTON" (singular) e "CARTONS" (plural)
- aceita "No." como 1 número ("5") ou range ("19 - 21")
- captura a linha final "* MODEL: DF300APXX" + "5 1 CARTON ..." + pesos "323 388"
- soma e compara com TOTAL; só gera warning se realmente divergir

Return signature:
    fields_dict, missing_required_fields, warnings
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from .common import parse_number_locale, truncate_evidence
except ImportError:  # pragma: no cover
    from common import parse_number_locale, truncate_evidence


# -----------------------------
# Helpers
# -----------------------------
def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _digits(s: str) -> str:
    """Mantém só dígitos (útil p/ CNPJ)."""
    return re.sub(r"\D+", "", str(s or ""))

def _clean_company_name(raw: str) -> str:
    """Normaliza nome de empresa extraído com ruído.

    - Remove data do final (ex: "AUG. 28,2025")
    - Se achar sufixo societário (LTDA/LTD/SA/etc.), corta até ele
    - Remove trechos comuns de BL (RECEIVED BY..., TEL..., etc.)
    """
    s = _clean_spaces(raw)

    # corrigir OCR quebrando palavra
    s = re.sub(r"\bVEICU\s+LOS\b", "VEICULOS", s, flags=re.I)

    # cortar em keywords que às vezes grudam no nome
    upper = s.upper()
    for kw in [" RECEIVED BY", " RECEIVED", " PARTY TO CONTACT", " TEL", " PHONE", " PH:"]:
        idx = upper.find(kw)
        if idx > 0:
            s = s[:idx].strip()
            upper = s.upper()

    # remover data do final (ex: AUG. 28,2025)
    s = re.sub(
        r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s+\d{1,2},\s*\d{4}\b",
        "",
        s,
        flags=re.I,
    ).strip()

    # cortar no sufixo societário (se existir)
    m = re.search(r"\b(LTDA\.?|LTD\.?|S\.?A\.?|S/A|SA|INC\.?|LLC|CORPORATION|CORP\.?)\b", s, flags=re.I)
    if m:
        s = s[: m.end()].strip()

    # limpeza final
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
        "evidence": truncate_evidence(evidence or []),
        "method": method,
    }


def _parse_number(v: str) -> Optional[float]:
    return parse_number_locale(v)


def _lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _find_invoice_number(lines: List[str]) -> Tuple[Optional[str], List[str]]:
    # New style: "INVOICE nr. INVOICE_EXT 37 DATA 02/02/2026"
    for ln in lines:
        if "INVOICE" not in ln.upper():
            continue

        m_num = re.search(
            r"\bINVOICE\b.*?\b(?:NR\.?|NO\.?|NUMBER|N[Oº°\.]?)\b[^\d]*(?:INVOICE[_\-\s]*EXT\s*)?(\d{1,10})\b",
            ln,
            flags=re.I,
        )
        if m_num:
            return m_num.group(1), [ln]

        m_ext = re.search(r"\bINVOICE[_\-\s]*EXT\s*(\d{1,10})\b", ln, flags=re.I)
        if m_ext:
            return m_ext.group(1), [ln]

    # Ex: "DN-24139-P" ou "DN-24139"
    for ln in lines:
        m = re.search(r"\b([A-Z]{1,4}-\d{3,10}(?:-[A-Z])?)\b", ln)
        if m and "PACKING" in ln.upper():
            return m.group(1), [ln]
    # fallback: pega primeiro DN-xxxxx
    for ln in lines:
        m = re.search(r"\b([A-Z]{1,4}-\d{3,10}(?:-[A-Z])?)\b", ln)
        if m:
            return m.group(1), [ln]
    return None, []

def _find_importer_name_cnpj(lines: List[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Extrai Importer (Consignee) e CNPJ no Packing List.

    Heuristica preferida (mais consistente):
      - localizar a linha do CNPJ
      - pegar a linha imediatamente ANTERIOR como nome
    Depois aplica limpeza para remover data/ruido.

    Fallback:
      - procurar 'ACCOUNT OF' e usar a proxima linha como nome
    """
    evidence: List[str] = []

    # 0) New style: "COMPANY <name> COUNTRY <...>"
    name = None
    for ln in lines:
        m_company = re.search(
            r"\bCOMPANY\b\s*[:\-]?\s*(.+?)(?:\s+\bCOUNTRY\b|$)", ln, flags=re.I
        )
        if not m_company:
            continue
        cand = _clean_company_name(m_company.group(1))
        if cand:
            name = cand
            evidence.append(ln)
            break

    # 1) localizar CNPJ/VAT e pegar linha anterior como nome
    cnpj_idx = None
    cnpj_raw_line = None
    cnpj = None

    for i, ln in enumerate(lines):
        m = re.search(
            r"\b(?:CNPJ|VAT\s*NUMBER|P\.?\s*IVA|PARTITA\s+I\.?\s*V\.?\s*A\.?|COD\.?\s*FISCALE(?:\s*P\.?\s*IVA)?)\b\s*[:#]?\s*([0-9][0-9\.\-/]{11,})\b",
            ln,
            flags=re.I,
        )
        if m:
            cnpj_idx = i
            cnpj_raw_line = ln.strip()
            cnpj = _digits(m.group(1))
            break

    # unlabeled CNPJ shape fallback
    if not cnpj:
        for i, ln in enumerate(lines):
            m_any = re.search(
                r"(\d{2}[.\s]?\d{3}[.\s]?\d{3}[\/\s]?\d{4}[-\s]?\d{2})", ln
            )
            if m_any:
                cnpj_idx = i
                cnpj_raw_line = ln.strip()
                cnpj = _digits(m_any.group(1))
                break

    if cnpj_idx is not None:
        # volta ate achar uma linha boa (nao vazia / nao label)
        for j in range(cnpj_idx - 1, max(-1, cnpj_idx - 6), -1):
            cand = (lines[j] or "").strip()
            if not cand:
                continue
            if re.search(
                r"\b(ACCOUNT OF|CNPJ|VAT|INVOICE|PACKING|P\.)\b", cand, flags=re.I
            ):
                continue
            if not name:
                name = _clean_company_name(cand)
                evidence.append(cand)
                break

        if cnpj_raw_line:
            evidence.append(cnpj_raw_line)

    # 2) fallback: ACCOUNT OF + proxima linha
    if not name:
        for i, ln in enumerate(lines):
            if re.search(r"\bACCOUNT OF\b", ln, flags=re.I):
                for j in range(i + 1, min(i + 6, len(lines))):
                    cand = (lines[j] or "").strip()
                    if not cand:
                        continue
                    if re.search(r"\b(CNPJ|INVOICE|PACKING)\b", cand, flags=re.I):
                        continue
                    name = _clean_company_name(cand)
                    evidence.append(cand)
                    break
                break

    return (name or None), (cnpj or None), evidence


def _find_shipper_name(lines: List[str]) -> Tuple[Optional[str], List[str]]:
    """Heuristica para achar shipper/exporter em Packing List.

    Busca por blocos 'SHIPPER/EXPORTER' e, se nao houver,
    tenta recuperar um nome de empresa imediatamente antes de 'ACCOUNT OF'.
    """
    evidence: List[str] = []

    # 1) Se houver SHIPPER/EXPORTER explicito
    for i, ln in enumerate(lines):
        if re.search(r"\b(SHIPPER|EXPORTER)\b", ln, flags=re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"\b(ACCOUNT OF|CNPJ|INVOICE|PACKING|P\.|ORDER|SALES|MODEL)\b", cand, flags=re.I):
                    continue
                name = _clean_company_name(cand)
                if name:
                    evidence.append(cand)
                    return name, evidence

    # 2) Fallback: procurar linha antes de 'ACCOUNT OF'
    for i, ln in enumerate(lines):
        if re.search(r"\bACCOUNT OF\b", ln, flags=re.I):
            # primeiro tenta achar um candidato com palavras-chave empresariais
            for j in range(i - 1, max(-1, i - 8), -1):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                up = cand.upper()
                if re.search(r"\b(CNPJ|INVOICE|PACKING|ORDER|SALES|MODEL|CARTON|UNITS?|PAGE|P\.)\b", up):
                    continue
                if sum(ch.isalpha() for ch in cand) < 3:
                    continue
                if re.search(r"\b(LTDA|LTD|S\.A\.|S/A|SA|INC|LLC|CORP|CO\.|COMPANY|INDUSTR|COMERC|MOTOR|MOTORS|LOGIX|LOGISTICS)\b", up):
                    name = _clean_company_name(cand)
                    if name:
                        evidence.append(cand)
                        return name, evidence

            # se nao achou com keyword, pega o primeiro plausivel
            for j in range(i - 1, max(-1, i - 8), -1):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                up = cand.upper()
                if re.search(r"\b(CNPJ|INVOICE|PACKING|ORDER|SALES|MODEL|CARTON|UNITS?|PAGE|P\.)\b", up):
                    continue
                if sum(ch.isalpha() for ch in cand) < 3:
                    continue
                name = _clean_company_name(cand)
                if name:
                    evidence.append(cand)
                    return name, evidence
            break

    # 3) fallback: top lines with company suffix (common in one-page PL)
    for ln in lines[:12]:
        cand = _clean_company_name(ln)
        if not cand:
            continue
        if re.search(
            r"\b(LTDA|LTD|S\.?P\.?A\.?|S\.?R\.?L\.?|S\.?A\.?|S/A|INC|LLC|CORP|CO\.)\b",
            cand,
            flags=re.I,
        ):
            return cand, [ln]

    return None, []

# -----------------------------
# Table parsing (critical fix)
# -----------------------------
ROW_RE = re.compile(
    r"""^
    (?P<no>\d+(?:\s*-\s*\d+)?)          # "19 - 21" ou "5"
    \s+
    (?P<packs>\d+)\s+
    CARTON(?:S)?                        # CARTON ou CARTONS
    \s+
    @(?P<nw>[0-9\.,]+)\s+@(?P<gw>[0-9\.,]+)  # @199 @264 (não é o peso final, mas ajuda a reconhecer a linha)
    \s+
    (?P<m3_each>[0-9\.,]+)\s+
    (?P<m3_total>[0-9\.,]+)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

WEIGHTS_LINE_RE = re.compile(r"([0-9][0-9\.,]*)")


def _extract_items(lines: List[str]) -> Tuple[List[dict], List[str]]:
    items: List[dict] = []
    evidence: List[str] = []
    current_model: Optional[str] = None

    i = 0
    while i < len(lines):
        ln = lines[i]

        # model marker
        m_model = re.search(r"\*\s*MODEL\s*:\s*([A-Z0-9\-]+)", ln, flags=re.I)
        if m_model:
            current_model = m_model.group(1).strip()
            i += 1
            continue

        m = ROW_RE.match(ln)
        if m:
            # weights are on the next non-empty line (ex: "323 388")
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            net_val = None
            gross_val = None
            if j < len(lines):
                nums = WEIGHTS_LINE_RE.findall(lines[j])
                if len(nums) >= 2:
                    net_val = _parse_number(nums[0])
                    gross_val = _parse_number(nums[1])

            item = {
                "model": current_model,
                "no": _clean_spaces(m.group("no")),
                "packages": int(m.group("packs")),
                "net_weight_kg": net_val,
                "gross_weight_kg": gross_val,
                "m3_total": _parse_number(m.group("m3_total")),
                "raw_line": ln,
                "raw_weights_line": lines[j] if j < len(lines) else None,
            }
            items.append(item)
            evidence.append(ln)
            if j < len(lines):
                evidence.append(lines[j])

            # avança: consumiu row + weights line
            i = j + 1
            continue

        i += 1

    return items, evidence


def _extract_items_compact_fallback(
    lines: List[str],
    total_packs: Optional[int],
    total_net: Optional[float],
    total_gross: Optional[float],
    total_m3: Optional[float],
) -> Tuple[List[dict], List[str]]:
    """
    Fallback for OCR-compact PLs where full row parser cannot identify CARTON rows.
    """
    items: List[dict] = []
    evidence: List[str] = []

    for ln in lines:
        m = re.match(r"^\s*([A-Z0-9]{6,16})\s+(.+)$", ln)
        if not m:
            continue
        up = ln.upper()
        if re.search(
            r"\b(PACKING|INVOICE|TOTAL|WEIGHT|VOLUME|BOX|CARTON|COMPANY|VAT|CNPJ)\b",
            up,
        ):
            continue
        desc = _clean_spaces(m.group(2))
        if len(desc) < 8:
            continue
        item = {
            "model": m.group(1),
            "no": None,
            "packages": total_packs,
            "net_weight_kg": total_net,
            "gross_weight_kg": total_gross,
            "m3_total": total_m3,
            "raw_line": ln,
            "raw_weights_line": None,
        }
        items.append(item)
        evidence.append(ln)
        break

    if not items and any(v is not None for v in [total_packs, total_net, total_gross, total_m3]):
        items.append(
            {
                "model": "SUMMARY",
                "no": None,
                "packages": total_packs,
                "net_weight_kg": total_net,
                "gross_weight_kg": total_gross,
                "m3_total": total_m3,
                "raw_line": "summary_from_totals",
                "raw_weights_line": None,
            }
        )
        evidence.append("summary_from_totals")

    return items, evidence


def _find_total_line(lines: List[str]) -> Tuple[Optional[int], Optional[float], Optional[float], Optional[float], List[str]]:
    """
    TOTAL : 33 CARTONS 7,980 9,825 53.772
             ^packs     ^net   ^gross  ^m3
    """
    for ln in lines:
        m = re.search(
            r"\bTOTAL\b\s*:?\s*(\d+)\s*CARTON(?:S)?\s+([0-9\.,]+)\s+([0-9\.,]+)\s+([0-9\.,]+)",
            ln,
            flags=re.I,
        )
        if m:
            packs = int(m.group(1))
            net = _parse_number(m.group(2))
            gross = _parse_number(m.group(3))
            m3 = _parse_number(m.group(4))
            return packs, net, gross, m3, [ln]

    packs: Optional[int] = None
    net: Optional[float] = None
    gross: Optional[float] = None
    m3: Optional[float] = None
    evidence: List[str] = []

    for i, ln in enumerate(lines):
        block = ln
        if i + 1 < len(lines):
            block = f"{ln} {lines[i + 1]}"

        if packs is None:
            m_pack = re.search(
                r"\bTOTAL\s+(?:BOXES|BOX|CARTON(?:S)?)\b.*?\b(?:NR\.?|N[Oº°\.]?)\s*[:\-]?\s*(\d+)\b",
                block,
                flags=re.I,
            )
            if not m_pack:
                m_pack = re.search(
                    r"\bTOTAL\b\s*:?\s*(\d+)\s*(?:BOXES|BOX|CARTON(?:S)?)\b",
                    block,
                    flags=re.I,
                )
            if m_pack:
                packs = int(m_pack.group(1))
                evidence.append(ln)

        if net is None or gross is None:
            m_both = re.search(
                r"\bGROSS(?:\s+WEIGHT)?\b[\sA-Z:]*?(?:KILO|KGS?|KG)\s*([0-9][0-9\.,]*)\s*KG?\b.*?\bNET(?:\s+WEIGHT)?\b[\sA-Z:]*?(?:KILO|KGS?|KG)\s*([0-9][0-9\.,]*)\s*KG?\b",
                block,
                flags=re.I,
            )
            if m_both:
                gross = _parse_number(m_both.group(1))
                net = _parse_number(m_both.group(2))
                evidence.append(ln)
            else:
                if gross is None:
                    mg = re.search(
                        r"\bGROSS(?:\s+WEIGHT)?\b[^\d]{0,30}(?:TOTAL\s+)?(?:KILO|KGS?|KG)\b[^\d]{0,10}([0-9][0-9\.,]*)\b",
                        block,
                        flags=re.I,
                    )
                    if mg:
                        gross = _parse_number(mg.group(1))
                        evidence.append(ln)
                if net is None:
                    mn = re.search(
                        r"\bNET(?:\s+WEIGHT)?\b[^\d]{0,30}(?:TOTAL\s+)?(?:KILO|KGS?|KG)\b[^\d]{0,10}([0-9][0-9\.,]*)\b",
                        block,
                        flags=re.I,
                    )
                    if mn:
                        net = _parse_number(mn.group(1))
                        evidence.append(ln)

        if m3 is None:
            m_m3_inline = re.search(
                r"\bTOTAL\s+VOLUME\b[^\dA-Z]{0,10}(?:MC|M3)\b[^\d]{0,10}([0-9][0-9\.,]*)\b",
                block,
                flags=re.I,
            )
            if m_m3_inline:
                m3 = _parse_number(m_m3_inline.group(1))
                evidence.append(ln)
            elif re.search(r"\bTOTAL\s+VOLUME\b.*\b(?:MC|M3)\b", ln, flags=re.I):
                if i + 1 < len(lines):
                    m_next = re.search(r"\b([0-9][0-9\.,]*)\b", lines[i + 1])
                    if m_next:
                        m3 = _parse_number(m_next.group(1))
                        evidence.append(ln)
                        evidence.append(lines[i + 1])

    if any(v is not None for v in [packs, net, gross, m3]):
        return packs, net, gross, m3, evidence

    return None, None, None, None, []


def extract_packing_list_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    ln = _lines(text)

    warnings: List[str] = []
    missing: List[str] = []

    invoice_no, inv_ev = _find_invoice_number(ln)
    importer_name, importer_cnpj, imp_ev = _find_importer_name_cnpj(ln)
    shipper_name, shipper_ev = _find_shipper_name(ln)

    items, items_ev = _extract_items(ln)
    total_packs, total_net, total_gross, total_m3, total_ev = _find_total_line(ln)

    if not items:
        items_fb, items_fb_ev = _extract_items_compact_fallback(
            ln, total_packs, total_net, total_gross, total_m3
        )
        if items_fb:
            items = items_fb
            items_ev = items_fb_ev
            warnings.append("packing_items_compact_fallback_used")

    if total_packs is None and items:
        p_vals = [it.get("packages") for it in items if it.get("packages") is not None]
        if p_vals:
            total_packs = int(sum(int(x) for x in p_vals))

    if total_net is None and items:
        n_vals = [it.get("net_weight_kg") for it in items if it.get("net_weight_kg") is not None]
        if n_vals:
            total_net = float(sum(float(x) for x in n_vals))

    if total_gross is None and items:
        g_vals = [it.get("gross_weight_kg") for it in items if it.get("gross_weight_kg") is not None]
        if g_vals:
            total_gross = float(sum(float(x) for x in g_vals))

    if total_m3 is None and items:
        m_vals = [it.get("m3_total") for it in items if it.get("m3_total") is not None]
        if m_vals:
            total_m3 = float(sum(float(x) for x in m_vals))

    # soma da tabela (se conseguiu captar os itens)
    sum_net = sum((it["net_weight_kg"] or 0.0) for it in items if it.get("net_weight_kg") is not None)
    sum_gross = sum((it["gross_weight_kg"] or 0.0) for it in items if it.get("gross_weight_kg") is not None)

    # decide o que usar como valor final:
    # TOTAL é a fonte mais confiável; mas a soma deve bater (se parser pegou tudo)
    net_final = total_net
    gross_final = total_gross

    # warning só se tiver TOTAL e tiver itens e realmente divergir acima de tolerância
    tol = 0.5
    if total_net is not None and items:
        if abs(sum_net - total_net) > tol:
            warnings.append(
                f"Soma Net Weight da tabela ({sum_net:.2f}) difere do TOTAL ({total_net:.2f}). Usando TOTAL."
            )
    if total_gross is not None and items:
        if abs(sum_gross - total_gross) > tol:
            warnings.append(
                f"Soma Gross Weight da tabela ({sum_gross:.2f}) difere do TOTAL ({total_gross:.2f}). Usando TOTAL."
            )

    if total_m3 is None and any(
        re.search(r"\bTOTAL\s+VOLUME\b.*\b(?:MC|M3)\b", x, flags=re.I) for x in ln
    ):
        warnings.append("packing_total_volume_found_without_numeric_value")

    required = {
        "invoice_number": True,
        "importer_name": True,
        "importer_cnpj": True,   # regra Karina
        "packages_total": True,
        "net_weight_kg": True,
        "gross_weight_kg": True,
        "measurement_total_m3": True,
        "items": True,
    }

    fields: Dict[str, Any] = {}
    fields["invoice_number"] = _mk_field(invoice_no, required["invoice_number"], inv_ev, "regex")
    fields["importer_name"] = _mk_field(importer_name, required["importer_name"], imp_ev[:1], "line_block")
    fields["shipper_name"] = _mk_field(shipper_name, False, shipper_ev, "heuristic")
    fields["importer_cnpj"] = _mk_field(importer_cnpj, required["importer_cnpj"], imp_ev, "regex")
    fields["packages_total"] = _mk_field(total_packs, required["packages_total"], total_ev, "regex_total")
    fields["net_weight_kg"] = _mk_field(net_final, required["net_weight_kg"], total_ev or items_ev[:2], "regex_total")
    fields["gross_weight_kg"] = _mk_field(gross_final, required["gross_weight_kg"], total_ev or items_ev[:2], "regex_total")
    fields["measurement_total_m3"] = _mk_field(total_m3, required["measurement_total_m3"], total_ev, "regex_total")
    fields["items"] = _mk_field(items, required["items"], items_ev[:6], "table_parse")

    for k, meta in fields.items():
        if meta["required"] and not meta["present"]:
            missing.append(k)

    return fields, missing, warnings
