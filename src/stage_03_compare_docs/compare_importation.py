# -*- coding: utf-8 -*-
"""
Stage 03 - IMPORTATION - Compare extracted fields between documents

Input : data/output/stage_02_fields/importation/*_fields.json
Output: data/output/stage_03_compare/importation/_stage03_comparison.json

Rules (aligned with Karina):
- Compare Invoice <-> Packing List (core fields)
- Compare (Invoice/Packing) <-> BL/HBL (core fields + consignee CNPJ)
- Cross-doc global checks:
  - shipper/exporter must exist in Invoice, PL, BL and be equal (soft-fuzzy)
  - consignee CNPJ must exist (Brazilian) and be equal across docs
- Incoterm vs Freight mode compatibility:
  - FOB/FCA/EXW -> tends to COLLECT
  - CFR/CIF/CPT/CIP/DAP/DPU/DDP -> tends to PREPAID
  (kept as "rule_check" with status match/divergent/skipped)

No external deps (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ------------------------
# Utilities
# ------------------------

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def is_blank(v: Any) -> bool:
    return v is None or v == "" or v == []


def norm_str(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip().upper()
    s = s.replace("Ç", "C")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^A-Z0-9 ]+", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def digits_only(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"\D+", "", str(v))


def to_float(v: Any) -> Optional[float]:
    """
    Robust float parsing:
    - "9,825.000" -> 9825.0
    - "7,980.00" -> 7980.0
    - "1,002" -> 1002.0
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip()
    if not s:
        return None

    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None

    last_comma = s.rfind(",")
    last_dot = s.rfind(".")

    if last_comma != -1 and last_dot != -1:
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        if "," in s and "." not in s:
            groups = s.split(",")
            if len(groups) >= 2 and all(g.isdigit() for g in groups):
                if len(groups[-1]) == 3:
                    s = "".join(groups)
                else:
                    s = s.replace(",", ".")
            else:
                s = s.replace(",", ".")
        elif "." in s and s.count(".") > 1:
            parts = s.split(".")
            if len(parts[-1]) == 3:
                s = "".join(parts)
            else:
                s = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(s)
    except ValueError:
        return None


def num_close(a: float, b: float, abs_tol: float = 0.5, rel_tol: float = 0.01) -> bool:
    diff = abs(a - b)
    if diff <= abs_tol:
        return True
    denom = max(abs(a), abs(b), 1.0)
    return (diff / denom) <= rel_tol


