"""Immersive reading engine — materials, locators, annotations, export.

A *material* is a file the user reads: it is cut once into **units** and stored
per-unit, so every later operation addresses it by **locator** (a 1-indexed unit
number that means page / chapter / slide / section depending on the format).
That one abstraction is what lets a model cite "page 12" and the reader scroll
to page 12 without either side knowing the file is a PDF.

Layering, bottom-up — each layer depends only on the ones above it in this list:

* :mod:`.models` — dataclasses and errors. No I/O, no imports from siblings.
* :mod:`.extract` — file → units. The only module that knows about formats.
* :mod:`.search` — pure locator-addressed matching over ``(locator, text)``.
* :mod:`.store` — durable per-material layout, atomic writes, annotations.
* :mod:`.service` — the composition callers use (read / search / outline /
  quote verification).
* :mod:`.export` — the annotated artefacts a user can take away.

Nothing here imports the chat loop, the tool registry or FastAPI, so the engine
is testable on its own and the capability that drives it
(:mod:`deepmentor.capabilities.reading`) stays a thin shell.
"""

from __future__ import annotations

from deepmentor.reading.catalog_models import (
    IngestionStatus,
    MaterialRecord,
    ReadingSessionRecord,
    SourceKind,
    WorkspaceRecord,
    WorkspaceTab,
)
from deepmentor.reading.catalog_store import ReadingCatalogStore
from deepmentor.reading.epub_bilingual import (
    create_epub_pairing,
    delete_epub_pairing,
    list_epub_pairings,
    recommend_epub_candidates,
)
from deepmentor.reading.export import ExportFormat, ExportResult, export_material
from deepmentor.reading.extract import Extraction, extract_material
from deepmentor.reading.models import (
    ANNOTATION_COLORS,
    Annotation,
    AnnotationKind,
    MaterialManifest,
    MaterialNotFound,
    OutlineEntry,
    ReadingBookmark,
    ReadingError,
    ReadingPosition,
    ReadingUpgradeConflict,
    Rect,
    RenderMode,
    SearchHit,
    TextPositionSelector,
    TextQuoteSelector,
    TextSelector,
    UnitKind,
    UnitReference,
)
from deepmentor.reading.search import SearchResult, search_units
from deepmentor.reading.service import (
    QuoteCheck,
    RenderedUnits,
    material_summary,
    parse_locators,
    render_outline,
    render_units,
    search_material,
    verify_quote,
)
from deepmentor.reading.store import ReadingStore, content_hash

__all__ = [
    "ANNOTATION_COLORS",
    "Annotation",
    "AnnotationKind",
    "ExportFormat",
    "ExportResult",
    "Extraction",
    "IngestionStatus",
    "MaterialRecord",
    "MaterialManifest",
    "MaterialNotFound",
    "OutlineEntry",
    "QuoteCheck",
    "ReadingError",
    "ReadingCatalogStore",
    "ReadingBookmark",
    "ReadingPosition",
    "ReadingSessionRecord",
    "ReadingUpgradeConflict",
    "ReadingStore",
    "Rect",
    "RenderedUnits",
    "SearchHit",
    "SearchResult",
    "SourceKind",
    "RenderMode",
    "TextPositionSelector",
    "TextQuoteSelector",
    "TextSelector",
    "UnitKind",
    "UnitReference",
    "WorkspaceRecord",
    "WorkspaceTab",
    "content_hash",
    "create_epub_pairing",
    "delete_epub_pairing",
    "export_material",
    "extract_material",
    "list_epub_pairings",
    "material_summary",
    "parse_locators",
    "render_outline",
    "recommend_epub_candidates",
    "render_units",
    "search_material",
    "search_units",
    "verify_quote",
]
