# invoice.py
import re

try:
    from .common import (
        build_field,
        find_first,
        find_all,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
        find_incoterm,
    )
except ImportError:  # pragma: no cover
    from common import (
        build_field,
        find_first,
        find_all,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
        find_incoterm,
    )

RE_INVOICE_NO = re.compile(r"(?is)\bINVOICE\s*NO\.?\s*[:\-]?\s*([A-Z0-9\-\/]+)\b")
RE_ANY_DOC_NO = re.compile(r"(?is)\b([A-Z]{1,4}-\d{3,8})\b")  # fallback tipo DN-24139
RE_DATE = re.compile(r"(?is)\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s+\d{1,2},\s*\d{4}\b")
RE_CURRENCY = re.compile(r"(?is)\bCURRENCY\b\s*[:\-]?\s*([A-Z]{3})\b")
RE_PAYMENT = re.compile(r"(?is)\b(ADVANCE\s+PAYMENT|PAYMENT\s+TERMS?.{0,40})\b")
RE_NET_WEIGHT = re.compile(r"(?is)\bNET\s*WEIGHT\b\s*[:\-]?\s*([0-9\.,]+)\s*KGS?\b")
RE_GROSS_WEIGHT = re.compile(r"(?is)\bGROSS\s*WEIGHT\b\s*[:\-]?\s*([0-9\.,]+)\s*KGS?\b")

RE_COUNTRY_ORIGIN = re.compile(r"(?is)\bCOUNTRY\s+OF\s+ORIGIN\b\s*[:\-]?\s*([A-Z][A-Z ]{2,})\b")
RE_COUNTRY_ACQ = re.compile(r"(?is)\bCOUNTRY\s+OF\s+ACQUISITION\b\s*[:\-]?\s*([A-Z][A-Z ]{2,})\b")
RE_COUNTRY_PROV = re.compile(r"(?is)\bCOUNTRY\s+OF\s+PROVENANCE\b\s*[:\-]?\s*([A-Z][A-Z ]{2,})\b")

RE_FREIGHT_WORDS = re.compile(r"(?is)\b(FREIGHT|INSURANCE|CHARGES?|EXPENSES?)\b")

RE_SHIPPER_HINT = re.compile(r"(?is)\b(SUZUKI[^\n]{0,80})\b")

# linha de item: "* DF250TX 2 UNITS @1,213,400.- 2,426,800.-"
RE_ITEM_LINE = re.compile(
    r"(?im)^\s*\*\s*([A-Z0-9]+)\s+(\d+)\s+UNITS?\s+@([0-9\.,]+)\s*-\.\s+([0-9\.,]+)\s*-\."
)

def extract_invoice_fields(text: str):
    warnings: list[str] = []
    fields: dict = {}

    inv_no, ev = find_first(RE_INVOICE_NO, text)
    if not inv_no:
        # fallback DN-24139
        inv_no2, ev2 = find_first(RE_ANY_DOC_NO, text)
        inv_no, ev = inv_no2, ev2
    fields["invoice_number"] = build_field(bool(inv_no), True, inv_no, [ev] if ev else [], "regex")

    inv_date, ev = find_first(RE_DATE, text, group=0)
    fields["invoice_date"] = build_field(bool(inv_date), True, inv_date, [ev] if ev else [], "regex")

    pay, ev = find_first(RE_PAYMENT, text, group=1)
    fields["payment_terms"] = build_field(bool(pay), True, pay, [ev] if ev else [], "regex")

    # importer/consignee name/cnpj (Karina: CNPJ obrigatório)
    importer_name, ev_name = find_company_line_before_cnpj(text)
    fields["importer_name"] = build_field(bool(importer_name), True, importer_name, [ev_name] if ev_name else [], "heuristic_line_before_cnpj")

    cnpj, ev = find_cnpj(text)
    fields["importer_cnpj"] = build_field(bool(cnpj), True, cnpj, [ev] if ev else [], "regex")
    # alias padronizado (para stage 3 depois)
    fields["consignee_cnpj"] = build_field(bool(cnpj), True, cnpj, [ev] if ev else [], "alias")

    # shipper/exporter (heurística simples por enquanto)
    shipper, ev = find_first(RE_SHIPPER_HINT, text)
    fields["shipper_name"] = build_field(bool(shipper), True, shipper, [ev] if ev else [], "heuristic_regex")

    cur, ev = find_first(RE_CURRENCY, text)
    fields["currency"] = build_field(bool(cur), True, cur, [ev] if ev else [], "regex")

    inc, ev = find_incoterm(text)
    fields["incoterm"] = build_field(bool(inc), True, inc, [ev] if ev else [], "regex_incoterm")

    coo, ev = find_first(RE_COUNTRY_ORIGIN, text)
    fields["country_of_origin"] = build_field(bool(coo), True, coo, [ev] if ev else [], "regex")

    coa, ev = find_first(RE_COUNTRY_ACQ, text)
    fields["country_of_acquisition"] = build_field(bool(coa), True, coa, [ev] if ev else [], "regex")

    cop, ev = find_first(RE_COUNTRY_PROV, text)
    fields["country_of_provenance"] = build_field(bool(cop), True, cop, [ev] if ev else [], "regex")

    nw_raw, ev = find_first(RE_NET_WEIGHT, text)
    nw = parse_mixed_number(nw_raw) if nw_raw else None
    fields["net_weight_kg"] = build_field(nw is not None, True, nw, [ev] if ev else [], "regex_number")

    gw_raw, ev = find_first(RE_GROSS_WEIGHT, text)
    gw = parse_mixed_number(gw_raw) if gw_raw else None
    fields["gross_weight_kg"] = build_field(gw is not None, True, gw, [ev] if ev else [], "regex_number")

    # “freight and other expenses” (pode estar ausente mesmo — vira “missing real”)
    freight_present = bool(RE_FREIGHT_WORDS.search(text or ""))
    fields["freight_and_expenses"] = build_field(freight_present, True, freight_present, [], "keyword_scan")

    # Itens (ajuda para comparação futura)
    items = []
    for m in RE_ITEM_LINE.finditer(text or ""):
        model = m.group(1)
        qty = int(m.group(2))
        unit = parse_mixed_number(m.group(3))
        amt = parse_mixed_number(m.group(4))
        items.append({
            "model": model,
            "qty": qty,
            "unit_price": unit,
            "amount": amt,
            "evidence": m.group(0).strip(),
        })
    fields["line_items"] = build_field(bool(items), False, items if items else None, [i["evidence"] for i in items[:5]], "regex_items")

    return fields, warnings
