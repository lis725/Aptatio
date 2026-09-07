from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


class PublicDataAdapterError(RuntimeError):
    pass


def load_manifest(path_or_manifest: str | Path | dict) -> dict:
    if isinstance(path_or_manifest, dict):
        return path_or_manifest
    path = Path(path_or_manifest)
    return json.loads(path.read_text(encoding="utf-8"))


def source_entry(manifest: dict, source_key: str) -> dict:
    sources = manifest.get("sources", {})
    if source_key not in sources:
        raise PublicDataAdapterError(f"Public data manifest is missing sources.{source_key}.")
    return sources[source_key]


def validate_required_columns(df: pd.DataFrame, required_columns: Iterable[str], source_key: str) -> None:
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise PublicDataAdapterError(f"{source_key} is missing required columns: {missing}")


def read_local_table(path: str | Path, source_key: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise PublicDataAdapterError(f"{source_key} local_file was configured but does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".tsv"}:
        return pd.read_csv(path, sep="\t")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise PublicDataAdapterError(f"{source_key} local_file must be CSV, TSV, XLS, or XLSX: {path}")


def write_provenance(metadata: dict, output_dir: str | Path | None, source_key: str) -> None:
    if output_dir is None:
        return
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{source_key}_provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def load_public_source(
    manifest: str | Path | dict,
    source_key: str,
    required_columns: Iterable[str] = (),
    output_dir: str | Path | None = None,
    offline_demo_fallback: bool = False,
) -> tuple[pd.DataFrame, dict]:
    manifest_obj = load_manifest(manifest)
    entry = source_entry(manifest_obj, source_key)
    metadata = {
        "source_key": source_key,
        "title": entry.get("title"),
        "url": entry.get("url"),
        "dataset_id": entry.get("dataset_id"),
        "retrieval_date": entry.get("retrieval_date"),
        "retrieval_status": entry.get("retrieval_status"),
        "reporting_period": entry.get("reporting_period"),
        "geography": entry.get("geography"),
        "local_cache_path": entry.get("local_cache_path") or entry.get("cache_raw_under"),
        "manifest_checksum_sha256": entry.get("checksum_sha256"),
        "columns_used": entry.get("columns_used", []),
        "transformation": entry.get("transformation"),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "public_data_only": True,
    }

    local_file = entry.get("local_file")
    if local_file:
        local_path = Path(local_file)
        df = read_local_table(local_path, source_key)
        column_mapping = entry.get("column_mappings", {}) or {}
        if column_mapping:
            df = df.rename(columns=column_mapping)
        validate_required_columns(df, required_columns, source_key)
        metadata["load_mode"] = "local_file"
        metadata["local_file"] = str(local_path)
        metadata["checksum_sha256"] = hashlib.sha256(local_path.read_bytes()).hexdigest()
        metadata["columns_loaded"] = list(df.columns)
        write_provenance(metadata, output_dir, source_key)
        return df, metadata

    allow_online = bool(entry.get("allow_online", False))
    if allow_online and entry.get("url"):
        try:
            df = pd.read_csv(entry["url"])
            column_mapping = entry.get("column_mappings", {}) or {}
            if column_mapping:
                df = df.rename(columns=column_mapping)
            validate_required_columns(df, required_columns, source_key)
            metadata["load_mode"] = "online_configured_url"
            metadata["columns_loaded"] = list(df.columns)
            metadata["checksum_sha256"] = None
            metadata["checksum_note"] = "Direct URL load was not persisted; configure a cache path for byte-level reproducibility."
            write_provenance(metadata, output_dir, source_key)
            return df, metadata
        except Exception as exc:  # pragma: no cover - network is optional.
            if not offline_demo_fallback:
                raise PublicDataAdapterError(
                    f"{source_key} online load failed. Supply a local_file in the manifest or enable offline_demo_fallback."
                ) from exc
            metadata["online_error"] = str(exc)

    if offline_demo_fallback:
        metadata["load_mode"] = "offline_demo_fallback"
        metadata["data_source_warning"] = "offline_fallback_distributions"
        metadata["source_mode"] = "offline_demo_fallback"
        metadata["checksum_sha256"] = None
        metadata["columns_loaded"] = []
        metadata["fallback_reason"] = "No local_file was supplied and online loading is disabled or unavailable."
        write_provenance(metadata, output_dir, source_key)
        return pd.DataFrame(), metadata

    raise PublicDataAdapterError(
        f"{source_key} has no usable public input. Add local_file to the manifest, "
        "set allow_online=true with a URL, or run the public semi-synthetic generator in offline_demo_fallback mode."
    )
