# -*- coding: utf-8 -*-
"""
Stage 02 - Importation - BL/HBL field extraction (stdlib only)

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
        r"\b(LTDA\.?|LTD\.?|S\.?A\.?|S/A|SA|SRL|S\.?R\.?L\.?|S\.?P\.?A\.?|SPA|INC\.?|LLC|CORPORATION|CORP\.?)\b",
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
        "evidence": truncate_evidence(evidence or []),
        "method": method,
    }


def _parse_number(v: str) -> Optional[float]:
    return parse_number_locale(v)


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
        if re.search(r"\b(SHIPPER|CONSIGNOR|CONSIGNER)\b", ln, flags=re.I):
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
                name = _clean_company_name(_clean_spaces(" ".join(parts)))
                if name and len(name) >= 3:
                    return name, [cand]

            break

    # fallback: às vezes vem "SHIPPER:" na mesma linha
    for ln in lines:
        m = re.search(r"\bSHIPPER\b\s*:?\s*(.+)$", ln, flags=re.I)
        if m:
            name = _clean_company_name(_clean_spaces(m.group(1)))
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
        if re.search(r"\bCONSIGNEE\b", ln, flags=re.I) or re.search(
            r"CONSIGNED\s+TO\s+THE\s+ORDER\s+OF", ln, flags=re.I
        ):
            consignee_idx = i
            break

    # fallback: "Consigned to the order of" sem bloco CONSIGNEE
    if consignee_idx is None:
        for i, ln in enumerate(lines):
            if re.search(r"CONSIGNED\s+TO\s+THE\s+ORDER\s+OF", ln, flags=re.I):
                consignee_idx = i
                # tenta capturar nome na próxima linha
                for j in range(i + 1, min(i + 6, len(lines))):
                    cand = (lines[j] or "").strip()
                    if not cand:
                        continue
                    if re.search(r"\b(CNPJ|NOTIFY|TEL|PHONE)\b", cand, flags=re.I):
                        continue
                    name = _clean_company_name(cand)
                    if name:
                        evidence.append(cand)
                        break
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
                if re.search(r"\b(CON...|CONSIGNED|NOTIFY|CNPJ|TEL|PHONE)\b", cand, flags=re.I):
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
    Aceitar 4, 6 ou 8 dígitos (HS/NCM).
    - 4/6 dígitos: warning (HS parcial), mas não FAIL.
    - Outros tamanhos: warning.
    """
    warnings: List[str] = []
    for ln in lines:
        m = re.search(r"\bNCM\b\s*(?:NO\.?|Nº)?\s*([0-9]{4,8})", ln, flags=re.I)
        if m:
            code = m.group(1)
            if len(code) not in (4, 6, 8):
                warnings.append(f"NCM/HS com {len(code)} digitos ({code}). Verificar.")
            return code, [ln], warnings

    return None, [], warnings


def _find_gross_weight(lines: List[str]) -> Tuple[Optional[float], List[str]]:
    # Ex: "Gross Weight 'in kilo's" e logo depois "9,825.000 KG"
    kg_re = re.compile(r"([0-9][0-9\.,]+)\s*K\s*[G6S](?:S|M)?\b", flags=re.I)
    m3_re = re.compile(r"([0-9][0-9\.,]+)\s*[A-Z0-9]{0,3}\s*[0-9][0-9\.,]*\s*(?:M3|CBM)\b", flags=re.I)

    for i, ln in enumerate(lines):
        if re.search(r"\bGROSS\s+WEIGHT\b", ln, flags=re.I):
            candidates = []
            # procura nas pr?ximas linhas um n?mero + KG
            for j in range(i, min(i + 8, len(lines))):
                cand = lines[j]
                nums = []
                for m in kg_re.finditer(cand):
                    num = _parse_number(m.group(1))
                    if num is not None:
                        nums.append(num)
                if nums:
                    if len(nums) > 1:
                        return sum(nums), [cand]
                    candidates.append((nums[0], cand))
                # fallback: linha de tabela com M3/CBM (KG pode ter sumido no OCR)
                m2 = m3_re.search(cand)
                if m2:
                    num = _parse_number(m2.group(1))
                    if num is not None:
                        candidates.append((num, cand))
            if candidates:
                best = max(candidates, key=lambda x: x[0])
                return best[0], [best[1]]
            break

    # fallback: qualquer linha com "KG" e formato do BL
    for ln in lines:
        m = kg_re.search(ln)
        if m and re.search(r"\bWEIGHT\b", ln, flags=re.I):
            num = _parse_number(m.group(1))
            if num is not None:
                return num, [ln]

    return None, []


