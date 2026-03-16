from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class Stage02LLMError(RuntimeError):
    pass


TOP_LEVEL_KEYS = (
    "source",
    "generated_at",
    "fields",
    "missing_required_fields",
    "warnings",
)
SOURCE_KEYS = ("stage01_file", "original_file", "doc_kind", "doc_kind_hint")
FIELD_META_KEYS = ("present", "required", "value", "evidence", "method")


DOC_KIND_FIELD_SPEC: Dict[str, List[Tuple[str, bool]]] = {
    "commercial_invoice": [
        ("invoice_number", True),
        ("invoice_date", True),
        ("country_of_origin", True),
        ("transport_mode", True),
        ("port_of_loading", True),
        ("port_of_destination", True),
        ("gross_weight_kg", True),
        ("net_weight_kg", True),
        ("incoterm", True),
        ("currency", True),
        ("ncm", True),
        ("container_count", True),
        ("exporter_cnpj", True),
        ("exporter_name", True),
        ("importer_name", True),
        ("payment_terms", False),
    ],
    "packing_list": [
        ("packing_list_number", True),
        ("packing_date", True),
        ("gross_weight_kg", True),
        ("net_weight_kg", True),
        ("ncm", True),
        ("incoterm", True),
        ("container_count", True),
        ("containers", True),
    ],
    "draft_bl": [
        ("freight_mode", True),
        ("incoterm", True),
        ("ncm", True),
        ("due", True),
        ("ruc", True),
        ("booking_number", False),
        ("wooden_packing", True),
        ("containers", True),
        ("total_cartons", True),
        ("net_weight_kg_total", True),
        ("gross_weight_kg_total", True),
        ("cubic_meters_total", True),
        ("exporter_cnpj", True),
        ("exporter_name", True),
        ("phones_found", False),
        ("importer_name", True),
        ("notify_party_name", True),
    ],
    "certificate_of_origin": [
        ("invoice_number", True),
        ("certificate_date", True),
        ("transport_mode", False),
        ("exporter_name", True),
        ("importer_name", True),
        ("net_weight_kg", False),
        ("gross_weight_kg", True),
        ("total_m2", False),
    ],
    "container_data": [
        ("invoice_number", True),
        ("booking_number", True),
        ("containers", True),
    ],
}


DOC_KIND_GUIDE: Dict[str, List[str]] = {
    "commercial_invoice": [
        "Extract invoice identifiers, date, origin country, transport mode, ports, and incoterm.",
        "Extract gross/net weight in kg, NCM, currency, container count, exporter/importer names, and exporter CNPJ.",
    ],
    "packing_list": [
        "Extract packing list number/date, gross/net weight in kg, NCM, incoterm, and container count.",
        "Extract containers as a structured list when present.",
    ],
    "draft_bl": [
        "Extract freight mode, incoterm, NCM, DUE, RUC, carton/weight/CBM totals, exporter/importer/notify data, and containers.",
    ],
    "certificate_of_origin": [
        "Extract invoice number, certificate date, exporter/importer names, and weights.",
        "Invoice number may be labeled as Commercial Invoice, Fatura Comercial, or Factura Comercial (EN/PT/ES variants). Do not rely on fixed digit patterns.",
        "Transport mode is optional in this document type.",
        "At least one of net_weight_kg or total_m2 must be present. If the document uses area (m2) instead of net weight, populate total_m2.",
    ],
    "container_data": [
        "Extract invoice number, booking number, and all container rows.",
    ],
}


DOC_KIND_HINT_ALIASES = {
    "commercial_invoice": "commercial_invoice",
    "invoice": "commercial_invoice",
    "packing_list": "packing_list",
    "packing list": "packing_list",
    "pl": "packing_list",
    "draft_bl": "draft_bl",
    "bl": "draft_bl",
    "bill_of_lading": "draft_bl",
    "certificate_of_origin": "certificate_of_origin",
    "co": "certificate_of_origin",
    "container_data": "container_data",
}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def normalize_doc_kind_hint(v: Any) -> Optional[str]:
    if v is None:
        return None
    return DOC_KIND_HINT_ALIASES.get(str(v).strip().lower())