def token_overlap_close(a: Any, b: Any, min_jaccard: float = 0.55) -> bool:
    """
    Better for OCR-noisy company names:
    - compares token sets with Jaccard similarity.
    """
    na = norm_str(a)
    nb = norm_str(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta = set(na.split())
    tb = set(nb.split())
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    j = inter / union if union else 0.0
    if j >= min_jaccard:
        return True
    # fallback: containment
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    return short in long_


def cnpj_close(a: Any, b: Any) -> bool:
    da = digits_only(a)
    db = digits_only(b)
    if not da or not db:
        return False
    # allow formatted vs unformatted; require exact digits
    return da == db


def docref_close(a: Any, b: Any) -> bool:
    """
    For Invoice/PL numbers:
    Example: DN-24139  == DN-24139-P  (accept trailing P or -P)
    """
    da = re.sub(r"[^A-Z0-9]", "", norm_str(a))
    db = re.sub(r"[^A-Z0-9]", "", norm_str(b))
    if not da or not db:
        return False
    if da == db:
        return True

    def strip_trailing_p(x: str) -> str:
        return x[:-1] if x.endswith("P") else x

    da2 = strip_trailing_p(da)
    db2 = strip_trailing_p(db)
    return da2 == db2


def code_close_prefix(a: Any, b: Any) -> bool:
    """
    For NCM/HS:
    - accept prefix match if one side has 4/6 digits and other has 8.
    """
    da = digits_only(a)
    db = digits_only(b)
    if not da or not db:
        return False
    if da == db:
        return True
    short, long_ = (da, db) if len(da) <= len(db) else (db, da)
    if len(short) in (4, 6) and long_.startswith(short):
        return True
    return False


def list_to_set(v: Any) -> set:
    if v is None:
        return set()
    if isinstance(v, list):
        return {norm_str(x) for x in v if norm_str(x)}
    return {norm_str(v)} if norm_str(v) else set()


def _evidence_from_locations(field_obj: dict) -> List[str]:
    ev = []
    for loc in (field_obj.get("locations") or []):
        snip = loc.get("snippet")
        if snip:
            ev.append(str(snip))
    return ev


def get_field(doc: dict, key: str) -> Tuple[Any, List[str]]:
    """
    Supports Stage 02 variants:
    - fields[key] with {"value": ..., "evidence":[...]}
    - fields[key] with {"value": ..., "locations":[{"snippet":...}, ...]}
    """
    fields = doc.get("fields")
    if isinstance(fields, dict) and key in fields:
        f = fields.get(key) or {}
    else:
        # fallback if some Stage02 puts fields at root
        f = doc.get(key) if isinstance(doc.get(key), dict) else {}

    if not isinstance(f, dict):
        return None, []

    v = f.get("value")
    ev = f.get("evidence") or _evidence_from_locations(f)
    return v, ev


def get_field_any(doc: dict, keys: List[str]) -> Tuple[Any, List[str], Optional[str]]:
    for k in keys:
        v, ev = get_field(doc, k)
        if not is_blank(v):
            return v, ev, k
    return None, [], None


# ------------------------
# Incoterm vs Freight mode
# ------------------------

def norm_incoterm(v: Any) -> str:
    s = norm_str(v)
    s = s.replace("INCOTERMS", "").strip()
    return s


def norm_freight_mode(v: Any) -> str:
    s = norm_str(v)
    if "COLLECT" in s:
        return "COLLECT"
    if "PREPAID" in s:
        return "PREPAID"
    return s


def expected_freight_mode_from_incoterm(incoterm: str) -> Optional[str]:
    it = norm_incoterm(incoterm)
    if not it:
        return None
    if it in ("FOB", "FCA", "EXW"):
        return "COLLECT"
    if it in ("CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"):
        return "PREPAID"
    return None


# ------------------------
# Comparison specs
# ------------------------

@dataclass
class CheckSpec:
    name: str
    kind: str                   # "number"|"string"|"set"|"code_prefix"|"cnpj"|"docref"
    a_keys: List[str]
    b_keys: List[str]
    abs_tol: float = 0.5
    rel_tol: float = 0.01


# IMPORTANT: Packing List weights are in *_total_calc in your Stage02 output.
INVOICE_VS_PACKING = [
    CheckSpec(
        name="Invoice vs Packing reference number",
        kind="docref",
        a_keys=["invoice_number"],
        b_keys=["invoice_number", "packing_list_number"],
    ),
    CheckSpec(
        name="Consignee name",
        kind="string",
        a_keys=["importer_name", "consignee_name"],
        b_keys=["consignee_name", "importer_name"],
    ),
    CheckSpec(
        name="Consignee CNPJ",
        kind="cnpj",
        a_keys=["importer_cnpj", "consignee_cnpj"],
        b_keys=["consignee_cnpj", "importer_cnpj"],
    ),
    CheckSpec(
        name="Gross weight (kg)",
        kind="number",
        a_keys=["gross_weight_kg"],
        b_keys=["gross_weight_kg_total_calc", "gross_weight_kg", "gross_weight_total_kg"],
        abs_tol=1.0,
        rel_tol=0.01,
    ),
    CheckSpec(
        name="Net weight (kg)",
        kind="number",
        a_keys=["net_weight_kg"],
        b_keys=["net_weight_kg_total_calc", "net_weight_kg", "net_weight_total_kg"],
        abs_tol=1.0,
        rel_tol=0.01,
    ),
]

PACKING_VS_BL = [
    CheckSpec(
        name="Consignee name",
        kind="string",
        a_keys=["consignee_name", "importer_name"],
        b_keys=["consignee_name", "importer_name"],
    ),
    CheckSpec(
        name="Consignee CNPJ",
        kind="cnpj",
        a_keys=["consignee_cnpj", "importer_cnpj"],
        b_keys=["consignee_cnpj", "importer_cnpj"],
    ),
    CheckSpec(
        name="Gross weight (kg)",
        kind="number",
        a_keys=["gross_weight_kg_total_calc", "gross_weight_kg", "gross_weight_total_kg"],
        b_keys=["gross_weight_kg"],
        abs_tol=1.0,
        rel_tol=0.01,
    ),
]

INVOICE_VS_BL = [
    CheckSpec(
        name="Consignee name",
        kind="string",
        a_keys=["importer_name", "consignee_name"],
        b_keys=["consignee_name", "importer_name"],
    ),
    CheckSpec(
        name="Consignee CNPJ",
        kind="cnpj",
        a_keys=["importer_cnpj", "consignee_cnpj"],
        b_keys=["consignee_cnpj", "importer_cnpj"],
    ),
    CheckSpec(
        name="Gross weight (kg)",
        kind="number",
        a_keys=["gross_weight_kg"],
        b_keys=["gross_weight_kg"],
        abs_tol=1.0,
        rel_tol=0.01,
    ),
]

DI_LI_VS_BASE = [
    CheckSpec(
        name="Invoice number",
        kind="docref",
        a_keys=["invoice_number"],
        b_keys=["invoice_number", "packing_list_number"],
    ),
    CheckSpec(
        name="Consignee name",
        kind="string",
        a_keys=["importer_name", "consignee_name"],
        b_keys=["importer_name", "consignee_name"],
    ),
    CheckSpec(
        name="Consignee CNPJ",
        kind="cnpj",
        a_keys=["importer_cnpj", "consignee_cnpj"],
        b_keys=["importer_cnpj", "consignee_cnpj"],
    ),
    CheckSpec(
        name="Gross weight (kg)",
        kind="number",
        a_keys=["gross_weight_kg"],
        b_keys=["gross_weight_kg", "gross_weight_kg_total_calc"],
        abs_tol=1.0,
        rel_tol=0.01,
    ),
]


# ------------------------
# Core compare
# ------------------------

def compare_pair(doc_a: dict, doc_b: dict, specs: List[CheckSpec], label: str) -> List[dict]:
    out: List[dict] = []

    for spec in specs:
        va, eva, a_used = get_field_any(doc_a, spec.a_keys)
        vb, evb, b_used = get_field_any(doc_b, spec.b_keys)

        missing_side = None
        if is_blank(va) and is_blank(vb):
            missing_side = "both"
        elif is_blank(va):
            missing_side = "a"
        elif is_blank(vb):
            missing_side = "b"

        if missing_side:
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "skipped",
                "reason": f"missing_on_{missing_side}",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": None if is_blank(va) else va,
                "b_value": None if is_blank(vb) else vb,
            })
            continue

        if spec.kind == "number":
            fa = to_float(va)
            fb = to_float(vb)
            if fa is None or fb is None:
                out.append({
                    "pair": label,
                    "check": spec.name,
                    "status": "skipped",
                    "reason": "not_numeric",
                    "a_key_used": a_used,
                    "b_key_used": b_used,
                    "a_value": va,
                    "b_value": vb,
                })
                continue

            ok = num_close(fa, fb, abs_tol=spec.abs_tol, rel_tol=spec.rel_tol)
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": fa,
                "b_value": fb,
                "tolerance": {"abs_tol": spec.abs_tol, "rel_tol": spec.rel_tol},
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

        if spec.kind == "string":
            ok = token_overlap_close(va, vb)
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": va,
                "b_value": vb,
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

        if spec.kind == "cnpj":
            ok = cnpj_close(va, vb)
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": va,
                "b_value": vb,
                "a_digits": digits_only(va),
                "b_digits": digits_only(vb),
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

        if spec.kind == "docref":
            ok = docref_close(va, vb)
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": va,
                "b_value": vb,
                "note": "accepts trailing '-P' / 'P' on one side",
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

        if spec.kind == "set":
            sa = list_to_set(va)
            sb = list_to_set(vb)
            if not sa or not sb:
                out.append({
                    "pair": label,
                    "check": spec.name,
                    "status": "skipped",
                    "reason": "empty_set",
                    "a_key_used": a_used,
                    "b_key_used": b_used,
                    "a_value": va,
                    "b_value": vb,
                })
                continue
            ok = sa == sb
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": sorted(list(sa))[:30],
                "b_value": sorted(list(sb))[:30],
                "diff": {
                    "a_minus_b": sorted(list(sa - sb))[:30],
                    "b_minus_a": sorted(list(sb - sa))[:30],
                },
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

        if spec.kind == "code_prefix":
            ok = code_close_prefix(va, vb)
            out.append({
                "pair": label,
                "check": spec.name,
                "status": "match" if ok else "divergent",
                "a_key_used": a_used,
                "b_key_used": b_used,
                "a_value": va,
                "b_value": vb,
                "note": "prefix_match_allowed_for_4_or_6_digits",
                "evidence": {"a": eva[:2], "b": evb[:2]},
            })
            continue

    return out


