# -*- coding: utf-8 -*-
"""
Stage 04 - IMPORTATION - Final Report (Stages 01-03)

Inputs:
- Stage 01 folder: data/output/stage_01_text/importation
  (contains *_extracted.json and optional _stage01_summary.json)
- Stage 02 folder: data/output/stage_02_fields/importation
  (contains *_fields.json and _stage02_summary.json)
- Stage 03 file  : data/output/stage_03_compare/importation/_stage03_comparison.json

Outputs:
- data/output/stage_04_report/importation/_stage04_report.json
- data/output/stage_04_report/importation/_stage04_report.md
- data/output/stage_04_report/importation/_stage04_report.html

No external dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Helpers
# -----------------------------


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_json(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(text)


def safe(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def short(s: str, n: int = 240) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 3].rstrip() + "..."


def classify_severity(stage02_doc: dict) -> Dict[str, Any]:
    """
    Decide severidade de um doc com base em missing_required_fields e warnings.
    """
    missing = stage02_doc.get("missing_required_fields") or []
    warnings = stage02_doc.get("warnings") or []
    if missing:
        return {"level": "FAIL", "reason": f"missing_required_fields={len(missing)}"}
    if warnings:
        return {"level": "ALERT", "reason": f"warnings={len(warnings)}"}
    return {"level": "OK", "reason": "no_missing_no_warnings"}


def extract_stage01_quality(stage01_dir: Path) -> Dict[str, Any]:
    """
    Lê *_extracted.json para inferir se foi direct ou OCR, chars por página, etc.
    """
    out: Dict[str, Any] = {
        "documents": [],
        "summary": {
            "total_docs": 0,
            "docs_with_ocr_pages": 0,
            "docs_all_direct": 0,
        },
    }

    extracted = sorted(stage01_dir.glob("*_extracted.json"))
    for p in extracted:
        try:
            obj = read_json(p)
        except Exception:
            continue

        pages = obj.get("pages") or []
        methods = [safe(pg.get("method")) for pg in pages]
        chars = [int(pg.get("text_chars") or 0) for pg in pages]

        has_ocr = any(m.lower() == "ocr" for m in methods)
        all_direct = (len(methods) > 0) and all(m.lower() == "direct" for m in methods)

        out["documents"].append(
            {
                "file": obj.get("file") or p.name.replace("_extracted.json", ".pdf"),
                "extracted_json": p.name,
                "pages": len(pages),
                "methods": methods,
                "chars_by_page": chars,
                "has_ocr": has_ocr,
                "all_direct": all_direct,
            }
        )

    out["summary"]["total_docs"] = len(out["documents"])
    out["summary"]["docs_with_ocr_pages"] = sum(
        1 for d in out["documents"] if d["has_ocr"]
    )
    out["summary"]["docs_all_direct"] = sum(
        1 for d in out["documents"] if d["all_direct"]
    )
    return out


def load_stage02_docs(stage02_dir: Path) -> List[dict]:
    files = sorted(
        [
            p
            for p in stage02_dir.glob("*_fields.json")
            if p.name != "_stage02_summary.json"
        ]
    )
    return [read_json(p) for p in files]


def doc_label(d: dict) -> str:
    src = d.get("source") or {}
    kind = src.get("doc_kind") or "unknown"
    original = src.get("original_file") or "?"
    return f"{kind} | {original}"


def normalize_stage03(stage03_obj: dict) -> Dict[str, Any]:
    """
    Suporta dois formatos:
    A) formato antigo: { summary, comparisons: [...] }
    B) formato novo: { pair_checks: [...], group_checks: [...], rule_checks: [...] }
    Retorna um formato único:
    {
      "pairs": [ {pair, field, status, ...} ],
      "groups": [ ... ],
      "rules":  [ ... ],
      "summary": { matches, divergences, skipped, total }
    }
    """
    pairs: List[dict] = []
    groups: List[dict] = []
    rules: List[dict] = []

    if "comparisons" in stage03_obj:
        pairs = stage03_obj.get("comparisons") or []
        # tentar achar summary já pronto
        sm = stage03_obj.get("summary") or {}
        total = int(sm.get("total_checks") or len(pairs))
        matches = int(
            sm.get("matches") or sum(1 for c in pairs if c.get("status") == "match")
        )
        divs = int(
            sm.get("divergences")
            or sum(1 for c in pairs if c.get("status") == "divergent")
        )
        skipped = int(
            sm.get("skipped") or sum(1 for c in pairs if c.get("status") == "skipped")
        )
        return {
            "pairs": pairs,
            "groups": groups,
            "rules": rules,
            "summary": {
                "total": total,
                "matches": matches,
                "divergences": divs,
                "skipped": skipped,
            },
        }

    # formato novo (o que você descreveu)
    pairs = stage03_obj.get("pair_checks") or []
    groups = stage03_obj.get("group_checks") or []
    rules = stage03_obj.get("rule_checks") or []

    # tentativa de consolidar summary
    def count_status(
        items: List[dict], key: str = "status"
    ) -> Tuple[int, int, int, int]:
        total = len(items)
        matches = sum(
            1 for x in items if (x.get(key) or "").lower() in ("match", "ok", "pass")
        )
        divs = sum(
            1
            for x in items
            if (x.get(key) or "").lower() in ("divergent", "fail", "error")
        )
        skipped = sum(1 for x in items if (x.get(key) or "").lower() == "skipped")
        return total, matches, divs, skipped

    t1, m1, d1, s1 = count_status(pairs, "status")
    # groups/rules podem ter "status" diferente, mas ainda somamos no total geral
    t2, m2, d2, s2 = count_status(groups, "status")
    t3, m3, d3, s3 = count_status(rules, "status")

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


def decide_overall_status(
    stage02_docs: List[dict], stage03_norm: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Regra simples:
    - FAIL se existir qualquer missing_required_fields em qualquer doc
    - FAIL se existir qualquer divergences no Stage 3
    - ALERT se não for FAIL, mas houver warnings (stage2) ou skipped alto
    - OK se tudo limpo
    """
    any_missing = any((d.get("missing_required_fields") or []) for d in stage02_docs)
    any_warnings = any((d.get("warnings") or []) for d in stage02_docs)

    divs = int(stage03_norm["summary"]["divergences"])
    skipped = int(stage03_norm["summary"]["skipped"])
    total = int(stage03_norm["summary"]["total"]) or 1
    skipped_ratio = skipped / max(total, 1)

    if any_missing or divs > 0:
        reasons = []
        if any_missing:
            reasons.append("missing_required_fields_em_algum_documento")
        if divs > 0:
            reasons.append(f"divergencias_stage03={divs}")
        return {"status": "FAIL", "reasons": reasons}

    # sem fail
    if any_warnings or skipped_ratio >= 0.35:
        reasons = []
        if any_warnings:
            reasons.append("warnings_em_algum_documento")
        if skipped_ratio >= 0.35:
            reasons.append(f"muitos_skipped={skipped}/{total}")
        return {"status": "ALERT", "reasons": reasons}

    return {"status": "OK", "reasons": ["sem_missing_sem_divergencias"]}


