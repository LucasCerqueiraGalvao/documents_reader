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
    "invoice": [
        ("invoice_number", True),
        ("invoice_date", True),
        ("payment_terms", True),
        ("importer_name", True),
        ("importer_cnpj", True),
        ("consignee_cnpj", True),
        ("shipper_name", True),
        ("currency", True),
        ("incoterm", True),
        ("country_of_origin", False),
        ("country_of_acquisition", False),
        ("country_of_provenance", False),
        ("net_weight_kg", True),
        ("gross_weight_kg", True),
        ("total_quantity", False),
        ("freight_and_expenses", False),
        ("line_items", False),
    ],
    "packing_list": [
        ("invoice_number", True),
        ("importer_name", True),
        ("shipper_name", False),
        ("importer_cnpj", True),
        ("packages_total", True),
        ("net_weight_kg", True),
        ("gross_weight_kg", True),
        ("measurement_total_m3", True),
        ("items", True),
    ],
    "bl": [
        ("shipper_name", True),
        ("importer_name", True),
        ("consignee_name", True),
        ("importer_cnpj", True),
        ("consignee_cnpj", True),
        ("ncm", True),
        ("ncm_or_hs", True),
        ("gross_weight_kg", True),
        ("freight_terms", False),
        ("freight_term", False),
        ("measurement_m3", False),
        ("notify_party", False),
        ("port_of_loading", False),
        ("port_of_discharge", False),
    ],
    "di": [
        ("importer_name", True),
        ("importer_cnpj", True),
        ("invoice_numbers", True),
        ("invoice_number", False),
        ("net_weight_kg", False),
        ("gross_weight_kg", False),
        ("ncm", False),
        ("ncm_or_hs", False),
        ("di_number", False),
        ("reference_internal", False),
        ("reference_client", False),
        ("bl_number", False),
        ("transport_mode", False),
        ("port_of_loading", False),
        ("shipment_date", False),
        ("arrival_date", False),
        ("declaration_type", False),
        ("operational_unit", False),
        ("dispatch_urf", False),
        ("dispatch_modality", False),
        ("transport_carrier", False),
        ("entry_urf", False),
        ("country_of_provenance", False),
        ("importer_address", False),
        ("importer_number", False),
        ("importer_complement", False),
        ("importer_neighborhood", False),
        ("importer_cep", False),
        ("importer_city_uf", False),
        ("importer_country", False),
    ],
    "li": [
        ("importer_name", True),
        ("importer_cnpj", True),
        ("li_number", False),
        ("li_reference", False),
        ("invoice_number", False),
        ("net_weight_kg", False),
        ("gross_weight_kg", False),
        ("ncm", False),
        ("ncm_or_hs", False),
        ("country_of_origin", False),
        ("country_of_provenance", False),
        ("country_of_acquisition", False),
        ("country_proc", False),
        ("exporter_name", False),
        ("quantity", False),
        ("unit_measure", False),
        ("incoterm", False),
        ("importer_address", False),
        ("importer_number", False),
        ("importer_complement", False),
        ("importer_city", False),
        ("importer_country", False),
        ("exporter_address", False),
        ("exporter_city", False),
        ("exporter_country", False),
        ("dispatch_urf", False),
        ("entry_urf", False),
        ("currency", False),
        ("purchase_condition", False),
        ("unit_commercial", False),
    ],
}
DOC_KIND_FIELD_SPEC["hbl"] = list(DOC_KIND_FIELD_SPEC["bl"])


