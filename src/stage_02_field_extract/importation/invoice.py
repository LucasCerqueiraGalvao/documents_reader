# invoice.py
import re

try:
    from .common import (
        build_field,
        find_first,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
        find_incoterm,
        normalize_spaces,
    )
except ImportError:  # pragma: no cover
    from common import (
        build_field,
        find_first,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
        find_incoterm,
        normalize_spaces,
    )


RE_INVOICE_NO = re.compile(
    r"(?is)\bINVOICE\s*(?:NO|N[Oº°\.]?)\s*[:\-]?\s*([A-Z0-9\-\/]+)\b"
)
RE_INVOICE_NO_FATTURA = re.compile(
    r"(?is)\b(?:FATTURA(?:\s+ACCOMPAGNATORIA)?|INVOICE(?:_EXT)?)\b[\s\S]{0,240}?\bN\.?\s*[:\-]?\s*([A-Z0-9\-\/]{1,30})\b"
)
RE_INVOICE_NO_NEAR_DATE = re.compile(
    r"(?is)\bN\.?\s*[:\-]?\s*([A-Z0-9\-\/]{1,30})\s*(?:\r?\n|\s){0,20}\b(?:DATA|DATE|DATA/DATE)\b"
)
RE_ANY_DOC_NO = re.compile(r"(?is)\b([A-Z]{1,6}-\d{3,10})\b")

RE_DATE_MONTH = re.compile(
    r"(?is)\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s+\d{1,2},\s*\d{4}\b"
)
RE_DATE_DMY_LABEL = re.compile(
    r"(?is)\b(?:DATA|DATE|DATA/DATE)\b\s*[:\-]?\s*([0-3]?\d/[01]?\d/(?:19|20)\d{2})\b"
)
RE_DATE_DMY = re.compile(r"\b([0-3]?\d/[01]?\d/(?:19|20)\d{2})\b")

RE_CURRENCY = re.compile(r"(?is)\bCURRENCY\b\s*[:\-]?\s*([A-Z]{3})\b")
RE_CURRENCY_CODE = re.compile(r"(?is)\b(EUR|USD|BRL|JPY|CNY|GBP|CHF)\b")
RE_PAYMENT_INLINE = re.compile(r"(?is)\b(ADVANCE\s+PAYMENT)\b")
RE_PAYMENT_LABEL_NEXT = re.compile(
    r"(?is)\b(?:PAYMENT\s+TERMS|CONDIZIONI\s+DI\s+PAGAMENTO)\b[^\n\r]*[\r\n]+([^\n\r]{3,120})"
)

RE_NET_WEIGHT = re.compile(r"(?is)\bNET\s*WEIGHT\b\s*[:\-]?\s*([0-9\.,]+)\s*KGS?\b")
RE_GROSS_WEIGHT = re.compile(
    r"(?is)\bGROSS\s*WEIGHT\b\s*[:\-]?\s*([0-9\.,]+)\s*KGS?\b"
)
RE_WEIGHT_GENERIC = re.compile(
    r"(?is)\b(?:PESO|WEIGHT)\b[\s\S]{0,80}\bKGS?\b[\s:]*([0-9][0-9\.,]{0,20})"
)

RE_TOTAL_QTY = re.compile(
    r"(?is)\b(TOTAL\s+(?:QTY|QUANTITY|UNITS))\b\s*[:\-]?\s*([0-9\.,]+)\b"
)

RE_COUNTRY_ORIGIN = re.compile(
    r"(?im)^\s*COUNTRY\s+OF\s+ORIGIN\s*[:\-]?\s*([A-Z][A-Z ]{2,40})\s*$"
)
RE_COUNTRY_ACQ = re.compile(
    r"(?im)^\s*COUNTRY\s+OF\s+ACQUISITION\s*[:\-]?\s*([A-Z][A-Z ]{2,40})\s*$"
)
RE_COUNTRY_PROV = re.compile(
    r"(?im)^\s*COUNTRY\s+OF\s+PROVENANCE\s*[:\-]?\s*([A-Z][A-Z ]{2,40})\s*$"
)

RE_FREIGHT_WORDS = re.compile(r"(?is)\b(FREIGHT|INSURANCE|CHARGES?|EXPENSES?)\b")
RE_SHIPPER_HINT = re.compile(r"(?is)\b((?:SUZUKI|INJECTA)[^\n]{0,120})\b")

# Old style line-items support (kept for backward compatibility)
RE_ITEM_LINE = re.compile(
    r"(?im)^\s*\*\s*([A-Z0-9]+)\s+(\d+)\s+UNITS?\s+@([0-9\.,]+)\s*-\.\s+([0-9\.,]+)\s*-\."
)


def _normalize_invoice_token(v: str) -> str:
    v = normalize_spaces(v or "").strip()
    v = re.sub(r"^[\.\-:/\s]+|[\.\-:/\s]+$", "", v)
    return v


