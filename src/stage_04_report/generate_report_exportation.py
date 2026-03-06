# -*- coding: utf-8 -*-
"""
Stage 04 - Exportation - Generate final report (HTML/MD/JSON)

Inputs:
- Stage 01 (text extract): data/output/stage_01_text/exportation/*_extracted.json
- Stage 02 (fields extract): data/output/stage_02_fields/exportation/*_fields.json
- Stage 03 (compare): data/output/stage_03_compare/exportation/_stage03_comparison.json

Outputs:
- data/output/stage_04_report/exportation/_stage04_report.json
- data/output/stage_04_report/exportation/_stage04_report.md
- data/output/stage_04_report/exportation/_stage04_report.html

Goal:
- Consolidate per-document field checks (Stage02)
- Consolidate cross-document comparisons (Stage03)
- Produce a clean “final” report for users
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


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


def write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(content)


def tr(x: Any) -> str:
    """safe html text"""
    if x is None:
        return ""
    return html.escape(str(x))


def norm_spaces(s: str) -> str:
    s = s or ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_pair_companies(pair_text: Any) -> Tuple[str, str]:
    """
    Parse pair text into two document/company labels.
    Expected format: "<rule> | A <> B" or "A <> B".
    """
    s = (str(pair_text) if pair_text is not None else "").strip()
    if not s:
        return ("", "")

    rhs = s.split("|", 1)[1].strip() if "|" in s else s
    if "<>" in rhs:
        left, right = rhs.split("<>", 1)
        return (left.strip(), right.strip())

    m = re.match(r"(.+?)\s+(?:vs|x)\s+(.+)", rhs, flags=re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip())

    return (rhs, "")


def format_skip_reason(reason: Any, pair_text: Any) -> str:
    r = (str(reason) if reason is not None else "").strip()
    if not r:
        return ""

    doc_a, doc_b = split_pair_companies(pair_text)
    rl = r.lower()
    if rl == "missing_on_a":
        return f'missing on "{doc_a or "Documento A"}"'
    if rl == "missing_on_b":
        return f'missing on "{doc_b or "Documento B"}"'

    return r.replace("_", " ")


DOC_KIND_LABELS = {
    "commercial_invoice": "COMMERCIAL INVOICE",
    "packing_list": "PACKING LIST",
    "draft_bl": "DRAFT BL",
    "certificate_of_origin": "CERTIFICATE OF ORIGIN",
    "container_data": "CONTAINER DATA",
}
EXPECTED_DOC_KINDS = [
    ("commercial_invoice", "COMMERCIAL INVOICE"),
    ("packing_list", "PACKING LIST"),
    ("draft_bl", "DRAFT BL"),
    ("certificate_of_origin", "CERTIFICATE OF ORIGIN"),
    ("container_data", "CONTAINER DATA"),
]

def doc_kind_label(kind: Any) -> str:
    k = (str(kind) if kind is not None else "").strip().lower()
    if k in DOC_KIND_LABELS:
        return DOC_KIND_LABELS[k]
    return (str(kind) if kind is not None else "").upper()


def doc_kind_with_original(kind: Any, original_file: Any) -> str:
    label = doc_kind_label(kind)
    original = (str(original_file) if original_file is not None else "").strip()
    if not original:
        return label
    return f"{label} ({original})"


def expected_docs_rows(stage02_docs: List[dict]) -> List[Tuple[str, int, str]]:
    counts: Dict[str, int] = {}
    for d in stage02_docs:
        k = ((d.get("doc_kind") or "") if isinstance(d, dict) else "")
        k = str(k).strip().lower()
        if not k:
            continue
        counts[k] = counts.get(k, 0) + 1
    rows: List[Tuple[str, int, str]] = []
    for k, label in EXPECTED_DOC_KINDS:
        cnt = counts.get(k, 0)
        status = "OK" if cnt > 0 else "MISSING"
        rows.append((label, cnt, status))
    return rows

# ------------------------
# Load Stage01 quality info
# ------------------------


def extract_stage01_quality(stage01_dir: Path) -> Dict[str, Any]:
    """
    Reads *_extracted.json to understand extraction method (direct vs ocr),
    per page. Used only for quality summary in report.
    """
    out: Dict[str, Any] = {"documents": []}

    if not stage01_dir.exists():
        return out

    for p in sorted(stage01_dir.glob("*_extracted.json")):
        try:
            obj = read_json(p)
        except Exception:
            continue

        pages = obj.get("pages") or []
        direct = sum(1 for pg in pages if (pg.get("method") or "").lower() == "direct")
        ocr = sum(1 for pg in pages if (pg.get("method") or "").lower() == "ocr")

        out["documents"].append(
            {
                "file": obj.get("file") or p.name,
                "pages": len(pages),
                "direct_pages": direct,
                "ocr_pages": ocr,
            }
        )

    return out


# ------------------------
# Load Stage02 docs
# ------------------------


def load_stage02_docs(stage02_dir: Path) -> List[dict]:
    docs: List[dict] = []
    if not stage02_dir.exists():
        return docs

    for p in sorted(stage02_dir.glob("*_fields.json")):
        if p.name == "_stage02_summary.json":
            continue
        try:
            docs.append(read_json(p))
        except Exception:
            continue
    return docs


def build_stage02_section(stage02_docs: List[dict]) -> Dict[str, Any]:
    items: List[dict] = []
    for d in stage02_docs:
        src = d.get("source") or {}
        fields = d.get("fields") or {}
        missing = d.get("missing_required_fields") or []
        if not missing:
            missing = [
                k
                for k, v in fields.items()
                if (v or {}).get("required") is True and (v or {}).get("present") is not True
            ]
        warnings = d.get("warnings") or []

        required_total = sum(
            1 for k, v in fields.items() if (v or {}).get("required") is True
        )
        required_present = sum(
            1
            for k, v in fields.items()
            if (v or {}).get("required") is True and (v or {}).get("present") is True
        )
        fields_total = len(fields)
        fields_present = sum(1 for k, v in fields.items() if (v or {}).get("present") is True)

        status = "OK"
        if missing:
            status = "FAIL"
        elif warnings:
            status = "ALERT"

        items.append(
            {
                "doc_kind": src.get("doc_kind"),
                "original_file": src.get("original_file"),
                "stage01_file": src.get("stage01_file"),
                "status": status,
                "missing_required_fields": missing,
                "warnings": warnings,
                "required_present": required_present,
                "required_total": required_total,
                "fields_present": fields_present,
                "fields_total": fields_total,
                "fields_count": fields_total,
            }
        )

    # sort: FAIL -> ALERT -> OK
    order = {"FAIL": 0, "ALERT": 1, "OK": 2}
    items.sort(
        key=lambda x: (
            order.get(x["status"], 9),
            x.get("doc_kind") or "",
            x.get("original_file") or "",
        )
    )

    return {"documents": items}


# ------------------------
# Normalize Stage03
# ------------------------


def normalize_stage03(stage03_obj: dict) -> Dict[str, Any]:
    """
    Normaliza Stage 03 para um formato único, porque ao longo do projeto existiram
    2 formatos de saída:

    (A) Antigo:
      {
        "comparisons": [ { "pair": "...", "field": "...", "status": "...", ... }, ... ],
        "summary": { "total_checks": ..., "matches": ..., "divergences": ..., "skipped": ... }
      }

    (B) Novo:
      {
        "pairs":  [ { "pair": "...", "check": "...", "status": "...", ... }, ... ],
        "groups": [ ... ],
        "rules":  [ ... ],
        "summary": { "pairs": {...}, "groups": {...}, "rules": {...} }
      }

    Esse normalizador:
    - Garante as chaves: pairs, groups, rules, summary
    - Para itens de "pairs", garante a chave "field" (alias de "check").
    """

    def _ensure_field(item: dict) -> dict:
        # Stage03 antigo usa "field"; novo usa "check"
        if "field" not in item or not item.get("field"):
            if item.get("check"):
                item["field"] = item["check"]
            elif item.get("key"):
                item["field"] = item["key"]
            elif item.get("campo"):
                item["field"] = item["campo"]
        return item

    def _ensure_group_rule_field(item: dict, fallback_key: str) -> dict:
        # Para tabelas de grupos/regras, precisamos de um "field" para renderizar
        if "field" not in item or not item.get("field"):
            if item.get(fallback_key):
                item["field"] = item[fallback_key]
        return item

    # ---------
    # Formato A
    # ---------
    if "comparisons" in stage03_obj:
        pairs = [_ensure_field(x) for x in (stage03_obj.get("comparisons") or [])]
        groups = stage03_obj.get("groups") or stage03_obj.get("group_checks") or []
        rules = stage03_obj.get("rules") or stage03_obj.get("rule_checks") or []

        groups = [_ensure_group_rule_field(dict(x), "group_check") for x in groups]
        rules = [_ensure_group_rule_field(dict(x), "rule_check") for x in rules]

        summary = stage03_obj.get("summary") or {}

        # formato A legado: total_checks / matches / divergences / skipped
        if any(k in summary for k in ("total_checks", "matches", "divergences", "skipped")):
            return {
                "pairs": pairs,
                "groups": groups,
                "rules": rules,
                "summary": {
                    "total": int(summary.get("total_checks", 0) or 0),
                    "matches": int(summary.get("matches", 0) or 0),
                    "divergences": int(summary.get("divergences", 0) or 0),
                    "skipped": int(summary.get("skipped", 0) or 0),
                },
            }

        # formato h?brido: summary com pair_checks / group_checks / rule_checks
        def _read_pair(block: dict) -> tuple[int, int, int, int]:
            if not isinstance(block, dict):
                return (0, 0, 0, 0)
            total = int(block.get("total", 0) or 0)
            matches = int(block.get("matches", 0) or 0)
            divergences = int(block.get("divergences", 0) or 0)
            skipped = int(block.get("skipped", 0) or 0)
            if total and matches == 0 and (divergences or skipped):
                matches = max(total - divergences - skipped, 0)
            return (total, matches, divergences, skipped)

        def _read_group(block: dict) -> tuple[int, int, int, int]:
            if not isinstance(block, dict):
                return (0, 0, 0, 0)
            total = int(block.get("total", 0) or 0)
            divergences = int(block.get("divergences", 0) or 0)
            missing = int(block.get("missing", 0) or 0)
            matches = max(total - divergences - missing, 0) if total else 0
            skipped = missing
            return (total, matches, divergences, skipped)

        def _read_rule(block: dict) -> tuple[int, int, int, int]:
            if not isinstance(block, dict):
                return (0, 0, 0, 0)
            total = int(block.get("total", 0) or 0)
            divergences = int(block.get("divergences", 0) or 0)
            skipped = int(block.get("skipped", 0) or 0)
            matches = max(total - divergences - skipped, 0) if total else 0
            return (total, matches, divergences, skipped)

        t1, m1, d1, s1 = _read_pair(summary.get("pair_checks") or {})
        t2, m2, d2, s2 = _read_group(summary.get("group_checks") or {})
        t3, m3, d3, s3 = _read_rule(summary.get("rule_checks") or {})

        if (t1 + t2 + t3) > 0:
            return {
                "pairs": pairs,
                "groups": groups,
                "rules": rules,
                "summary": {
                    "total": (t1 + t2 + t3),
                    "matches": (m1 + m2 + m3),
                    "divergences": (d1 + d2 + d3),
                    "skipped": (s1 + s2 + s3),
                },
            }

        # fallback: calcula em cima das listas
        all_items = pairs + groups + rules
        total = len(all_items)
        matches = sum(1 for x in all_items if (x.get("status") in ("match", "ok", "pass")))
        divs = sum(1 for x in all_items if (x.get("status") in ("divergent", "fail", "error")))
        skps = sum(1 for x in all_items if (x.get("status") in ("skipped", "missing")))
        return {
            "pairs": pairs,
            "groups": groups,
            "rules": rules,
            "summary": {
                "total": total,
                "matches": matches,
                "divergences": divs,
                "skipped": skps,
            },
        }

    # -------------
    # Formato B (novo) - duas variações: "pairs" ou "pair_checks"
    # -------------
    pairs = stage03_obj.get("pairs") or stage03_obj.get("pair_checks") or []
    groups = stage03_obj.get("groups") or stage03_obj.get("group_checks") or []
    rules = stage03_obj.get("rules") or stage03_obj.get("rule_checks") or []

    pairs = [_ensure_field(dict(x)) for x in pairs]
    groups = [_ensure_group_rule_field(dict(x), "group") for x in groups]
    rules = [_ensure_group_rule_field(dict(x), "rule") for x in rules]

    # Alguns Stage03 novos trazem summary detalhada por categoria
    s = stage03_obj.get("summary") or {}

    # tenta ler contagens se existirem
    def _read_counts(block: dict) -> tuple[int, int, int, int]:
        if not isinstance(block, dict):
            return (0, 0, 0, 0)
        total = int(block.get("total", 0) or 0)
        matches = int(block.get("matches", 0) or 0)
        divergences = int(block.get("divergences", 0) or 0)
        skipped = int(block.get("skipped", 0) or 0)
        return (total, matches, divergences, skipped)

    t1, m1, d1, s1 = _read_counts(s.get("pairs"))
    t2, m2, d2, s2 = _read_counts(s.get("groups"))
    t3, m3, d3, s3 = _read_counts(s.get("rules"))

    # Se o summary não veio preenchido, calcula em cima das listas
    if (t1 + t2 + t3) == 0:
        all_items = pairs + groups + rules
        total = len(all_items)
        matches = sum(1 for x in all_items if (x.get("status") == "match"))
        divs = sum(1 for x in all_items if (x.get("status") == "divergent"))
        skps = sum(1 for x in all_items if (x.get("status") == "skipped"))
        return {
            "pairs": pairs,
            "groups": groups,
            "rules": rules,
            "summary": {
                "total": total,
                "matches": matches,
                "divergences": divs,
                "skipped": skps,
            },
        }

    return {
        "pairs": pairs,
        "groups": groups,
        "rules": rules,
        "summary": {
            "total": (t1 + t2 + t3),
            "matches": (m1 + m2 + m3),
            "divergences": (d1 + d2 + d3),
            "skipped": (s1 + s2 + s3),
        },
    }


# ------------------------
# Decide overall status
# ------------------------


def decide_overall_status(
    stage02_docs: List[dict], stage03_norm: Dict[str, Any]
) -> Dict[str, Any]:
    missing_total = sum(
        len((d.get("missing_required_fields") or [])) for d in stage02_docs
    )
    warnings_total = sum(len((d.get("warnings") or [])) for d in stage02_docs)
    divs_total = int((stage03_norm.get("summary") or {}).get("divergences", 0) or 0)

    status = "OK"
    reasons: List[str] = []

    if missing_total > 0:
        status = "FAIL"
        reasons.append(f"missing_required_fields={missing_total}")
    if divs_total > 0:
        # divergência é importante, mas em geral é ALERT (a menos que você queira FAIL)
        if status != "FAIL":
            status = "ALERT"
        reasons.append(f"divergences={divs_total}")
    if warnings_total > 0:
        if status == "OK":
            status = "ALERT"
        reasons.append(f"warnings={warnings_total}")

    return {"status": status, "reasons": reasons}


# ------------------------
# Evidence extraction helpers
# ------------------------


def pick_evidence_from_pair(c: dict) -> Tuple[str, str]:
    """
    Stage03 antigo: c["evidence"] = {"a":[...], "b":[...]}
    Stage03 novo:  c tem evidence_a/evidence_b ou details
    """
    if isinstance(c.get("evidence"), dict):
        ea = c["evidence"].get("a") or []
        eb = c["evidence"].get("b") or []
        return ("\n".join(ea[:2]), "\n".join(eb[:2]))

    ea = c.get("evidence_a") or ""
    eb = c.get("evidence_b") or ""
    if ea or eb:
        return (str(ea), str(eb))

    # fallback
    return ("", "")


# ------------------------
# Markdown / HTML builders
# ------------------------


def build_markdown(report: dict) -> str:
    overall = report.get("overall") or {}
    s01 = report.get("stage01_quality") or {}
    s02 = report.get("stage02") or {}
    s03 = report.get("stage03") or {}

    lines: List[str] = []
    lines.append(f"# Report — Exportation")
    lines.append("")
    lines.append(f"- Generated at: **{report.get('generated_at','')}**")
    lines.append(f"- Overall: **{overall.get('status','')}**")
    if overall.get("reasons"):
        lines.append(f"- Reasons: {', '.join(overall['reasons'])}")
    lines.append("")

    # Stage01
    lines.append("## Stage 01 — Extração de texto (qualidade)")
    docs = s01.get("documents") or []
    if not docs:
        lines.append("_Sem dados do Stage 01._")
    else:
        lines.append("| Documento | Páginas | Direct | OCR |")
        lines.append("|---|---:|---:|---:|")
        for d in docs:
            lines.append(
                f"| {d.get('file','')} | {d.get('pages',0)} | {d.get('direct_pages',0)} | {d.get('ocr_pages',0)} |"
            )
    lines.append("")

    # Stage02
    lines.append("## Stage 02 — Campos por documento")
    d2 = s02.get("documents") or []
    if not d2:
        lines.append("_Sem dados do Stage 02._")
    else:
        lines.append("### Documentos esperados")
        lines.append("| Tipo | Encontrados | Status |")
        lines.append("|---|---:|---|")
        for label, cnt, status in expected_docs_rows(d2):
            lines.append(f"| {label} | {cnt} | {status} |")
        lines.append("")

        lines.append("| Doc | Kind | Status | Missing | Warnings | Required | Fields |")
        lines.append("|---|---|---|---|---|---:|---:|")
        for d in d2:
            miss = ", ".join(d.get("missing_required_fields") or []) or "-"
            warn = "; ".join(d.get("warnings") or []) or "-"
            lines.append(
                f"| {d.get('original_file','')} | {doc_kind_with_original(d.get('doc_kind',''), d.get('original_file',''))} | {d.get('status','')} | {miss} | {warn} | {d.get('required_present',0)} / {d.get('required_total',0)} | {d.get('fields_present',0)} / {d.get('fields_total', d.get('fields_count', 0))} |"
            )
    lines.append("")

    # Stage03
    lines.append("## Stage 03 — Comparações")
    summ = s03.get("summary") or {}
    counts = s03.get("counts") or {}
    lines.append(
        f"- Total: **{summ.get('total',0)}** | Match: **{summ.get('matches',0)}** | Divergences: **{summ.get('divergences',0)}** | Skipped: **{summ.get('skipped',0)}**"
    )
    lines.append(
        f"- (render) matches={counts.get('matches',0)} divergent={counts.get('divergent',0)} skipped={counts.get('skipped',0)}"
    )
    lines.append("")

    # Divergences list
    divs = (report.get("lists") or {}).get("divergent") or []
    if divs:
        lines.append("### Divergências (top 50)")
        lines.append("| Bucket | Documento A | Documento B | Campo | A | B |")
        lines.append("|---|---|---|---|---|---|")
        for c in divs[:50]:
            company_a, company_b = split_pair_companies(c.get("pair"))
            lines.append(
                f"| {c.get('bucket','')} | {tr(company_a)} | {tr(company_b)} | {tr(c.get('field','?'))} | {tr(c.get('a_value'))} | {tr(c.get('b_value'))} |"
            )
        lines.append("")
    else:
        lines.append("### Divergências")
        lines.append("_Nenhuma divergência._")
        lines.append("")

    # Skipped list
    skips = (report.get("lists") or {}).get("skipped") or []
    if skips:
        lines.append("### Skipped (top 50)")
        lines.append("| Bucket | Documento A | Documento B | Campo | Motivo |")
        lines.append("|---|---|---|---|---|")
        for c in skips[:50]:
            reason = format_skip_reason(c.get("reason"), c.get("pair"))
            company_a, company_b = split_pair_companies(c.get("pair"))
            lines.append(
                f"| {c.get('bucket','')} | {tr(company_a)} | {tr(company_b)} | {tr(c.get('field','?'))} | {tr(reason)} |"
            )
        lines.append("")
    else:
        lines.append("### Skipped")
        lines.append("_Nenhum skipped._")
        lines.append("")

    return "\n".join(lines)


def build_stage02_table_html(stage02_docs: List[dict]) -> str:
    rows = []
    for d in stage02_docs:
        miss = ", ".join(d.get("missing_required_fields") or []) or "-"
        warn = "; ".join(d.get("warnings") or []) or "-"
        status = d.get("status") or ""
        status_badge = status
        badge_class = "ok"
        if status == "FAIL":
            badge_class = "fail"
        elif status == "ALERT":
            badge_class = "alert"

        rows.append(
            f"""
        <tr>
          <td><b>{tr(doc_kind_with_original(d.get("doc_kind",""), d.get("original_file","")))}</b></td>
          <td><span class="badge {badge_class}">{tr(status_badge)}</span><br><span class="muted">{tr("missing_required_fields="+str(len(d.get("missing_required_fields") or [])) if status=="FAIL" else "warnings="+str(len(d.get("warnings") or [])) if status=="ALERT" else "no_missing_no_warnings")}</span></td>
          <td>{tr(miss)}</td>
          <td>{tr(warn)}</td>
          <td style="text-align:right">{tr(d.get("required_present",0))} / {tr(d.get("required_total",0))}</td>
          <td style="text-align:right">{tr(d.get("fields_present",0))} / {tr(d.get("fields_total", d.get("fields_count",0)))}</td>
        </tr>
        """
        )

    return f"""
    <table class="tbl">
      <thead>
        <tr>
          <th>Documento</th>
          <th>Status</th>
          <th>Missing</th>
          <th>Warnings</th>
          <th style="text-align:right">Required</th>
          <th style="text-align:right">Fields</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


