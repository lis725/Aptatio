from __future__ import annotations

from .public_adapter_base import load_public_source


def ruca_code_to_area_type(code):
    numeric = float(code)
    if numeric <= 3:
        return "metropolitan"
    if numeric <= 6:
        return "micropolitan"
    if numeric <= 9:
        return "small_town"
    return "rural"


def load_ruca_area_type(manifest, output_dir=None, offline_demo_fallback=False):
    return load_public_source(
        manifest=manifest,
        source_key="usda_ruca_zip",
        required_columns=(),
        output_dir=output_dir,
        offline_demo_fallback=offline_demo_fallback,
    )
