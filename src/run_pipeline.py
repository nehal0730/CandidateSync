"""
run_pipeline.py
==============
Main pipeline orchestrator.

Pipeline stages (in order):
  ① Ingest    — detect source types, load files
  ② Extract   — run source-specific extractors
  ③ Merge     — group by candidate, merge IntermediateRecords → CanonicalRecord
  ④ Confidence— compute per-field and overall confidence scores
  ⑤ Project   — apply runtime config to produce custom output
  ⑥ Validate  — validate output against schema before returning

A corrupt or missing source file is logged and skipped — pipeline never crashes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from models import IntermediateRecord, CanonicalRecord
from extractors import (
    extract_from_csv,
    extract_from_ats_json,
    extract_from_resume,
    extract_from_notes,
)
from pipeline.merger     import merge_records
from pipeline.confidence import compute_confidence
from pipeline.projector  import project, ProjectionError
from pipeline.validator  import validate_output, ValidationError

log = logging.getLogger(__name__)


# ── Source type detection ─────────────────────────────────────────────────────

def _detect_source_type(path: str) -> Optional[str]:
    """
    Detect source type from file extension + content sniff.
    Returns: 'csv' | 'ats_json' | 'resume' | 'notes' | None
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        return "csv"

    if ext == ".json":
        return "ats_json"

    if ext in (".pdf", ".docx", ".doc"):
        return "resume"

    if ext == ".txt":
        # Sniff: if it looks like a JSON blob inside .txt, treat as ATS JSON
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                snippet = f.read(200).strip()
            if snippet.startswith("{") or snippet.startswith("["):
                return "ats_json"
        except Exception:
            pass
        return "notes"

    return None


# ── Single-source extractor dispatcher ───────────────────────────────────────

def _extract(path: str, source_type: str) -> List[IntermediateRecord]:
    """Dispatch to the correct extractor based on source_type."""
    try:
        if source_type == "csv":
            return extract_from_csv(path)
        elif source_type == "ats_json":
            return extract_from_ats_json(path)
        elif source_type == "resume":
            return extract_from_resume(path)
        elif source_type == "notes":
            return extract_from_notes(path)
        else:
            r = IntermediateRecord(source_name="unknown", source_weight=0.0)
            r.extraction_error = f"Unsupported source type for: {path}"
            return [r]
    except Exception as exc:
        r = IntermediateRecord(source_name=source_type, source_weight=0.0)
        r.extraction_error = f"Unexpected extractor error: {exc}"
        log.exception("Extractor crashed for %s", path)
        return [r]


# ── Public API ────────────────────────────────────────────────────────────────

def run_pipeline(
    source_paths: List[str],
    config: Optional[Dict[str, Any]] = None,
    strict_validation: bool = False,
) -> List[Dict[str, Any]]:
    """
    Run the full transformer pipeline end-to-end.

    Parameters
    ----------
    source_paths       : List of input file paths (any mix of CSV/JSON/PDF/DOCX/TXT).
    config             : Runtime projection config dict. If None, default schema used.
    strict_validation  : If True, raises ValidationError on schema violations.

    Returns
    -------
    List of projected + validated output dicts (one per unique candidate).

    Raises
    ------
    ProjectionError  : if on_missing='error' and a required field is absent.
    ValidationError  : if strict_validation=True and output fails schema check.
    """
    config = config or {}

    # ── ① Ingest + ② Extract ──────────────────────────────────────────────────
    all_intermediate: List[IntermediateRecord] = []
    ingestion_log: List[Dict[str, Any]] = []

    for path in source_paths:
        if not os.path.exists(path):
            log.warning("Source file not found (skipped): %s", path)
            ingestion_log.append({"path": path, "status": "not_found"})
            continue

        source_type = _detect_source_type(path)
        if not source_type:
            log.warning("Cannot detect source type (skipped): %s", path)
            ingestion_log.append({"path": path, "status": "unsupported_type"})
            continue

        log.info("Extracting from %s (%s)", path, source_type)
        records = _extract(path, source_type)

        for rec in records:
            if rec.extraction_error:
                log.warning("Extraction error in %s: %s", path, rec.extraction_error)
                ingestion_log.append({
                    "path":   path,
                    "source": rec.source_name,
                    "status": "partial_error",
                    "error":  rec.extraction_error,
                })
            else:
                ingestion_log.append({"path": path, "source": rec.source_name, "status": "ok"})

        all_intermediate.extend(records)

    if not all_intermediate:
        log.error("No records extracted from any source.")
        return []

    # ── ③ Merge ────────────────────────────────────────────────────────────────
    # merge_records returns (CanonicalRecord, contributing_records) pairs so that
    # confidence scoring below is correctly scoped to each candidate's own sources.
    merged_pairs = merge_records(all_intermediate)
    log.info("Merged into %d unique candidate(s).", len(merged_pairs))

    # ── ④ Confidence ──────────────────────────────────────────────────────────
    canonical_records: List[CanonicalRecord] = []
    for canon, contributing_recs in merged_pairs:
        compute_confidence(canon, contributing_recs)
        canonical_records.append(canon)

    # ── ⑤ Project + ⑥ Validate ───────────────────────────────────────────────
    outputs: List[Dict[str, Any]] = []

    for canon in canonical_records:
        try:
            projected = project(canon, config)
        except ProjectionError as exc:
            log.error("Projection error for candidate %s: %s",
                      canon.candidate_id, exc)
            raise

        is_valid, errors = validate_output(
            projected, config, strict=strict_validation
        )
        if not is_valid:
            log.warning(
                "Validation warnings for candidate %s: %s",
                canon.candidate_id, errors
            )
            projected["_validation_warnings"] = errors

        outputs.append(projected)

    return outputs


# ── Config loader ─────────────────────────────────────────────────────────────

def load_config(config_path: Optional[str]) -> Dict[str, Any]:
    """Load a JSON config file. Returns {} (default config) if path is None."""
    if not config_path:
        return {}
    if not os.path.exists(config_path):
        log.warning("Config file not found: %s — using defaults.", config_path)
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        log.error("Invalid config JSON: %s", exc)
        return {}