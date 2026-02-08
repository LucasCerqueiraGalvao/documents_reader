# hbl.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    from .bl import extract_bl_fields
except ImportError:  # pragma: no cover
    from bl import extract_bl_fields


def extract_hbl_fields(text: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    # HBL segue as mesmas regras do BL para os campos principais.
    return extract_bl_fields(text)
