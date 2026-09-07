from __future__ import annotations

from .public_adapter_base import load_public_source


def load_census_acs_zcta(manifest, output_dir=None, offline_demo_fallback=False):
    return load_public_source(
        manifest=manifest,
        source_key="census_acs_5yr_zcta",
        required_columns=(),
        output_dir=output_dir,
        offline_demo_fallback=offline_demo_fallback,
    )
