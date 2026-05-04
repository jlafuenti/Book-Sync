"""
Import-source registry.

Each adapter is a singleton instance keyed by `source_key`. Routers and the
scheduler look up adapters here rather than importing them directly, so adding
a source is a one-line registration.
"""

from typing import Dict

from services.import_sources.base import SourceAdapter
from services.import_sources.audible import AudibleSource
from services.import_sources.acsm import AcsmSource


_REGISTRY: Dict[str, SourceAdapter] = {
    AudibleSource.SOURCE_KEY: AudibleSource(),
    AcsmSource.SOURCE_KEY: AcsmSource(),
}


def get_source(source_key: str) -> SourceAdapter:
    if source_key not in _REGISTRY:
        raise KeyError(f"Unknown import source: {source_key}")
    return _REGISTRY[source_key]


def list_sources() -> Dict[str, SourceAdapter]:
    return dict(_REGISTRY)