DOC_KIND_EXTRACTION_GUIDE: Dict[str, List[str]] = {
    "invoice": [
        "Find invoice number and issue date.",
        "Find payment terms, currency, and incoterm.",
        "For incoterm, return only the 3-letter uppercase code (for example: FCA, FOB, CIF), never location text.",
        "Find importer/consignee legal name and CNPJ.",
        "Find shipper/exporter name.",
        "Find country of origin, acquisition, and provenance when available.",
        "Find total net and gross weight in kg.",
        "Find total quantity and line items when present.",
    ],
    "packing_list": [
        "Find packing/invoice reference number used in the document.",
        "Find importer/consignee legal name and CNPJ.",
        "Find shipper/exporter name when available.",
        "Find package/carton total, net weight, gross weight, and total measurement m3.",
        "Find item table rows with model, package count, weights, and m3 when available.",
    ],
    "bl": [
        "Find shipper/exporter legal name.",
        "Find consignee/importer legal name and CNPJ.",
        "Find NCM/HS code.",
        "Find gross weight in kg.",
        "Find freight terms (collect/prepaid) when available.",
        "Find measurement, notify party, port of loading, and port of discharge when available.",
    ],
    "hbl": [
        "Apply the same extraction rules used for BL documents.",
    ],
    "di": [
        "Find importer legal name and CNPJ.",
        "Find all invoice numbers and first invoice number alias.",
        "Find net/gross weight and NCM/HS when available.",
        "Find DI number, BL number, transport mode, and loading/arrival dates when available.",
        "Find declaration metadata and importer address block when available.",
    ],
    "li": [
        "Find importer legal name and CNPJ.",
        "Find LI number, LI reference, and invoice number when available.",
        "Find net/gross weight, NCM/HS, and origin/provenance/acquisition countries when available.",
        "Find exporter name and exporter/importer address details when available.",
        "Find quantity, unit, currency, purchase condition, and incoterm when available.",
        "For incoterm, return only the 3-letter uppercase code (for example: FCA, FOB, CIF), never location text.",
    ],
}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_json(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: Path, obj: dict) -> None:
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _to_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _stage02_trace_enabled() -> bool:
    return _to_bool_env("DOCREADER_STAGE2_LLM_DETAILED_LOG", False)


def _run_debug_log_file() -> str:
    return str(os.getenv("DOCREADER_RUN_DEBUG_LOG_FILE", "")).strip()


def _append_run_debug_log(line: str) -> None:
    target = _run_debug_log_file()
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Keep extraction running even if debug file write fails.
        pass


def stage02_trace(step: str, details: Optional[Dict[str, Any]] = None, level: str = "INFO") -> None:
    if not _stage02_trace_enabled():
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = f"[{ts}] [Stage02-LLM] [{level}] {step}"
    if details:
        try:
            suffix = " :: " + json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            suffix = " :: " + str(details)
    else:
        suffix = ""
    line = head + suffix
    print(line)
    _append_run_debug_log(line)


def _command_missing_text(text: str) -> bool:
    t = str(text or "").lower()
    return (
        "is not recognized as an internal or external command" in t
        or "nao e reconhecido como um comando interno" in t
        or "command not found" in t
        or "not found" in t
        or "no such file or directory" in t
    )


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

    stage02_trace(
        "codex.context.read",
        {
            "context_file": context_file,
            "connected": info.get("connected"),
            "provider": info.get("provider"),
            "has_access_token_env": has_access_token,
            "has_identity": bool(info.get("identity")),
        },
    )
    return info


def join_pages(stage01_obj: dict) -> str:
    pages = stage01_obj.get("pages") or []
    parts: List[str] = []
    for pg in pages:
        t = (pg.get("text") or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts).strip()


def _match_any(text: str, patterns: List[str]) -> bool:
    for p in patterns:
        if re.search(p, text, flags=re.I):
            return True
    return False