def join_pages(stage01_obj: Dict[str, Any]) -> str:
    parts: List[str] = []
    for pg in stage01_obj.get("pages") or []:
        text = (pg.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def infer_doc_kind(filename: str, full_text: str) -> str:
    fn = (filename or "").lower()
    t = re.sub(r"\s+", " ", (full_text or "")).lower()
    if ("certificate of origin" in t) or ("certificado de origem" in t):
        return "certificate_of_origin"
    if ("bill of lading" in t) or ("b/l number" in t) or ("carrier" in t and "freight" in t):
        return "draft_bl"
    if ("packing list" in fn) or ("packing" in fn) or ("packing list" in t):
        return "packing_list"
    if ("commercial invoice" in fn) or ("invoice" in fn) or ("invoice" in t):
        return "commercial_invoice"
    if ("dados cntr" in fn) or ("booking" in t and "container" in t):
        return "container_data"
    return "unknown"


def build_fields_template(doc_kind: str) -> Dict[str, Dict[str, Any]]:
    spec = DOC_KIND_FIELD_SPEC.get(doc_kind)
    if not spec:
        raise Stage02LLMError(f"Unsupported doc_kind for LLM extraction: {doc_kind}")
    out: Dict[str, Dict[str, Any]] = {}
    for field_name, required in spec:
        out[field_name] = {
            "present": False,
            "required": bool(required),
            "value": None,
            "evidence": [],
            "method": "llm_manual",
        }
    return out


def build_stage02_template(
    stage01_file: str,
    original_file: str,
    doc_kind: str,
    doc_kind_hint: str,
) -> Dict[str, Any]:
    return {
        "source": {
            "stage01_file": stage01_file,
            "original_file": original_file,
            "doc_kind": doc_kind,
            "doc_kind_hint": doc_kind_hint,
        },
        "generated_at": now_iso(),
        "fields": build_fields_template(doc_kind),
        "missing_required_fields": [],
        "warnings": [],
    }


def build_prompt(stage01_obj: Dict[str, Any], stage02_template: Dict[str, Any], doc_kind: str) -> str:
    guide = DOC_KIND_GUIDE.get(doc_kind, [])
    guide_block = "\n".join(f"- {g}" for g in guide) if guide else "- Extract all template fields strictly from source."
    return (
        "You are extracting fields for Stage 02 (exportation) from a Stage 01 JSON.\n"
        "Return only valid JSON.\n"
        "Do not add keys. Do not remove keys.\n"
        "Use only source content. Do not guess values.\n"
        "If not found: present=false, value=null, evidence=[].\n"
        "Keep source exactly as template.\n"
        "Keep required exactly as template.\n"
        "Use a short non-empty method string.\n"
        "\n"
        f"Document kind: {doc_kind}\n"
        "Business extraction rules:\n"
        f"{guide_block}\n"
        "\n"
        "TEMPLATE_STAGE02_JSON:\n"
        f"{json.dumps(stage02_template, ensure_ascii=False, indent=2)}\n"
        "\n"
        "STAGE01_JSON:\n"
        f"{json.dumps(stage01_obj, ensure_ascii=False, indent=2)}\n"
    )


def _strip_markdown_fence(raw: str) -> str:
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_model_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise Stage02LLMError("LLM returned empty output.")

    candidates = [text, _strip_markdown_fence(text)]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1].strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise Stage02LLMError("LLM output is not valid JSON object.")