def _is_valid_invoice_number(v: str) -> bool:
    if not v:
        return False
    t = _normalize_invoice_token(v)
    if len(t) < 2:
        return False
    if t.upper() in {"N", "NO", "O", "INVOICE"}:
        return False
    # Invoice ids in our docs always carry at least one digit.
    if not re.search(r"\d", t):
        return False
    return True


def _is_section_noise(line: str) -> bool:
    if not line:
        return True
    return bool(
        re.search(
            r"(?i)\b(SHIP\s*TO|BILL\s*TO|COD\.?\s*CLIENTE|COD\.?\s*FISCALE|P\.?\s*IVA|ZIP|PAGINA|PAGE|DATA/DATE|TIPO/TYPE|PAYMENT\s+TERMS|INVOICE_EXT|DESCRIZIONE|DESCRIPTION|ARTICOLO|VAT|IBAN|SWIFT|AGENTE|Banca|BANCA)\b",
            line,
        )
    )


def _looks_company_name(line: str) -> bool:
    line = normalize_spaces(line or "").strip(" -:\t")
    if not line:
        return False
    if len(line) < 6:
        return False
    if "http://" in line.lower() or "https://" in line.lower() or "@" in line:
        return False
    if _is_section_noise(line):
        return False
    if re.search(r"(?i)\b(RUA|VIA|ZIP|BRAZIL|LOUVEIRA|MONZA|RIETI)\b", line):
        return False
    alpha_words = re.findall(r"[A-Za-z]{2,}", line)
    if len(alpha_words) < 2:
        return False
    legal = re.search(
        r"(?i)\b(LTDA|LTD|S\.?\s*P\.?\s*A\.?|S\.?\s*R\.?\s*L\.?|S\.?\s*A\.?|INC|CORP|GMBH|CO\.?)\b",
        line,
    )
    return bool(legal) or line == line.upper()


def _find_invoice_number(text: str):
    for rx in (
        RE_INVOICE_NO,
        RE_INVOICE_NO_FATTURA,
        RE_INVOICE_NO_NEAR_DATE,
        RE_ANY_DOC_NO,
    ):
        for m in rx.finditer(text or ""):
            val = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            val = _normalize_invoice_token(val)
            if _is_valid_invoice_number(val):
                return val, m.group(0)
    return None, None


def _find_invoice_date(text: str):
    d, ev = find_first(RE_DATE_DMY_LABEL, text)
    if d:
        return d, ev
    d2, ev2 = find_first(RE_DATE_MONTH, text, group=0)
    if d2:
        return d2, ev2
    d3, ev3 = find_first(RE_DATE_DMY, text)
    return d3, ev3


def _find_payment_terms(text: str):
    v, ev = find_first(RE_PAYMENT_LABEL_NEXT, text)
    if v:
        return normalize_spaces(v), ev
    v2, ev2 = find_first(RE_PAYMENT_INLINE, text, group=1)
    if v2:
        return normalize_spaces(v2), ev2
    return None, None


def _find_currency(text: str):
    cur, ev = find_first(RE_CURRENCY, text)
    if cur:
        return cur.upper(), ev
    cur2, ev2 = find_first(RE_CURRENCY_CODE, text)
    if cur2:
        return cur2.upper(), ev2
    if "€" in text or "â‚¬" in text:
        return "EUR", "€"
    return None, None


def _find_importer_name(text: str):
    lines = [normalize_spaces(x).strip() for x in (text or "").splitlines()]

    # Prefer BILL TO block
    for i, line in enumerate(lines):
        if re.search(r"(?i)\bBILL\s*TO\b", line):
            for j in range(i + 1, min(i + 16, len(lines))):
                cand = lines[j]
                if not cand or _is_section_noise(cand):
                    continue
                if re.fullmatch(r"[A-Z]?\d{2,}", cand):
                    continue
                if _looks_company_name(cand):
                    return cand, cand

    # Generic "line before CNPJ/P.IVA" heuristic
    name0, ev0 = find_company_line_before_cnpj(text)
    if name0:
        return name0, ev0

    # Try around tax-id labels
    for i, line in enumerate(lines):
        if re.search(r"(?i)\b(CNPJ|P\.?\s*IVA|COD\.?\s*FISCALE)\b", line):
            near = [i - 2, i - 1, i + 1, i + 2]
            for k in near:
                if 0 <= k < len(lines):
                    cand = lines[k]
                    if _looks_company_name(cand):
                        return cand, cand

    return None, None


def _find_shipper_name(text: str):
    # Existing hint for legacy docs
    shipper, ev = find_first(RE_SHIPPER_HINT, text)
    if shipper:
        return normalize_spaces(shipper), ev

    lines = [normalize_spaces(x).strip() for x in (text or "").splitlines()]
    ship_to_idx = None
    for i, line in enumerate(lines):
        if re.search(r"(?i)\bSHIP\s*TO\b", line):
            ship_to_idx = i
            break

    if ship_to_idx is not None:
        for k in range(ship_to_idx - 1, max(-1, ship_to_idx - 10), -1):
            cand = lines[k]
            if _looks_company_name(cand):
                return cand, cand

    # fallback: top-of-document company-like line
    for cand in lines[:12]:
        if _looks_company_name(cand):
            return cand, cand

    return None, None


