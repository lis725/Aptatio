from __future__ import annotations

from .public_adapter_base import load_public_source


def load_cms_provider_data(manifest, output_dir=None, offline_demo_fallback=False):
    return load_public_source(
        manifest=manifest,
        source_key="cms_provider_data_home_health",
        output_dir=output_dir,
        offline_demo_fallback=offline_demo_fallback,
    )