def _find_measurement_m3(lines: List[str]) -> Tuple[Optional[float], List[str]]:
    m3_re = re.compile(r"([0-9][0-9\.,]+)\s*(M3|CBM)\b", flags=re.I)
    for ln in lines:
        m = m3_re.search(ln)
        if m:
            num = _parse_number(m.group(1))
            if num is not None:
                return num, [ln]
    return None, []


def _find_notify_party(lines: List[str]) -> Tuple[Optional[str], List[str]]:
    for i, ln in enumerate(lines):
        if re.search(r"\bNOTIFY\b", ln, flags=re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = (lines[j] or "").strip()
                if not cand:
                    continue
                if re.search(r"SAME\s+AS\s+CONSIGNEE", cand, flags=re.I):
                    return "SAME AS CONSIGNEE", [cand]
                if re.search(r"\b(CONSIGNEE|SHIPPER|BOOKING|B/L|PORT)\b", cand, flags=re.I):
                    continue
                return _clean_company_name(cand), [cand]
    return None, []


def _find_ports(lines: List[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    pol = None
    pod = None
    ev: List[str] = []
    def _is_port_candidate(s: str) -> bool:
        up = s.upper()
        if any(
            kw in up
            for kw in [
                "EMAIL",
                "E-MAIL",
                "TEL",
                "FAX",
                "CNPJ",
                "IVA",
                "CAP.SOC",
                "CAP SOC",
                "TRIB",
                "C.C.",
                "PORT OF",
                "PLACE OF",
                "DELIVERY",
            ]
        ):
            return False
        if sum(ch.isalpha() for ch in s) < 3:
            return False
        return True

    for ln in lines:
        if re.search(r"\bPORT\s+OF\s+LOADING\b", ln, flags=re.I):
            m = re.search(r"\bPORT\s+OF\s+LOADING\b\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m:
                candidate = _clean_spaces(m.group(1))
                if candidate and _is_port_candidate(candidate) and ("@" not in candidate):
                    pol = candidate
                    ev.append(ln)
        if re.search(r"\bPORT\s+OF\s+DISCHARGE\b", ln, flags=re.I):
            m = re.search(r"\bPORT\s+OF\s+DISCHARGE\b\s*[:\-]?\s*(.+)$", ln, flags=re.I)
            if m:
                candidate = _clean_spaces(m.group(1))
                if candidate and _is_port_candidate(candidate) and ("@" not in candidate):
                    pod = candidate
                    ev.append(ln)

    # fallback: se não achou valor, tenta próxima linha após o marcador
    if not pol:
        for i, ln in enumerate(lines):
            if re.search(r"\bPORT\s+OF\s+LOADING\b", ln, flags=re.I):
                for j in range(i + 1, min(i + 4, len(lines))):
                    cand = (lines[j] or "").strip()
                    if not cand:
                        continue
                    if re.search(r"\b(PORT\s+OF|PLACE\s+OF|E-MAIL|EMAIL)\b", cand, flags=re.I):
                        continue
                    if not _is_port_candidate(cand):
                        continue
                    pol = _clean_spaces(cand)
                    ev.append(cand)
                    break
                break
    if not pol:
        for i, ln in enumerate(lines):
            if re.search(r"\bPORT\s+OF\s+LOADING\b", ln, flags=re.I) and re.search(r"\b(OCEAN\s+VESSEL|VESSEL)\b", ln, flags=re.I):
                for j in range(i + 1, min(i + 3, len(lines))):
                    cand = (lines[j] or "").strip()
                    if not cand:
                        continue
                    # corta partes administrativas
                    cut = re.split(r"\b(C\.C\.|IVA|P\.?\s*IVA|CAP\.?SOC|TRIB)\b", cand, flags=re.I)[0]
                    words = re.findall(r"\b[A-Z]{4,}\b", cut.upper())
                    if words:
                        pol = words[-1]
                        ev.append(cand)
                        break
                if pol:
                    break

    if not pod:
        for i, ln in enumerate(lines):
            if re.search(r"\bPORT\s+OF\s+DISCHARGE\b", ln, flags=re.I):
                for j in range(i + 1, min(i + 4, len(lines))):
                    cand = (lines[j] or "").strip()
                    if not cand:
                        continue
                    if re.search(r"\b(PORT\s+OF|PLACE\s+OF|E-MAIL|EMAIL)\b", cand, flags=re.I):
                        continue
                    if not _is_port_candidate(cand):
                        continue
                    pod = _clean_spaces(cand)
                    ev.append(cand)
                    break
                break
    return pol, pod, ev


def _find_freight_terms(lines: List[str]) -> Tuple[Optional[str], List[str]]:
    """Extrai FREIGHT TERMS (COLLECT/PREPAID) do BL."""
    for i, ln in enumerate(lines):
        if re.search(r"\bFREIGHT\b", ln, flags=re.I):
            if re.search(r"\bCOLLECT\b", ln, flags=re.I):
                return "COLLECT", [ln]
            if re.search(r"\bPREPAID\b", ln, flags=re.I):
                return "PREPAID", [ln]
            # tenta pr??xima linha
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if re.search(r"\bCOLLECT\b", nxt, flags=re.I):
                    return "COLLECT", [ln, nxt]
                if re.search(r"\bPREPAID\b", nxt, flags=re.I):
                    return "PREPAID", [ln, nxt]

    # fallback: procura COLLECT/PREPAID com FREIGHT na linha anterior
    for i, ln in enumerate(lines):
        if re.search(r"\b(COLLECT|PREPAID)\b", ln, flags=re.I):
            if re.search(r"\bFREIGHT\b", ln, flags=re.I):
                return ("COLLECT" if "COLLECT" in ln.upper() else "PREPAID"), [ln]
            if i > 0 and re.search(r"\bFREIGHT\b", lines[i - 1], flags=re.I):
                val = "COLLECT" if "COLLECT" in ln.upper() else "PREPAID"
                return val, [lines[i - 1], ln]

    return None, []
def extract_bl_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    ln = _lines(text)

    warnings: List[str] = []
    missing: List[str] = []

    shipper_name, shipper_ev = _find_shipper(ln)
    consignee_name, consignee_cnpj, consignee_ev = _find_consignee_name_cnpj(ln)
    ncm, ncm_ev, ncm_warn = _find_ncm(ln)
    gross_weight, gross_ev = _find_gross_weight(ln)
    measurement_m3, m3_ev = _find_measurement_m3(ln)
    freight_terms, freight_ev = _find_freight_terms(ln)
    notify_party, notify_ev = _find_notify_party(ln)
    port_loading, port_discharge, port_ev = _find_ports(ln)

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
    fields["consignee_name"] = _mk_field(consignee_name, required["importer_name"], consignee_ev[:1], "alias")
    fields["importer_cnpj"] = _mk_field(consignee_cnpj, required["importer_cnpj"], consignee_ev, "regex")
    fields["consignee_cnpj"] = _mk_field(consignee_cnpj, required["importer_cnpj"], consignee_ev, "alias")
    fields["ncm"] = _mk_field(ncm, required["ncm"], ncm_ev, "regex")
    fields["ncm_or_hs"] = _mk_field(ncm, required["ncm"], ncm_ev, "alias")
    fields["gross_weight_kg"] = _mk_field(gross_weight, required["gross_weight_kg"], gross_ev, "regex")
    fields["freight_terms"] = _mk_field(freight_terms, False, freight_ev, "regex")
    fields["freight_term"] = _mk_field(freight_terms, False, freight_ev, "alias")
    fields["measurement_m3"] = _mk_field(measurement_m3, False, m3_ev, "regex")
    fields["notify_party"] = _mk_field(notify_party, False, notify_ev, "regex")
    fields["port_of_loading"] = _mk_field(port_loading, False, port_ev, "regex")
    fields["port_of_discharge"] = _mk_field(port_discharge, False, port_ev, "regex")

    for k, meta in fields.items():
        if meta["required"] and not meta["present"]:
            missing.append(k)

    return fields, missing, warnings