def build_markdown(report: dict) -> str:
    overall = report["overall"]
    s1 = report.get("stage01_quality") or {}
    s2 = report.get("stage02") or {}
    s3 = report.get("stage03") or {}

    lines: List[str] = []
    lines.append(f"# Stage 04 Report — Importação")
    lines.append(f"- Gerado em: **{report['generated_at']}**")
    lines.append(f"- Status final: **{overall['status']}**")
    if overall.get("reasons"):
        lines.append(f"- Motivos: {', '.join(overall['reasons'])}")
    lines.append("")

    # Stage 01
    lines.append("## Stage 01 — Qualidade da extração")
    sm1 = s1.get("summary") or {}
    lines.append(
        f"- Docs: {sm1.get('total_docs', 0)} | all_direct: {sm1.get('docs_all_direct', 0)} | com OCR: {sm1.get('docs_with_ocr_pages', 0)}"
    )
    for d in s1.get("documents") or []:
        lines.append(
            f"- **{d['file']}** | pages={d['pages']} | has_ocr={d['has_ocr']} | methods={d['methods']}"
        )
    lines.append("")

    # Stage 02
    lines.append("## Stage 02 — Campos (por documento)")
    for doc in s2.get("documents") or []:
        lines.append(f"### {doc['label']}")
        lines.append(
            f"- Severidade: **{doc['severity']['level']}** ({doc['severity']['reason']})"
        )
        if doc["missing_required_fields"]:
            lines.append(f"- Missing: {', '.join(doc['missing_required_fields'])}")
        if doc["warnings"]:
            lines.append(f"- Warnings: {', '.join(doc['warnings'])}")
        lines.append(
            f"- Fields encontrados: {doc['fields_present_count']} / {doc['fields_total_count']}"
        )
    lines.append("")

    # Stage 03
    lines.append("## Stage 03 — Comparações")
    sm3 = s3.get("summary") or {}
    lines.append(
        f"- Total checks: {sm3.get('total', 0)} | matches: {sm3.get('matches', 0)} | divergences: {sm3.get('divergences', 0)} | skipped: {sm3.get('skipped', 0)}"
    )
    lines.append("")
    lines.append("### Divergências")
    divs = report["lists"]["divergent"]
    if not divs:
        lines.append("- (nenhuma)")
    else:
        for c in divs[:60]:
            lines.append(
                f"- [{c.get('bucket')}] {c.get('pair','?')} | {c.get('field','?')} | A={c.get('a_value')} | B={c.get('b_value')}"
            )
    lines.append("")
    lines.append("### Skipped (não comparados)")
    sk = report["lists"]["skipped"]
    if not sk:
        lines.append("- (nenhum)")
    else:
        for c in sk[:60]:
            lines.append(
                f"- [{c.get('bucket')}] {c.get('pair','?')} | {c.get('field','?')} | reason={c.get('reason','?')}"
            )
    lines.append("")

    return "\n".join(lines) + "\n"