def _normalize_warnings(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for item in v:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def _normalize_field(field_name: str, expected_required: bool, field_obj: Any) -> Dict[str, Any]:
    if not isinstance(field_obj, dict):
        raise Stage02LLMError(f"Field '{field_name}' is not an object.")
    keys = set(field_obj.keys())
    expected = set(FIELD_META_KEYS)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise Stage02LLMError(f"Field '{field_name}' has invalid keys. missing={missing} extra={extra}")

    present = field_obj.get("present")
    required = field_obj.get("required")
    value = field_obj.get("value")
    evidence = field_obj.get("evidence")
    method = field_obj.get("method")

    if not isinstance(present, bool):
        raise Stage02LLMError(f"Field '{field_name}' has non-boolean 'present'.")
    if not isinstance(required, bool):
        raise Stage02LLMError(f"Field '{field_name}' has non-boolean 'required'.")
    if bool(required) != bool(expected_required):
        raise Stage02LLMError(
            f"Field '{field_name}' changed required={required} (expected {expected_required})."
        )
    if evidence is None:
        evidence_list: List[str] = []
    elif isinstance(evidence, list):
        evidence_list = [str(x).strip() for x in evidence if str(x).strip()]
    else:
        raise Stage02LLMError(f"Field '{field_name}' evidence must be a list.")
    if not isinstance(method, str) or not method.strip():
        raise Stage02LLMError(f"Field '{field_name}' method must be non-empty string.")

    return {
        "present": present,
        "required": required,
        "value": value,
        "evidence": evidence_list,
        "method": method.strip(),
    }


def _meta_is_present(meta: Dict[str, Any]) -> bool:
    if bool(meta.get("present")):
        return True
    value = meta.get("value")
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _apply_doc_kind_business_rules(
    doc_kind: str,
    normalized_fields: Dict[str, Dict[str, Any]],
    missing_required: List[str],
) -> List[str]:
    missing = list(missing_required)
    if doc_kind == "certificate_of_origin":
        net_ok = _meta_is_present(normalized_fields.get("net_weight_kg") or {})
        area_ok = _meta_is_present(normalized_fields.get("total_m2") or {})
        if not net_ok and not area_ok:
            missing.append("net_weight_kg_or_total_m2")
    return sorted(set(missing))


def normalize_llm_stage02_payload(
    payload: Dict[str, Any],
    template: Dict[str, Any],
    doc_kind: str,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    if not isinstance(payload, dict):
        raise Stage02LLMError("LLM payload must be an object.")

    template_fields = template["fields"]
    expected_keys = set(template_fields.keys())

    if "fields" in payload:
        fields_payload = payload.get("fields")
        warnings_payload = payload.get("warnings", [])
    else:
        fields_payload = payload
        warnings_payload = []

    if not isinstance(fields_payload, dict):
        raise Stage02LLMError("LLM payload does not contain a valid 'fields' object.")

    got_keys = set(fields_payload.keys())
    if got_keys != expected_keys:
        missing = sorted(expected_keys - got_keys)
        extra = sorted(got_keys - expected_keys)
        raise Stage02LLMError(
            f"Field keys mismatch for doc_kind={doc_kind}. missing={missing} extra={extra}"
        )

    normalized: Dict[str, Any] = {}
    for field_name, meta in template_fields.items():
        normalized[field_name] = _normalize_field(
            field_name=field_name,
            expected_required=bool(meta.get("required")),
            field_obj=fields_payload[field_name],
        )

    missing_required = [
        k
        for k, meta in normalized.items()
        if bool(meta.get("required")) and not bool(meta.get("present"))
    ]
    missing_required = _apply_doc_kind_business_rules(
        doc_kind=doc_kind,
        normalized_fields=normalized,
        missing_required=missing_required,
    )
    warnings = _normalize_warnings(warnings_payload)
    return normalized, missing_required, warnings


def validate_final_stage02_output(out_obj: Dict[str, Any], doc_kind: str) -> None:
    if not isinstance(out_obj, dict):
        raise Stage02LLMError("Final Stage 02 output is not an object.")

    if set(out_obj.keys()) != set(TOP_LEVEL_KEYS):
        missing = sorted(set(TOP_LEVEL_KEYS) - set(out_obj.keys()))
        extra = sorted(set(out_obj.keys()) - set(TOP_LEVEL_KEYS))
        raise Stage02LLMError(f"Final Stage 02 top-level keys mismatch. missing={missing} extra={extra}")

    source = out_obj.get("source")
    if not isinstance(source, dict):
        raise Stage02LLMError("Final Stage 02 source is not an object.")
    if set(source.keys()) != set(SOURCE_KEYS):
        missing = sorted(set(SOURCE_KEYS) - set(source.keys()))
        extra = sorted(set(source.keys()) - set(SOURCE_KEYS))
        raise Stage02LLMError(f"Final Stage 02 source keys mismatch. missing={missing} extra={extra}")

    expected_fields = set(build_fields_template(doc_kind).keys())
    fields = out_obj.get("fields")
    if not isinstance(fields, dict):
        raise Stage02LLMError("Final Stage 02 fields is not an object.")
    if set(fields.keys()) != expected_fields:
        missing = sorted(expected_fields - set(fields.keys()))
        extra = sorted(set(fields.keys()) - expected_fields)
        raise Stage02LLMError(f"Final Stage 02 field keys mismatch. missing={missing} extra={extra}")


def _command_missing_text(text: str) -> bool:
    t = str(text or "").lower()
    return (
        "is not recognized as an internal or external command" in t
        or "nao e reconhecido como um comando interno" in t
        or "command not found" in t
        or "not found" in t
        or "no such file or directory" in t
    )


def run_codex_cli_prompt(
    prompt: str,
    cwd: Path,
    model: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> str:
    codex_bin = os.getenv("DOCREADER_CODEX_CLI_PATH", "codex").strip() or "codex"
    model_name = (model or os.getenv("DOCREADER_STAGE2_LLM_MODEL", "")).strip()
    timeout = timeout_sec or int(os.getenv("DOCREADER_STAGE2_LLM_TIMEOUT_SEC", "240"))

    fd, tmp_path = tempfile.mkstemp(prefix="stage02_export_llm_", suffix=".txt")
    os.close(fd)
    output_file = Path(tmp_path)

    cmd: List[str] = [
        codex_bin,
        "exec",
        "-",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-C",
        str(cwd),
        "-o",
        str(output_file),
    ]
    if model_name:
        cmd.extend(["-m", model_name])

    is_windows = os.name == "nt"
    is_bare = not ("\\" in codex_bin or "/" in codex_bin)
    is_cmd_script = codex_bin.lower().endswith(".cmd") or codex_bin.lower().endswith(".bat")
    use_shell = is_windows and (is_bare or is_cmd_script)
    shell_cmd = subprocess.list2cmdline(cmd) if use_shell else None

    try:
        proc = subprocess.run(
            shell_cmd if use_shell else cmd,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=str(cwd),
            timeout=max(1, int(timeout)),
            encoding="utf-8",
            shell=use_shell,
        )
    except subprocess.TimeoutExpired as exc:
        raise Stage02LLMError(f"Codex CLI timeout after {timeout}s.") from exc
    except FileNotFoundError as exc:
        raise Stage02LLMError(f"Codex CLI executable not found: '{codex_bin}'.") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    try:
        response = output_file.read_text(encoding="utf-8").strip()
    finally:
        try:
            output_file.unlink(missing_ok=True)
        except Exception:
            pass

    if proc.returncode != 0:
        details = stderr or stdout or "no stderr/stdout from codex exec"
        if _command_missing_text(details):
            raise Stage02LLMError(
                f"Codex CLI command unavailable while executing '{codex_bin}'. details={details[:600]}"
            )
        raise Stage02LLMError(
            f"Codex CLI returned non-zero exit ({proc.returncode}). details={details[:600]}"
        )

    if not response:
        if stdout:
            response = stdout
        else:
            raise Stage02LLMError("Codex CLI produced empty response.")
    return response


def extract_fields_with_llm_for_document(
    stage01_obj: Dict[str, Any],
    stage01_file: str,
    original_file: str,
    doc_kind: str,
    doc_kind_hint: str,
    cwd: Path,
    llm_client: Optional[Callable[[str, Path], str]] = None,
    model: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    template = build_stage02_template(
        stage01_file=stage01_file,
        original_file=original_file,
        doc_kind=doc_kind,
        doc_kind_hint=doc_kind_hint,
    )
    prompt = build_prompt(stage01_obj=stage01_obj, stage02_template=template, doc_kind=doc_kind)
    raw = llm_client(prompt, cwd) if llm_client is not None else run_codex_cli_prompt(
        prompt=prompt,
        cwd=cwd,
        model=model,
        timeout_sec=timeout_sec,
    )

    try:
        payload = parse_model_json(raw)
        return normalize_llm_stage02_payload(
            payload=payload,
            template=template,
            doc_kind=doc_kind,
        )
    except Stage02LLMError as exc:
        snippet = (raw or "").strip().replace("\n", " ")
        raise Stage02LLMError(f"{exc} | llm_response_snippet={snippet[:800]}") from exc


def read_codex_runtime_context() -> Dict[str, Any]:
    context_file = os.getenv("DOCREADER_CODEX_AUTH_CONTEXT_FILE", "").strip()
    has_access_token = bool(os.getenv("DOCREADER_CODEX_ACCESS_TOKEN"))

    info: Dict[str, Any] = {
        "context_file": context_file,
        "has_access_token": has_access_token,
        "connected": False,
        "provider": "",
    }
    if not context_file:
        return info

    p = Path(context_file)
    if not p.exists():
        info["context_file_missing"] = True
        return info
    try:
        payload = read_json(p)
    except Exception:
        info["context_file_invalid"] = True
        return info

    info["connected"] = bool(payload.get("connected"))
    info["provider"] = str(payload.get("provider") or "")
    identity = payload.get("identity")
    if isinstance(identity, dict):
        info["identity"] = {
            "sub": str(identity.get("sub") or ""),
            "email": str(identity.get("email") or ""),
        }
    return info


def run_stage02_llm_for_exportation(
    in_dir: Path,
    out_dir: Path,
    verbose: bool = True,
    llm_client: Optional[Callable[[str, Path], str]] = None,
    model: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(in_dir.glob("*_extracted.json"))
    if not files:
        return {
            "processed_count": 0,
            "warnings": [f"No *_extracted.json files found in: {in_dir}"],
            "documents": [],
        }

    docs: List[dict] = []
    all_warnings: List[str] = []
    total = len(files)

    for idx, p in enumerate(files, start=1):
        stage01_obj = read_json(p)
        original_file = stage01_obj.get("file") or p.name.replace("_extracted.json", ".pdf")
        full_text = join_pages(stage01_obj)
        doc_kind_hint = normalize_doc_kind_hint(stage01_obj.get("doc_kind_hint")) or ""
        doc_kind = doc_kind_hint or infer_doc_kind(original_file, full_text)

        if verbose:
            print(f"[Stage02-LLM-EXPORT] {idx}/{total} processing {p.name} (kind={doc_kind})")

        if doc_kind not in DOC_KIND_FIELD_SPEC:
            fields, missing_required, warnings = ({}, [f"doc_kind unknown: {doc_kind}"], [])
        else:
            fields, missing_required, warnings = extract_fields_with_llm_for_document(
                stage01_obj=stage01_obj,
                stage01_file=p.name,
                original_file=original_file,
                doc_kind=doc_kind,
                doc_kind_hint=doc_kind_hint,
                cwd=in_dir,
                llm_client=llm_client,
                model=model,
                timeout_sec=timeout_sec,
            )

        out_obj = {
            "source": {
                "stage01_file": p.name,
                "original_file": original_file,
                "doc_kind": doc_kind,
                "doc_kind_hint": doc_kind_hint,
            },
            "generated_at": now_iso(),
            "fields": fields,
            "missing_required_fields": missing_required,
            "warnings": warnings,
        }
        if doc_kind in DOC_KIND_FIELD_SPEC:
            validate_final_stage02_output(out_obj, doc_kind)

        out_name = p.name.replace("_extracted.json", "_fields.json")
        write_json(out_dir / out_name, out_obj)

        docs.append(
            {
                "doc_kind": doc_kind,
                "doc_kind_hint": doc_kind_hint,
                "original_file": original_file,
                "stage01_file": p.name,
                "stage02_file": out_name,
                "missing_required_fields": missing_required,
                "warnings": warnings,
            }
        )
        all_warnings.extend(warnings)

        if verbose:
            print(
                f"[Stage02-LLM-EXPORT] OK -> {out_name} | "
                f"missing={len(missing_required)} | warnings={len(warnings)}"
            )

    codex_runtime = read_codex_runtime_context()
    summary = {
        "generated_at": now_iso(),
        "flow": "exportation",
        "input_folder": str(in_dir),
        "output_folder": str(out_dir),
        "codex_auth_context": codex_runtime,
        "documents": docs,
    }
    write_json(out_dir / "_stage02_summary.json", summary)

    if verbose:
        print("[Stage02-LLM-EXPORT] Completed.")

    return {
        "processed_count": len(docs),
        "warnings": all_warnings,
        "codex_auth_context": codex_runtime,
        "documents": docs,
    }
