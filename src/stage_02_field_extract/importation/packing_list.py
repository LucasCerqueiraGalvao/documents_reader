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

    # 7,980.000
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    # 7.980,00
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
            s = left + right
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
            s = left + right
        try:
            return float(s)
        except ValueError:
            return None

    # ambos
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


def _find_invoice_number(lines: List[str]) -> Tuple[Optional[str], List[str]]:
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
    """
    PL tem 'ACCOUNT OF' e depois o nome
    """
    evidence: List[str] = []
    name = None

    for i, ln in enumerate(lines):
        if "ACCOUNT OF" in ln.upper():
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = lines[j].strip()
                if not cand:
                    continue
                if re.search(r"\bCNPJ\b", cand, flags=re.I):
                    continue
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
    return None, None, None, None, []


def extract_packing_list_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    ln = _lines(text)

    warnings: List[str] = []
    missing: List[str] = []

    invoice_no, inv_ev = _find_invoice_number(ln)
    importer_name, importer_cnpj, imp_ev = _find_importer_name_cnpj(ln)

    items, items_ev = _extract_items(ln)
    total_packs, total_net, total_gross, total_m3, total_ev = _find_total_line(ln)

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