def build_html(report: dict) -> str:
    overall = report["overall"]
    s1 = report.get("stage01_quality") or {}
    s2 = report.get("stage02") or {}
    s3 = report.get("stage03") or {}
    sm1 = s1.get("summary") or {}
    sm3 = s3.get("summary") or {}

    def badge(status: str) -> str:
        status_u = status.upper()
        cls = "ok" if status_u == "OK" else ("fail" if status_u == "FAIL" else "alert")
        return f'<span class="badge {cls}">{html.escape(status_u)}</span>'

    divergent = report["lists"]["divergent"]
    skipped = report["lists"]["skipped"]

    css = """
    body{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#111}
    h1{margin:0 0 6px 0}
    .meta{color:#444;margin-bottom:18px}
    .badge{display:inline-block;padding:4px 10px;border-radius:999px;font-weight:700;font-size:12px}
    .ok{background:#e7f7ed;color:#0b6b2b;border:1px solid #bfe7cd}
    .alert{background:#fff6e5;color:#8a5a00;border:1px solid #ffe0a6}
    .fail{background:#ffe8e8;color:#8a0000;border:1px solid #ffb8b8}
    .card{border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:12px 0}
    table{border-collapse:collapse;width:100%}
    th,td{border-bottom:1px solid #eee;padding:8px 8px;text-align:left;font-size:13px;vertical-align:top}
    th{background:#fafafa}
    .small{color:#555;font-size:12px}
    code{background:#f5f5f5;padding:1px 4px;border-radius:4px}
    """

    def tr(text: str) -> str:
        return html.escape(text)

    # Stage02 docs table
    rows2 = []
    for d in s2.get("documents") or []:
        rows2.append(
            f"""
        <tr>
          <td>{tr(d['label'])}</td>
          <td>{badge(d['severity']['level'])}<div class="small">{tr(d['severity']['reason'])}</div></td>
          <td>{tr(", ".join(d["missing_required_fields"]) if d["missing_required_fields"] else "-")}</td>
          <td>{tr(", ".join(d["warnings"]) if d["warnings"] else "-")}</td>
          <td>{d['fields_present_count']} / {d['fields_total_count']}</td>
        </tr>
        """
        )

    # Divergences table
    rows_div = []
    for c in divergent[:120]:
        rows_div.append(
            f"""
        <tr>
          <td>{tr(c.get("bucket","pair"))}</td>
          <td>{tr(c.get("pair","?"))}</td>
          <td><code>{tr(c.get("field","?"))}</code></td>
          <td>{tr(safe(c.get("a_value")))}</td>
          <td>{tr(safe(c.get("b_value")))}</td>
          <td class="small">{tr(safe(c.get("evidence_a","")))}</td>
          <td class="small">{tr(safe(c.get("evidence_b","")))}</td>
        </tr>
        """
        )

    # Skipped table
    rows_sk = []
    for c in skipped[:120]:
        rows_sk.append(
            f"""
        <tr>
          <td>{tr(c.get("bucket","pair"))}</td>
          <td>{tr(c.get("pair","?"))}</td>
          <td><code>{tr(c.get("field","?"))}</code></td>
          <td class="small">{tr(c.get("reason","?"))}</td>
        </tr>
        """
        )

    reasons = ", ".join(overall.get("reasons") or [])

    html_out = f"""<!doctype html>
    <html lang="pt-br">
    <head>
      <meta charset="utf-8"/>
      <title>Stage 04 Report — Importação</title>
      <style>{css}</style>
    </head>
    <body>
      <h1>Stage 04 Report — Importação</h1>
      <div class="meta">
        Gerado em <b>{html.escape(report["generated_at"])}</b> — Status final {badge(overall["status"])}<br/>
        <span class="small">Motivos: {html.escape(reasons)}</span>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Stage 01 — Qualidade da extração</h2>
        <div class="small">Docs: {sm1.get("total_docs",0)} | all_direct: {sm1.get("docs_all_direct",0)} | com OCR: {sm1.get("docs_with_ocr_pages",0)}</div>
        <table>
          <thead><tr><th>Arquivo</th><th>Páginas</th><th>OCR?</th><th>Métodos</th><th>Chars/página</th></tr></thead>
          <tbody>
          {''.join([
              f"<tr><td>{tr(d['file'])}</td><td>{d['pages']}</td><td>{'SIM' if d['has_ocr'] else 'NÃO'}</td><td>{tr(str(d['methods']))}</td><td>{tr(str(d['chars_by_page']))}</td></tr>"
              for d in (s1.get("documents") or [])
          ])}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Stage 02 — Campos por documento</h2>
        <table>
          <thead><tr><th>Documento</th><th>Status</th><th>Missing</th><th>Warnings</th><th>Fields</th></tr></thead>
          <tbody>
            {''.join(rows2)}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Stage 03 — Comparações</h2>
        <div class="small">Total: {sm3.get("total",0)} | Matches: {sm3.get("matches",0)} | Divergences: {sm3.get("divergences",0)} | Skipped: {sm3.get("skipped",0)}</div>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Divergências</h2>
        {'<div class="small">(nenhuma)</div>' if not rows_div else ''}
        <table>
          <thead><tr><th>Tipo</th><th>Par</th><th>Campo</th><th>A</th><th>B</th><th>Evidência A</th><th>Evidência B</th></tr></thead>
          <tbody>{''.join(rows_div)}</tbody>
        </table>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Skipped (não comparados)</h2>
        {'<div class="small">(nenhum)</div>' if not rows_sk else ''}
        <table>
          <thead><tr><th>Tipo</th><th>Par</th><th>Campo</th><th>Motivo</th></tr></thead>
          <tbody>{''.join(rows_sk)}</tbody>
        </table>
      </div>

    </body>
    </html>
    """
    return html_out