def pick_docs_by_kind(docs: List[dict]) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    for d in docs:
        kind = (d.get("source") or {}).get("doc_kind") or "unknown"
        by.setdefault(kind, []).append(d)
    return by


def doc_label(d: dict) -> str:
    src = d.get("source") or {}
    return src.get("original_file") or src.get("stage01_file") or (src.get("doc_kind") or "doc")


def get_doc_kind(d: dict) -> str:
    return (d.get("source") or {}).get("doc_kind") or "unknown"


def group_check_equal_string(name: str, docs: List[dict], aliases_by_kind: Dict[str, List[str]]) -> dict:
    items = []
    values_norm = []
    missing = []
    for d in docs:
        k = get_doc_kind(d)
        keys = aliases_by_kind.get(k, [])
        v, ev, used = get_field_any(d, keys) if keys else (None, [], None)
        lbl = doc_label(d)
        if is_blank(v):
            missing.append(lbl)
        items.append({
            "doc": lbl,
            "doc_kind": k,
            "key_used": used,
            "value": v,
            "evidence": (ev[:2] if ev else []),
        })
        values_norm.append(norm_str(v))

    present_values = [v for v in values_norm if v]
    if missing:
        status = "missing"
        reason = f"missing_in: {', '.join(missing)}"
    elif not present_values:
        status = "missing"
        reason = "no_values_found"
    else:
        base = present_values[0]
        ok = all(token_overlap_close(v, base) for v in present_values if v)
        status = "match" if ok else "divergent"
        reason = "all_equal_soft" if ok else "values_differ"

    return {
        "group_check": name,
        "status": status,
        "reason": reason,
        "items": items,
    }


