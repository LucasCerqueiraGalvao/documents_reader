# packing_list.py
import re

try:
    from .common import (
        build_field,
        find_first,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
    )
except ImportError:  # pragma: no cover
    from common import (
        build_field,
        find_first,
        find_cnpj,
        find_company_line_before_cnpj,
        parse_mixed_number,
    )

RE_ANY_DOC_NO = re.compile(r"(?is)\b([A-Z]{1,4}-\d{3,8}(?:-P)?)\b")  # DN-24139-P
RE_TOTAL_UNITS_CARTONS = re.compile(r"(?is)\b(\d+)\s+UNITS?\s*/\s*(\d+)\s+CARTONS?\b")
RE_MODEL = re.compile(r"(?im)^\s*\*\s*MODEL:\s*([A-Z0-9]+)\b")
# linha principal: "19 - 21 3 CARTONS @199 @264 1.754325 5.263"
RE_ROW = re.compile(
    r"(?im)^\s*(\d+\s*-\s*\d+)\s+(\d+)\s+CARTONS?\s+@([0-9\.,]+)\s+@([0-9\.,]+)\s+([0-9\.,]+)\s+([0-9\.,]+)\s*$"
)
# linha de totais logo abaixo: "597 792"
RE_TOTAL_LINE = re.compile(r"(?im)^\s*([0-9\.,]+)\s+([0-9\.,]+)\s*$")

RE_SHIPPER_HINT = re.compile(r"(?is)\b(SUZUKI[^\n]{0,80})\b")

def extract_packing_list_fields(text: str):
    warnings: list[str] = []
    fields: dict = {}

    docno, ev = find_first(RE_ANY_DOC_NO, text)
    fields["packing_list_number"] = build_field(bool(docno), True, docno, [ev] if ev else [], "regex")

    # shipper/exporter
    m = RE_SHIPPER_HINT.search(text or "")
    shipper = m.group(1).strip() if m else None
    fields["shipper_name"] = build_field(bool(shipper), True, shipper, [m.group(0)] if m else [], "heuristic_regex")

    # consignee/importer
    consignee_name, ev_name = find_company_line_before_cnpj(text)
    fields["consignee_name"] = build_field(bool(consignee_name), True, consignee_name, [ev_name] if ev_name else [], "heuristic_line_before_cnpj")

    cnpj, ev = find_cnpj(text)
    fields["consignee_cnpj"] = build_field(bool(cnpj), True, cnpj, [ev] if ev else [], "regex")

    # totals (unidades/cartons)
    tu, tc = None, None
    m2 = RE_TOTAL_UNITS_CARTONS.search(text or "")
    if m2:
        tu = int(m2.group(1))
        tc = int(m2.group(2))
    fields["total_units"] = build_field(tu is not None, True, tu, [m2.group(0)] if m2 else [], "regex")
    fields["total_cartons"] = build_field(tc is not None, True, tc, [m2.group(0)] if m2 else [], "regex")

    # parse por modelo (usa seu formato: MODEL + linha com @net @gross + m3)
    lines = (text or "").splitlines()
    current_model = None
    items = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m_model = RE_MODEL.match(line)
        if m_model:
            current_model = m_model.group(1)
            i += 1
            continue

        m_row = RE_ROW.match(line)
        if m_row and current_model:
            carton_range = m_row.group(1).replace(" ", "")
            cartons = int(m_row.group(2))
            net_pkg = parse_mixed_number(m_row.group(3))
            gross_pkg = parse_mixed_number(m_row.group(4))
            m3_pkg = parse_mixed_number(m_row.group(5))
            m3_total = parse_mixed_number(m_row.group(6))

            # tenta ler a próxima linha como total net/gross
            net_total = None
            gross_total = None
            ev_total = None
            if i + 1 < len(lines):
                m_tot = RE_TOTAL_LINE.match(lines[i + 1].strip())
                if m_tot:
                    net_total = parse_mixed_number(m_tot.group(1))
                    gross_total = parse_mixed_number(m_tot.group(2))
                    ev_total = m_tot.group(0)
                    i += 1  # consome linha total

            items.append({
                "model": current_model,
                "carton_range": carton_range,
                "cartons": cartons,
                "net_weight_per_pkg_kg": net_pkg,
                "gross_weight_per_pkg_kg": gross_pkg,
                "measurement_per_pkg_m3": m3_pkg,
                "measurement_total_m3": m3_total,
                "net_weight_total_kg": net_total,
                "gross_weight_total_kg": gross_total,
                "evidence_row": m_row.group(0),
                "evidence_totals": ev_total,
            })
        i += 1

    fields["items"] = build_field(bool(items), False, items if items else None, [it["evidence_row"] for it in items[:5]], "regex_items")

    # totais calculados (se tiver totals em cada item)
    net_sum = 0.0
    gross_sum = 0.0
    has_totals = False
    for it in items:
        if it.get("net_weight_total_kg") is not None and it.get("gross_weight_total_kg") is not None:
            net_sum += float(it["net_weight_total_kg"])
            gross_sum += float(it["gross_weight_total_kg"])
            has_totals = True

    fields["net_weight_kg_total_calc"] = build_field(has_totals, False, net_sum if has_totals else None, [], "calculated_sum")
    fields["gross_weight_kg_total_calc"] = build_field(has_totals, False, gross_sum if has_totals else None, [], "calculated_sum")

    return fields, warnings