def build_stage02_section(stage02_docs: List[dict]) -> Dict[str, Any]:
    docs_out = []
    for d in stage02_docs:
        src = d.get("source") or {}
        fields = d.get("fields") or {}
        present_count = sum(
            1 for _, fv in fields.items() if (fv or {}).get("present") is True
        )
        total_count = len(fields)
        sev = classify_severity(d)
        docs_out.append(
            {
                "label": doc_label(d),
                "doc_kind": src.get("doc_kind"),
                "original_file": src.get("original_file"),
                "stage01_file": src.get("stage01_file"),
                "missing_required_fields": d.get("missing_required_fields") or [],
                "warnings": d.get("warnings") or [],
                "fields_present_count": present_count,
                "fields_total_count": total_count,
                "severity": sev,
            }
        )
    return {"documents": docs_out}


def pick_evidence_from_pair(item: dict) -> Tuple[str, str]:
    ev = item.get("evidence") or {}
    a = ev.get("a") if isinstance(ev, dict) else None
    b = ev.get("b") if isinstance(ev, dict) else None
    # suportar formato novo (evidence_a/evidence_b)
    if not a and item.get("evidence_a"):
        a = [item.get("evidence_a")]
    if not b and item.get("evidence_b"):
        b = [item.get("evidence_b")]
    ea = short(a[0]) if isinstance(a, list) and a else ""
    eb = short(b[0]) if isinstance(b, list) and b else ""
    return ea, eb