def group_check_equal_cnpj(name: str, docs: List[dict], aliases_by_kind: Dict[str, List[str]]) -> dict:
    items = []
    values = []
    missing = []
    for d in docs:
        k = get_doc_kind(d)
        keys = aliases_by_kind.get(k, [])
        v, ev, used = get_field_any(d, keys) if keys else (None, [], None)
        lbl = doc_label(d)
        if is_blank(v):
            missing.append(lbl)
        dv = digits_only(v)
        items.append({
            "doc": lbl,
            "doc_kind": k,
            "key_used": used,
            "value": v,
            "digits": dv,
            "evidence": (ev[:2] if ev else []),
        })
        values.append(dv)

    present = [x for x in values if x]
    if missing:
        status = "missing"
        reason = f"missing_in: {', '.join(missing)}"
    elif not present:
        status = "missing"
        reason = "no_values_found"
    else:
        base = present[0]
        ok = all(x == base for x in present)
        status = "match" if ok else "divergent"
        reason = "all_equal" if ok else "digits_differ"

    return {
        "group_check": name,
        "status": status,
        "reason": reason,
        "items": items,
    }


def rule_check_incoterm_vs_freight_mode(invoice_docs: List[dict], bl_docs: List[dict]) -> List[dict]:
    out = []
    for inv in invoice_docs:
        for bl in bl_docs:
            inv_lbl = doc_label(inv)
            bl_lbl = doc_label(bl)

            inc, inc_ev, inc_k = get_field_any(inv, ["incoterm", "incoterms"])
            # IMPORTANT: your BL has freight_terms
            fm, fm_ev, fm_k = get_field_any(bl, ["freight_terms", "freight_mode", "freight", "freight_term"])

            if is_blank(inc) or is_blank(fm):
                out.append({
                    "rule_check": "incoterm_vs_freight_mode",
                    "pair": f"{inv_lbl} <> {bl_lbl}",
                    "status": "skipped",
                    "reason": "missing_incoterm_or_freight_mode",
                    "invoice_incoterm": inc,
                    "bl_freight_mode": fm,
                    "keys_used": {"invoice": inc_k, "bl": fm_k},
                    "evidence": {"invoice": inc_ev[:2], "bl": fm_ev[:2]},
                })
                continue

            expected = expected_freight_mode_from_incoterm(str(inc))
            actual = norm_freight_mode(fm)

            if expected is None:
                out.append({
                    "rule_check": "incoterm_vs_freight_mode",
                    "pair": f"{inv_lbl} <> {bl_lbl}",
                    "status": "skipped",
                    "reason": "incoterm_not_in_mapping",
                    "invoice_incoterm": inc,
                    "expected_mode": None,
                    "bl_freight_mode": fm,
                    "keys_used": {"invoice": inc_k, "bl": fm_k},
                    "evidence": {"invoice": inc_ev[:2], "bl": fm_ev[:2]},
                })
                continue

            ok = (actual == expected)
            out.append({
                "rule_check": "incoterm_vs_freight_mode",
                "pair": f"{inv_lbl} <> {bl_lbl}",
                "status": "match" if ok else "divergent",
                "invoice_incoterm": inc,
                "expected_mode": expected,
                "bl_freight_mode": actual,
                "keys_used": {"invoice": inc_k, "bl": fm_k},
                "evidence": {"invoice": inc_ev[:2], "bl": fm_ev[:2]},
            })

    return out