def build_html(report: dict) -> str:
    overall = report.get("overall") or {}
    s01 = report.get("stage01_quality") or {}
    s02 = report.get("stage02") or {}
    s03 = report.get("stage03") or {}

    status = (overall.get("status") or "OK").upper()
    badge_class = "ok"
    if status == "FAIL":
        badge_class = "fail"
    elif status == "ALERT":
        badge_class = "alert"

    reasons = ", ".join(overall.get("reasons") or [])

    # stage01 small table
    s01_rows = []
    for d in s01.get("documents") or []:
        s01_rows.append(
            f"""
        <tr>
          <td>{tr(d.get("file",""))}</td>
          <td style="text-align:right">{tr(d.get("pages",0))}</td>
          <td style="text-align:right">{tr(d.get("direct_pages",0))}</td>
          <td style="text-align:right">{tr(d.get("ocr_pages",0))}</td>
        </tr>
        """
        )
    stage01_tbl = ""
    if s01_rows:
        stage01_tbl = f"""
        <table class="tbl">
          <thead><tr><th>Documento</th><th style="text-align:right">Páginas</th><th style="text-align:right">Direct</th><th style="text-align:right">OCR</th></tr></thead>
          <tbody>{''.join(s01_rows)}</tbody>
        </table>
        """
    else:
        stage01_tbl = "<div class='muted'>Sem dados do Stage 01.</div>"

    # Stage02 table
    expected_rows = expected_docs_rows(s02.get("documents") or [])
    expected_tbl = ""
    if expected_rows:
        exp_rows_html = []
        for label, cnt, status in expected_rows:
            exp_rows_html.append(
                f"<tr><td><b>{tr(label)}</b></td><td style=\"text-align:right\">{tr(cnt)}</td><td>{tr(status)}</td></tr>"
            )
        expected_tbl = f"""
        <table class=\"tbl\">
          <thead><tr><th>Tipo</th><th style=\"text-align:right\">Encontrados</th><th>Status</th></tr></thead>
          <tbody>{''.join(exp_rows_html)}</tbody>
        </table>
        """

    stage02_tbl = build_stage02_table_html(s02.get("documents") or [])

    # Stage03 summary
    summ = s03.get("summary") or {}
    counts = s03.get("counts") or {}
    stage03_box = f"""
      <div class="grid">
        <div class="card">
          <div class="k">Total checks</div>
          <div class="v">{tr(summ.get("total",0))}</div>
        </div>
        <div class="card">
          <div class="k">Matches</div>
          <div class="v">{tr(summ.get("matches",0))}</div>
        </div>
        <div class="card">
          <div class="k">Divergences</div>
          <div class="v">{tr(summ.get("divergences",0))}</div>
        </div>
        <div class="card">
          <div class="k">Skipped</div>
          <div class="v">{tr(summ.get("skipped",0))}</div>
        </div>
      </div>
      <div class="muted" style="margin-top:8px">
        (render) matches={tr(counts.get("matches",0))} divergent={tr(counts.get("divergent",0))} skipped={tr(counts.get("skipped",0))}
      </div>
    """

    # Divergences table
    divs = (report.get("lists") or {}).get("divergent") or []
    rows_div = []
    for c in divs[:100]:
        field_label = c.get("field") or c.get("check") or "?"
        company_a, company_b = split_pair_companies(c.get("pair"))
        rows_div.append(
            f"""
        <tr>
          <td class="muted">{tr(c.get("bucket",""))}</td>
          <td>{tr(company_a or "-")}</td>
          <td>{tr(company_b or "-")}</td>
          <td><code title="{tr(field_label)}">{tr(field_label)}</code></td>
          <td>{tr(c.get("a_value"))}</td>
          <td>{tr(c.get("b_value"))}</td>
          <td class="muted">{tr(c.get("evidence_a",""))}</td>
          <td class="muted">{tr(c.get("evidence_b",""))}</td>
        </tr>
        """
        )
    div_tbl = ""
    if rows_div:
        div_tbl = f"""
        <table class="tbl">
          <thead>
            <tr>
              <th>Tipo</th>
              <th>Documento A</th>
              <th>Documento B</th>
              <th>Campo</th>
              <th>A</th>
              <th>B</th>
              <th>Evidência A</th>
              <th>Evidência B</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_div)}
          </tbody>
        </table>
        """
    else:
        div_tbl = "<div class='muted'>Nenhuma divergência.</div>"

    # Skipped table
    skips = (report.get("lists") or {}).get("skipped") or []
    rows_sk = []
    for c in skips[:100]:
        field_label = c.get("field") or c.get("check") or "?"
        company_a, company_b = split_pair_companies(c.get("pair"))
        rows_sk.append(
            f"""
        <tr>
          <td class="muted">{tr(c.get("bucket",""))}</td>
          <td>{tr(company_a or "-")}</td>
          <td>{tr(company_b or "-")}</td>
          <td><code title="{tr(field_label)}">{tr(field_label)}</code></td>
          <td class="muted">{tr(format_skip_reason(c.get("reason",""), c.get("pair")))}</td>
        </tr>
        """
        )
    sk_tbl = ""
    if rows_sk:
        sk_tbl = f"""
        <table class="tbl">
          <thead>
            <tr>
              <th>Tipo</th>
              <th>Documento A</th>
              <th>Documento B</th>
              <th>Campo</th>
              <th>Motivo</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_sk)}
          </tbody>
        </table>
        """
    else:
        sk_tbl = "<div class='muted'>Nenhum skipped.</div>"

    html_out = f"""
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Report — Exportation</title>
  <style>
    body {{
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
      background: #fff;
      color: #111;
      margin: 20px;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    h1 {{ margin: 0 0 6px 0; }}
    h2 {{ margin-top: 28px; }}
    .muted {{ color: #666; font-size: 12px; white-space: pre-wrap; }}
    .badge {{
      display: inline-block; padding: 3px 10px; border-radius: 999px;
      font-size: 12px; font-weight: 600;
      border: 1px solid #ddd;
    }}
    .badge.ok {{ background: #e7f6ea; border-color: #bfe7c7; color: #1b5e20; }}
    .badge.alert {{ background: #fff7e0; border-color: #ffe3a3; color: #7a4a00; }}
    .badge.fail {{ background: #fdecea; border-color: #f5c2be; color: #7f1d1d; }}
    .tbl {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      table-layout: fixed;
    }}
    .tbl th, .tbl td {{
      border-bottom: 1px solid #eee;
      padding: 10px 8px;
      vertical-align: top;
      font-size: 13px;
      word-break: break-word;
    }}
    .tbl th {{ text-align: left; background: #fafafa; }}
    code {{
      background: #f6f6f6;
      padding: 2px 6px;
      border-radius: 6px;
      font-size: 12px;
      display: inline-block;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      vertical-align: bottom;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }}
    .card {{
      border: 1px solid #eee;
      border-radius: 12px;
      padding: 12px;
      background: #fff;
    }}
    .card .k {{ color: #666; font-size: 12px; }}
    .card .v {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
    .section {{
      border: 1px solid #eee;
      border-radius: 14px;
      padding: 14px;
      margin-top: 14px;
      background: #fff;
    }}
  </style>
</head>
<body>
<div class="container">

  <h1>Report — Exportation</h1>
  <div class="muted">Generated at {tr(report.get("generated_at",""))}</div>
  <div style="margin-top:10px">
    <span class="badge {badge_class}">{tr(status)}</span>
    <span class="muted" style="margin-left:10px">{tr(reasons)}</span>
  </div>

  <h2>Stage 01 — Qualidade da extração</h2>
  <div class="section">
    {stage01_tbl}
  </div>

  <h2>Stage 02 — Campos por documento</h2>
  <div class="section">
    <h3>Documentos esperados</h3>
    {expected_tbl}
    {stage02_tbl}
  </div>

  <h2>Stage 03 — Comparações</h2>
  <div class="section">
    {stage03_box}
  </div>

  <h2>Divergências</h2>
  <div class="section">
    {div_tbl}
  </div>

  <h2>Skipped</h2>
  <div class="section">
    {sk_tbl}
  </div>

</div>
</body>
</html>
"""
    return html_out