def run_stage_04_report(
    stage01_dir: Path,
    stage02_dir: Path,
    stage03_file: Path,
    out_dir: Path,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Execute Stage 04: Generate final consolidated report

    Args:
        stage01_dir: Directory with Stage 01 extracted text
        stage02_dir: Directory with Stage 02 extracted fields
        stage03_file: Stage 03 comparison JSON file
        out_dir: Output directory for reports
        verbose: Print progress messages

    Returns:
        Dictionary with report paths and status
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load
    stage01_quality = extract_stage01_quality(stage01_dir)
    stage02_docs = load_stage02_docs(stage02_dir)
    stage03_obj = read_json(stage03_file)
    stage03_norm = normalize_stage03(stage03_obj)

    # Overall
    overall = decide_overall_status(stage02_docs, stage03_norm)

    # Lists from Stage03 (pairs/groups/rules)
    divergent: List[dict] = []
    skipped: List[dict] = []
    matches: List[dict] = []

    # pairs
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

    # groups/rules (podem ter status = ok/fail/pass)
    for bucket_name in ("groups", "rules"):
        for c in stage03_norm.get(bucket_name) or []:
            st = (c.get("status") or "").lower()
            item = dict(c)
            item["bucket"] = bucket_name[:-1]  # group / rule
            if st in ("divergent", "fail", "error"):
                divergent.append(item)
            elif st == "skipped":
                skipped.append(item)
            elif st in ("match", "ok", "pass"):
                matches.append(item)

    # Stage02 section
    stage02_section = build_stage02_section(stage02_docs)

    report = {
        "generated_at": now_iso(),
        "flow": "importation",
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

    # Write outputs
    out_json = out_dir / "_stage04_report.json"
    out_md = out_dir / "_stage04_report.md"
    out_html = out_dir / "_stage04_report.html"

    write_json(out_json, report)
    write_text(out_md, build_markdown(report))
    write_text(out_html, build_html(report))

    if verbose:
        print("Completed.")
        print(f"JSON : {out_json}")
        print(f"MD   : {out_md}")
        print(f"HTML : {out_html}")

    return {
        "success": True,
        "output_json": str(out_json),
        "output_md": str(out_md),
        "output_html": str(out_html),
        "overall_status": overall.get("status"),
        "divergent_count": len(divergent),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--stage01", required=True, help="Stage 01 folder with *_extracted.json"
    )
    ap.add_argument(
        "--stage02", required=True, help="Stage 02 folder with *_fields.json"
    )
    ap.add_argument(
        "--stage03", required=True, help="Stage 03 _stage03_comparison.json file"
    )
    ap.add_argument("--out", required=True, help="Stage 04 output folder")
    args = ap.parse_args()

    run_stage_04_report(
        stage01_dir=Path(args.stage01),
        stage02_dir=Path(args.stage02),
        stage03_file=Path(args.stage03),
        out_dir=Path(args.out),
    )


if __name__ == "__main__":
    main()