def pair_by_reference(invoices: List[dict], packings: List[dict]) -> List[Tuple[dict, dict]]:
    """
    Pair invoice with packing using:
      invoice.invoice_number  <-> packing.packing_list_number (docref_close)
    If can't pair, fallback to all pairs.
    """
    inv_list = []
    for inv in invoices:
        num, _, _ = get_field_any(inv, ["invoice_number"])
        inv_list.append((inv, num))

    pairs = []
    used_pl = set()
    for pl in packings:
        plnum, _, _ = get_field_any(pl, ["packing_list_number", "invoice_number"])
        if is_blank(plnum):
            continue
        for inv, invnum in inv_list:
            if not is_blank(invnum) and docref_close(invnum, plnum):
                pairs.append((inv, pl))
                used_pl.add(id(pl))

    if pairs:
        return pairs

    # fallback
    for inv, _ in inv_list:
        for pl in packings:
            pairs.append((inv, pl))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Pasta do Stage 02 importation (com *_fields.json)")
    ap.add_argument("--output", required=True, help="Pasta de saída do Stage 03 (comparação)")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in in_dir.glob("*_fields.json") if p.name != "_stage02_summary.json"])
    if not files:
        raise SystemExit(f"Nenhum *_fields.json encontrado em: {in_dir}")

    docs = [read_json(p) for p in files]
    by_kind = pick_docs_by_kind(docs)

    invoices = by_kind.get("invoice", []) + by_kind.get("commercial_invoice", [])
    packings = by_kind.get("packing_list", []) + by_kind.get("pl", [])
    bls = by_kind.get("bl", []) + by_kind.get("hbl", []) + by_kind.get("bill_of_lading", [])
    dis = by_kind.get("di", []) + by_kind.get("conferencia_di", [])
    lis = by_kind.get("li", []) + by_kind.get("conferencia_li", [])

    comparisons: List[dict] = []
    rule_checks: List[dict] = []
    group_checks: List[dict] = []
    meta_docs: List[dict] = []

    for d in docs:
        src = d.get("source") or {}
        meta_docs.append({
            "doc_kind": src.get("doc_kind"),
            "original_file": src.get("original_file"),
            "stage02_file": src.get("stage01_file"),
            "missing_required_fields": d.get("missing_required_fields", []),
            "warnings": d.get("warnings", []),
        })

    # -------------------------
    # Pair comparisons
    # -------------------------

    for inv, pl in pair_by_reference(invoices, packings):
        label = f"invoice_vs_packing | {doc_label(inv)} <> {doc_label(pl)}"
        comparisons.extend(compare_pair(inv, pl, INVOICE_VS_PACKING, label))

    for bl in bls:
        for inv in invoices:
            label = f"invoice_vs_bl | {doc_label(inv)} <> {doc_label(bl)}"
            comparisons.extend(compare_pair(inv, bl, INVOICE_VS_BL, label))

        for pl in packings:
            label = f"packing_vs_bl | {doc_label(pl)} <> {doc_label(bl)}"
            comparisons.extend(compare_pair(pl, bl, PACKING_VS_BL, label))

    bases = invoices + packings + bls
    for di in dis:
        for base in bases:
            label = f"di_vs_base | {doc_label(di)} <> {doc_label(base)}"
            comparisons.extend(compare_pair(di, base, DI_LI_VS_BASE, label))

    for li in lis:
        for base in bases:
            label = f"li_vs_base | {doc_label(li)} <> {doc_label(base)}"
            comparisons.extend(compare_pair(li, base, DI_LI_VS_BASE, label))

    # -------------------------
    # Group checks (Karina)
    # -------------------------

    core_docs_for_shipper = []
    core_docs_for_shipper.extend(invoices[:1] if invoices else [])
    core_docs_for_shipper.extend(packings[:1] if packings else [])
    core_docs_for_shipper.extend(bls[:1] if bls else [])

    if core_docs_for_shipper:
        group_checks.append(
            group_check_equal_string(
                name="shipper_exporter_equal_across_invoice_packing_bl",
                docs=core_docs_for_shipper,
                aliases_by_kind={
                    "invoice": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "commercial_invoice": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "packing_list": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "pl": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "bl": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "hbl": ["shipper_name", "exporter_name", "shipper", "exporter"],
                    "bill_of_lading": ["shipper_name", "exporter_name", "shipper", "exporter"],
                },
            )
        )

    core_docs_for_cnpj = []
    core_docs_for_cnpj.extend(invoices[:1] if invoices else [])
    core_docs_for_cnpj.extend(packings[:1] if packings else [])
    core_docs_for_cnpj.extend(bls[:1] if bls else [])

    if core_docs_for_cnpj:
        group_checks.append(
            group_check_equal_cnpj(
                name="consignee_cnpj_equal_across_invoice_packing_bl",
                docs=core_docs_for_cnpj,
                aliases_by_kind={
                    "invoice": ["importer_cnpj", "consignee_cnpj"],
                    "commercial_invoice": ["importer_cnpj", "consignee_cnpj"],
                    "packing_list": ["consignee_cnpj", "importer_cnpj"],
                    "pl": ["consignee_cnpj", "importer_cnpj"],
                    "bl": ["consignee_cnpj", "importer_cnpj"],
                    "hbl": ["consignee_cnpj", "importer_cnpj"],
                    "bill_of_lading": ["consignee_cnpj", "importer_cnpj"],
                },
            )
        )

    # -------------------------
    # Rule checks
    # -------------------------
    rule_checks.extend(rule_check_incoterm_vs_freight_mode(invoices, bls))

    # -------------------------
    # Summary
    # -------------------------
    total = len(comparisons)
    matches = sum(1 for c in comparisons if c["status"] == "match")
    divs = sum(1 for c in comparisons if c["status"] == "divergent")
    skipped = sum(1 for c in comparisons if c["status"] == "skipped")

    gc_total = len(group_checks)
    gc_div = sum(1 for g in group_checks if g["status"] == "divergent")
    gc_missing = sum(1 for g in group_checks if g["status"] == "missing")

    rc_total = len(rule_checks)
    rc_div = sum(1 for r in rule_checks if r.get("status") == "divergent")
    rc_skipped = sum(1 for r in rule_checks if r.get("status") == "skipped")

    out = {
        "generated_at": now_iso(),
        "flow": "importation",
        "input_folder": str(in_dir),
        "documents": meta_docs,
        "summary": {
            "pair_checks": {
                "total": total,
                "matches": matches,
                "divergences": divs,
                "skipped": skipped,
            },
            "group_checks": {
                "total": gc_total,
                "divergences": gc_div,
                "missing": gc_missing,
            },
            "rule_checks": {
                "total": rc_total,
                "divergences": rc_div,
                "skipped": rc_skipped,
            },
        },
        "comparisons": comparisons,
        "group_checks": group_checks,
        "rule_checks": rule_checks,
    }

    out_path = out_dir / "_stage03_comparison.json"
    write_json(out_path, out)

    print("Concluído.")
    print(f"Saída: {out_path}")


if __name__ == "__main__":
    main()