def normalize_doc_kind_hint(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    aliases = {
        "invoice": "invoice",
        "commercial_invoice": "invoice",
        "packing_list": "packing_list",
        "packing list": "packing_list",
        "pl": "packing_list",
        "bl": "bl",
        "bill_of_lading": "bl",
        "hbl": "hbl",
        "di": "di",
        "li": "li",
    }
    return aliases.get(s)


def detect_kind(full_text: str) -> str:
    text = (full_text or "").upper()

    if _match_any(text, [r"\bHBL\b", r"HOUSE\s+BILL"]):
        return "hbl"
    if _match_any(text, [r"PACKING\s+LIST", r"\bROMANEIO\b"]):
        return "packing_list"
    if _match_any(
        text,
        [
            r"CONFERENCI[AA]\s+DI",
            r"RASCUNHO\s+DA\s+DI",
            r"RASCUNHO\s+DI",
            r"DECLARA\w*\s+DE\s+IMPORTA",
            r"\bNR\.?\s*DI\b",
            r"\bN\w*MERO\s+DA\s+DI\b",
        ],
    ):
        return "di"
    if _match_any(
        text,
        [
            r"CONFERENCI[AA]\s+LI",
            r"RASCUNHO\s+LI",
            r"LICEN[CC]A\s+DE\s+IMPORTA",
            r"\bNR\.?\s*LI\b",
            r"\bN\w*MERO\s+DA\s+LI\b",
            r"\bNREFERENCIA\s+LI\b",
        ],
    ):
        return "li"
    if _match_any(
        text,
        [r"COMMERCIAL\s+INVOICE", r"INVOICE", r"PRO[-\s]?FORMA", r"FATTURA"],
    ):
        return "invoice"
    if _match_any(text, [r"BILL\s+OF\s+LADING", r"\bB/L\b", r"\bBL\b"]):
        return "bl"

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
    rules = DOC_KIND_EXTRACTION_GUIDE.get(doc_kind, [])
    rules_block = "\n".join(f"- {item}" for item in rules)
    if not rules_block:
        rules_block = "- Extract all template fields strictly from the source content."

    template_json = json.dumps(stage02_template, ensure_ascii=False, indent=2)
    stage01_json = json.dumps(stage01_obj, ensure_ascii=False, indent=2)

    return (
        "You are extracting fields for Stage 02 (importation) from a Stage 01 JSON.\n"
        "Return only valid JSON. Do not include markdown, comments, explanations, or code fences.\n"
        "Do not add new keys. Do not remove keys.\n"
        "Use only the provided Stage 01 content. Do not guess.\n"
        "If a value is not found, set present=false, value=null, evidence=[].\n"
        "Keep required exactly as in template.\n"
        "Keep source exactly as in template.\n"
        "Set method to a short string (for example: llm_manual).\n"
        "Incoterm rule (critical): if field 'incoterm' exists, output only one of these uppercase codes:\n"
        "EXW, FCA, FAS, FOB, CFR, CIF, CPT, CIP, DAP, DPU, DDP, DAT.\n"
        "Normalize punctuation and trailing location words.\n"
        "Example: 'F.C.A. NAGOYA' => 'FCA'.\n"
        "If no valid incoterm code is found, use present=false and value=null.\n"
        "\n"
        f"Document kind: {doc_kind}\n"
        "Business extraction rules:\n"
        f"{rules_block}\n"
        "\n"
        "TEMPLATE_STAGE02_JSON:\n"
        f"{template_json}\n"
        "\n"
        "STAGE01_JSON:\n"
        f"{stage01_json}\n"
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
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

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
    expected_keys = set(FIELD_META_KEYS)
    if keys != expected_keys:
        missing = sorted(expected_keys - keys)
        extra = sorted(keys - expected_keys)
        raise Stage02LLMError(
            f"Field '{field_name}' has invalid keys. missing={missing} extra={extra}"
        )

    present = field_obj.get("present")
    required = field_obj.get("required")
    value = field_obj.get("value")
    evidence = field_obj.get("evidence")
    method = field_obj.get("method")

    if not isinstance(present, bool):
        raise Stage02LLMError(f"Field '{field_name}' has non-boolean 'present'.")
    if not isinstance(required, bool):
        raise Stage02LLMError(f"Field '{field_name}' has non-boolean 'required'.")
    if required != bool(expected_required):
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


def normalize_llm_stage02_payload(
    payload: Dict[str, Any],
    template: Dict[str, Any],
    doc_kind: str,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    template_fields = template["fields"]
    template_field_keys = set(template_fields.keys())

    if not isinstance(payload, dict):
        raise Stage02LLMError("LLM payload must be an object.")

    if "fields" in payload:
        fields_payload = payload.get("fields")
        warnings_payload = payload.get("warnings", [])
    else:
        fields_payload = payload
        warnings_payload = []

    if not isinstance(fields_payload, dict):
        raise Stage02LLMError("LLM payload does not contain a valid 'fields' object.")

    got_field_keys = set(fields_payload.keys())
    if got_field_keys != template_field_keys:
        missing = sorted(template_field_keys - got_field_keys)
        extra = sorted(got_field_keys - template_field_keys)
        raise Stage02LLMError(
            f"Field keys mismatch for doc_kind={doc_kind}. missing={missing} extra={extra}"
        )

    normalized_fields: Dict[str, Any] = {}
    for field_name, template_meta in template_fields.items():
        normalized_fields[field_name] = _normalize_field(
            field_name=field_name,
            expected_required=bool(template_meta.get("required")),
            field_obj=fields_payload[field_name],
        )

    missing_required_fields = [
        k
        for k, meta in normalized_fields.items()
        if bool(meta.get("required")) and not bool(meta.get("present"))
    ]
    warnings = _normalize_warnings(warnings_payload)
    return normalized_fields, missing_required_fields, warnings


def validate_final_stage02_output(out_obj: Dict[str, Any], doc_kind: str) -> None:
    if not isinstance(out_obj, dict):
        raise Stage02LLMError("Final Stage 02 output is not an object.")

    top_keys = set(out_obj.keys())
    if top_keys != set(TOP_LEVEL_KEYS):
        missing = sorted(set(TOP_LEVEL_KEYS) - top_keys)
        extra = sorted(top_keys - set(TOP_LEVEL_KEYS))
        raise Stage02LLMError(
            f"Final Stage 02 top-level keys mismatch. missing={missing} extra={extra}"
        )

    source = out_obj.get("source")
    if not isinstance(source, dict):
        raise Stage02LLMError("Final Stage 02 source is not an object.")
    source_keys = set(source.keys())
    if source_keys != set(SOURCE_KEYS):
        missing = sorted(set(SOURCE_KEYS) - source_keys)
        extra = sorted(source_keys - set(SOURCE_KEYS))
        raise Stage02LLMError(
            f"Final Stage 02 source keys mismatch. missing={missing} extra={extra}"
        )

    expected_fields = set(build_fields_template(doc_kind).keys())
    fields = out_obj.get("fields")
    if not isinstance(fields, dict):
        raise Stage02LLMError("Final Stage 02 fields is not an object.")

    got_fields = set(fields.keys())
    if got_fields != expected_fields:
        missing = sorted(expected_fields - got_fields)
        extra = sorted(got_fields - expected_fields)
        raise Stage02LLMError(
            f"Final Stage 02 field keys mismatch. missing={missing} extra={extra}"
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

    fd, tmp_path = tempfile.mkstemp(prefix="stage02_llm_codex_", suffix=".txt")
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
    is_bare_command = not ("\\" in codex_bin or "/" in codex_bin)
    is_cmd_script = codex_bin.lower().endswith(".cmd") or codex_bin.lower().endswith(".bat")
    use_shell = is_windows and (is_bare_command or is_cmd_script)
    shell_cmd = subprocess.list2cmdline(cmd) if use_shell else None

    stage02_trace(
        "codex.exec.begin",
        {
            "cwd": str(cwd),
            "codex_bin": codex_bin,
            "model": model_name or None,
            "timeout_sec": timeout,
            "use_shell": use_shell,
            "is_bare_command": is_bare_command,
            "is_cmd_script": is_cmd_script,
            "prompt_chars": len(prompt or ""),
            "output_file": str(output_file),
            "command": shell_cmd if use_shell else cmd,
        },
    )

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
        stage02_trace("codex.exec.timeout", {"timeout_sec": timeout}, level="ERROR")
        raise Stage02LLMError(f"Codex CLI timeout after {timeout}s.") from exc
    except FileNotFoundError as exc:
        stage02_trace("codex.exec.missing", {"codex_bin": codex_bin}, level="ERROR")
        raise Stage02LLMError(
            f"Codex CLI executable not found: '{codex_bin}'."
        ) from exc
    except Exception as exc:
        stage02_trace(
            "codex.exec.exception",
            {"codex_bin": codex_bin, "error": str(exc)},
            level="ERROR",
        )
        raise

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    stage02_trace(
        "codex.exec.completed",
        {
            "returncode": proc.returncode,
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
        },
    )

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

    stage02_trace(
        "codex.exec.response_ready",
        {"response_chars": len(response)},
    )

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
    stage02_trace(
        "document.extract.begin",
        {
            "stage01_file": stage01_file,
            "original_file": original_file,
            "doc_kind": doc_kind,
            "doc_kind_hint": doc_kind_hint,
            "llm_client_mocked": llm_client is not None,
            "model": model or None,
            "timeout_sec": timeout_sec,
        },
    )
    template = build_stage02_template(
        stage01_file=stage01_file,
        original_file=original_file,
        doc_kind=doc_kind,
        doc_kind_hint=doc_kind_hint,
    )
    prompt = build_prompt(stage01_obj=stage01_obj, stage02_template=template, doc_kind=doc_kind)
    stage02_trace(
        "document.prompt.built",
        {
            "stage01_file": stage01_file,
            "prompt_chars": len(prompt),
            "template_field_count": len(template.get("fields", {})),
        },
    )

    if llm_client is None:
        raw = run_codex_cli_prompt(
            prompt=prompt,
            cwd=cwd,
            model=model,
            timeout_sec=timeout_sec,
        )
    else:
        raw = llm_client(prompt, cwd)
    stage02_trace(
        "document.response.received",
        {
            "stage01_file": stage01_file,
            "response_chars": len(raw or ""),
        },
    )

    try:
        payload = parse_model_json(raw)
        stage02_trace(
            "document.response.json_parsed",
            {
                "stage01_file": stage01_file,
                "top_keys": sorted(list(payload.keys())),
            },
        )
        fields, missing_required_fields, warnings = normalize_llm_stage02_payload(
            payload=payload,
            template=template,
            doc_kind=doc_kind,
        )
        stage02_trace(
            "document.response.normalized",
            {
                "stage01_file": stage01_file,
                "missing_required_count": len(missing_required_fields),
                "warnings_count": len(warnings),
            },
        )
    except Stage02LLMError as exc:
        snippet = (raw or "").strip().replace("\n", " ")
        stage02_trace(
            "document.response.invalid",
            {
                "stage01_file": stage01_file,
                "error": str(exc),
                "snippet": snippet[:500],
            },
            level="ERROR",
        )
        raise Stage02LLMError(
            f"{exc} | llm_response_snippet={snippet[:800]}"
        ) from exc

    return fields, missing_required_fields, warnings


def run_stage02_llm_for_importation(
    in_dir: Path,
    out_dir: Path,
    verbose: bool = True,
    llm_client: Optional[Callable[[str, Path], str]] = None,
    model: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stage02_trace(
        "run.begin",
        {
            "input_dir": str(in_dir),
            "output_dir": str(out_dir),
            "verbose": verbose,
            "model": model or None,
            "timeout_sec": timeout_sec,
        },
    )

    files = sorted(in_dir.glob("*_extracted.json"))
    if not files:
        stage02_trace("run.no_files", {"input_dir": str(in_dir)}, level="WARN")
        return {
            "processed_count": 0,
            "warnings": [f"No *_extracted.json files found in: {in_dir}"],
            "documents": [],
        }

    summary_docs: List[dict] = []
    all_warnings: List[str] = []

    total = len(files)
    for idx, p in enumerate(files, start=1):
        stage02_trace(
            "document.load.begin",
            {"index": idx, "total": total, "stage01_file": p.name},
        )
        obj = read_json(p)
        original_file = obj.get("file") or p.name.replace("_extracted.json", ".pdf")
        full_text = join_pages(obj)
        doc_kind_hint = normalize_doc_kind_hint(obj.get("doc_kind_hint")) or ""
        doc_kind = doc_kind_hint or detect_kind(full_text)
        stage02_trace(
            "document.kind.resolved",
            {
                "stage01_file": p.name,
                "doc_kind": doc_kind,
                "doc_kind_hint": doc_kind_hint,
                "text_chars": len(full_text or ""),
            },
        )

        if verbose:
            print(f"[Stage02-LLM] {idx}/{total} processing {p.name} (kind={doc_kind})")

        if doc_kind not in DOC_KIND_FIELD_SPEC:
            fields, missing_required_fields, warnings = ({}, [f"doc_kind unknown: {doc_kind}"], [])
        else:
            fields, missing_required_fields, warnings = extract_fields_with_llm_for_document(
                stage01_obj=obj,
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
            "missing_required_fields": missing_required_fields,
            "warnings": warnings,
        }
        if doc_kind in DOC_KIND_FIELD_SPEC:
            validate_final_stage02_output(out_obj, doc_kind)

        out_name = p.name.replace("_extracted.json", "_fields.json").replace("__", "_")
        out_path = out_dir / out_name
        write_json(out_path, out_obj)
        stage02_trace(
            "document.output.written",
            {
                "stage01_file": p.name,
                "stage02_file": out_name,
                "missing_required_count": len(missing_required_fields),
                "warnings_count": len(warnings),
                "output_path": str(out_path),
            },
        )

        summary_docs.append(
            {
                "doc_kind": doc_kind,
                "original_file": original_file,
                "stage01_file": p.name,
                "stage02_file": out_name,
                "missing_required_fields": missing_required_fields,
                "warnings": warnings,
            }
        )
        all_warnings.extend(warnings)

        if verbose:
            print(
                f"[Stage02-LLM] OK -> {out_name} | missing={len(missing_required_fields)} | warnings={len(warnings)}"
            )

    codex_runtime = read_codex_runtime_context()
    summary = {
        "generated_at": now_iso(),
        "flow": "importation",
        "input_folder": str(in_dir),
        "output_folder": str(out_dir),
        "codex_auth_context": codex_runtime,
        "documents": summary_docs,
    }
    write_json(out_dir / "_stage02_summary.json", summary)
    stage02_trace(
        "run.summary.written",
        {
            "summary_path": str(out_dir / "_stage02_summary.json"),
            "processed_count": len(summary_docs),
            "warnings_count": len(all_warnings),
        },
    )

    if verbose:
        print("[Stage02-LLM] Completed.")
    stage02_trace("run.completed", {"processed_count": len(summary_docs)})

    return {
        "processed_count": len(summary_docs),
        "warnings": all_warnings,
        "codex_auth_context": codex_runtime,
        "documents": summary_docs,
    }