# ------------------------
# Main
# ------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--stage01",
        required=True,
        help="Pasta Stage 01 Exportation (com *_extracted.json)",
    )
    ap.add_argument(
        "--stage02",
        required=True,
        help="Pasta Stage 02 Exportation (com *_fields.json)",
    )
    ap.add_argument(
        "--stage03",
        required=True,
        help="Arquivo _stage03_comparison.json (Stage 03 Exportation)",
    )
    ap.add_argument(
        "--out", required=True, help="Pasta de saída Stage 04 report (Exportation)"
    )
    args = ap.parse_args()

    run_stage_04_report(
        stage01_dir=Path(args.stage01),
        stage02_dir=Path(args.stage02),
        stage03_file=Path(args.stage03),
        out_dir=Path(args.out),
        verbose=True,
    )


def run_stage_04_report(
    stage01_dir: Path,
    stage02_dir: Path,
    stage03_file: Path,
    out_dir: Path,
    verbose: bool = True,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    stage01_quality = extract_stage01_quality(stage01_dir)
    stage02_docs = load_stage02_docs(stage02_dir)
    stage03_obj = read_json(stage03_file)
    stage03_norm = normalize_stage03(stage03_obj)

    overall = decide_overall_status(stage02_docs, stage03_norm)

    divergent: List[dict] = []
    skipped: List[dict] = []
    matches: List[dict] = []

    for c in stage03_norm.get("pairs") or []:
        st = (c.get("status") or "").lower()
        ea, eb = pick_evidence_from_pair(c)
        item = dict(c)
        item["bucket"] = "pair"
        item["evidence_a"] = ea
        item["evidence_b"] = eb
        if st == "divergent":
            divergent.append(item)
        elif st == "skipped":
            skipped.append(item)
        elif st == "match":
            matches.append(item)

    for bucket_name in ("groups", "rules"):
        for c in stage03_norm.get(bucket_name) or []:
            st = (c.get("status") or "").lower()
            item = dict(c)
            item["bucket"] = bucket_name[:-1]

            # enrich values for report visibility
            if bucket_name == "groups" and item.get("items"):
                vals = []
                for it in item.get("items") or []:
                    doc = it.get("doc") or it.get("doc_kind") or "doc"
                    val = it.get("value")
                    vals.append(f"{doc}: {val}")
                item.setdefault("a_value", "; ".join(vals))
            if bucket_name == "rules":
                if item.get("invoice_incoterm") is not None:
                    item.setdefault("a_value", item.get("invoice_incoterm"))
                if item.get("bl_freight_mode") is not None:
                    item.setdefault("b_value", item.get("bl_freight_mode"))

            if st in ("divergent", "fail", "error"):
                divergent.append(item)
            elif st in ("skipped", "missing"):
                skipped.append(item)
            elif st in ("match", "ok", "pass"):
                matches.append(item)

    stage02_section = build_stage02_section(stage02_docs)

    report = {
        "generated_at": now_iso(),
        "flow": "exportation",
        "inputs": {
            "stage01_dir": str(stage01_dir),
            "stage02_dir": str(stage02_dir),
            "stage03_file": str(stage03_file),
        },
        "overall": overall,
        "stage01_quality": stage01_quality,
        "stage02": stage02_section,
        "stage03": {
            "summary": stage03_norm.get("summary") or {},
            "counts": {
                "matches": len(matches),
                "divergent": len(divergent),
                "skipped": len(skipped),
            },
        },
        "lists": {
            "divergent": divergent,
            "skipped": skipped,
            "matches": matches[:200],
        },
    }

    out_json = out_dir / "_stage04_report.json"
    out_md = out_dir / "_stage04_report.md"
    out_html = out_dir / "_stage04_report.html"

    write_json(out_json, report)
    write_text(out_md, build_markdown(report))
    write_text(out_html, build_html(report))

    if verbose:
        print("Concluído.")
        print(f"JSON : {out_json}")
        print(f"MD   : {out_md}")
        print(f"HTML : {out_html}")

    return {
        "processed": True,
        "warnings": [],
        "output_json": str(out_json),
        "output_md": str(out_md),
        "output_html": str(out_html),
    }


if __name__ == "__main__":
    main()