def extract_invoice_fields(text: str):
    warnings: list[str] = []
    fields: dict = {}

    inv_no, ev = _find_invoice_number(text)
    fields["invoice_number"] = build_field(
        bool(inv_no), True, inv_no, [ev] if ev else [], "regex"
    )

    inv_date, ev = _find_invoice_date(text)
    fields["invoice_date"] = build_field(
        bool(inv_date), True, inv_date, [ev] if ev else [], "regex"
    )

    pay, ev = _find_payment_terms(text)
    fields["payment_terms"] = build_field(
        bool(pay), True, pay, [ev] if ev else [], "regex"
    )

    importer_name, ev_name = _find_importer_name(text)
    fields["importer_name"] = build_field(
        bool(importer_name),
        True,
        importer_name,
        [ev_name] if ev_name else [],
        "heuristic_importer_block",
    )

    cnpj, ev = find_cnpj(text)
    fields["importer_cnpj"] = build_field(
        bool(cnpj), True, cnpj, [ev] if ev else [], "regex_tax_id"
    )
    fields["consignee_cnpj"] = build_field(
        bool(cnpj), True, cnpj, [ev] if ev else [], "alias"
    )

    shipper, ev = _find_shipper_name(text)
    fields["shipper_name"] = build_field(
        bool(shipper), True, shipper, [ev] if ev else [], "heuristic_shipper"
    )

    cur, ev = _find_currency(text)
    fields["currency"] = build_field(bool(cur), True, cur, [ev] if ev else [], "regex")

    inc, ev = find_incoterm(text)
    fields["incoterm"] = build_field(
        bool(inc), True, inc, [ev] if ev else [], "regex_incoterm"
    )

    coo, ev = find_first(RE_COUNTRY_ORIGIN, text)
    fields["country_of_origin"] = build_field(
        bool(coo), False, coo, [ev] if ev else [], "regex"
    )

    coa, ev = find_first(RE_COUNTRY_ACQ, text)
    fields["country_of_acquisition"] = build_field(
        bool(coa), False, coa, [ev] if ev else [], "regex"
    )

    cop, ev = find_first(RE_COUNTRY_PROV, text)
    fields["country_of_provenance"] = build_field(
        bool(cop), False, cop, [ev] if ev else [], "regex"
    )

    nw_raw, ev_nw = find_first(RE_NET_WEIGHT, text)
    gw_raw, ev_gw = find_first(RE_GROSS_WEIGHT, text)
    nw = parse_mixed_number(nw_raw) if nw_raw else None
    gw = parse_mixed_number(gw_raw) if gw_raw else None

    if nw is None and gw is None:
        one_raw, one_ev = find_first(RE_WEIGHT_GENERIC, text)
        one_val = parse_mixed_number(one_raw) if one_raw else None
        if one_val is not None:
            nw = one_val
            gw = one_val
            ev_nw = one_ev
            ev_gw = one_ev
            warnings.append("invoice_single_weight_used_for_net_and_gross")

    if nw is None and gw is not None:
        nw = gw
        ev_nw = ev_gw
        warnings.append("invoice_net_weight_inferred_from_gross")
    if gw is None and nw is not None:
        gw = nw
        ev_gw = ev_nw
        warnings.append("invoice_gross_weight_inferred_from_net")

    fields["net_weight_kg"] = build_field(
        nw is not None, True, nw, [ev_nw] if ev_nw else [], "regex_number"
    )
    fields["gross_weight_kg"] = build_field(
        gw is not None, True, gw, [ev_gw] if ev_gw else [], "regex_number"
    )

    tq_raw, ev = find_first(RE_TOTAL_QTY, text, group=2)
    tq = parse_mixed_number(tq_raw) if tq_raw else None
    fields["total_quantity"] = build_field(
        tq is not None, False, tq, [ev] if ev else [], "regex_number"
    )

    freight_present = bool(RE_FREIGHT_WORDS.search(text or ""))
    fields["freight_and_expenses"] = build_field(
        freight_present, False, freight_present, [], "keyword_scan"
    )

    items = []
    for m in RE_ITEM_LINE.finditer(text or ""):
        model = m.group(1)
        qty = int(m.group(2))
        unit = parse_mixed_number(m.group(3))
        amt = parse_mixed_number(m.group(4))
        items.append(
            {
                "model": model,
                "qty": qty,
                "unit_price": unit,
                "amount": amt,
                "evidence": m.group(0).strip(),
            }
        )
    fields["line_items"] = build_field(
        bool(items),
        False,
        items if items else None,
        [i["evidence"] for i in items[:5]],
        "regex_items",
    )

    return fields, warnings
