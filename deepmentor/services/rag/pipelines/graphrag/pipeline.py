"""GraphRAG-backed RAG pipeline orchestration.

Implements the same contract as :class:`LlamaIndexPipeline` (see
``..base.RAGPipeline``) but delegates indexing and retrieval to a local
microsoft/graphrag project. Each KB owns a self-contained GraphRAG project under
its ``version-N`` directory (see ``storage``); documents are parsed to text by
DeepMentor first (see ``ingestion``) so GraphRAG only ever sees ``.txt`` input.

GraphRAG is an optional dependency: every method fails with a clear, actionable
message when it is not installed instead of an opaque ``ImportError``.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import tempfile
import traceback
from typing import Any, Dict, List, Optional

from deepmentor.logging import PROCESS_LOG_PRIVATE_ATTR
from deepmentor.runtime.home import get_runtime_data_root
from deepmentor.services.rag.index_versioning import (
    resolve_storage_dir_for_read,
    resolve_storage_dir_for_rebuild,
)
from deepmentor.services.rag.kb_paths import resolve_kb_dir

from . import config as gr_config
from . import ingestion, storage

logger = logging.getLogger(__name__)

DEFAULT_KB_BASE_DIR = str(get_runtime_data_root() / "knowledge_bases")


class GraphRagPipeline:
    """Index/retrieve KB content via a local microsoft/graphrag project."""

    def __init__(self, kb_base_dir: Optional[str] = None, **_: Any) -> None:
        self.logger = logging.getLogger(__name__)
        self.kb_base_dir = kb_base_dir or DEFAULT_KB_BASE_DIR

    # ----- helpers --------------------------------------------------------

    def _ensure_available(self) -> None:
        if not gr_config.is_graphrag_available():
            raise gr_config.GraphRagNotAvailableError(
                "GraphRAG is not installed. Install it with "
                "`pip install 'deepmentor[graphrag]'` to use GraphRAG knowledge bases."
            )

    def _resolve_mode(self, kb_name: str, kwargs: dict[str, Any]) -> str:
        from ..modes import resolve_kb_mode

        return resolve_kb_mode(
            self.kb_base_dir,
            kb_name,
            storage.PROVIDER,
            explicit=kwargs.get("mode"),
            supported=gr_config.SUPPORTED_MODES,
            default=gr_config.DEFAULT_MODE,
        )

    def _cleanup_failed_version_dir(self, root_dir: Path) -> None:
        try:
            if root_dir.is_dir() and not (root_dir / storage.META_FILENAME).exists():
                shutil.rmtree(root_dir)
        except Exception as exc:  # pragma: no cover - best-effort
            self.logger.warning("Could not clean up failed version dir %s: %s", root_dir, exc)

    async def _preflight_settings(self, settings: dict[str, Any]) -> None:
        """Probe a settings snapshot without touching an existing KB version."""
        from . import engine

        with tempfile.TemporaryDirectory(prefix="deepmentor-graphrag-preflight-") as temp_dir:
            probe_root = Path(temp_dir)
            gr_config.write_settings_payload(probe_root, settings)
            # Check the cheaper embedding request first so an invalid endpoint
            # does not trigger a structured completion call unnecessarily.
            await engine.preflight_embedding(probe_root)
            await engine.preflight_completion(probe_root)

    # ----- indexing -------------------------------------------------------

    async def initialize(self, kb_name: str, file_paths: List[str], **kwargs) -> bool:
        self._ensure_available()
        kb_dir = resolve_kb_dir(self.kb_base_dir, kb_name)
        root_dir = resolve_storage_dir_for_rebuild(kb_dir, None)
        self.logger.info(
            "Initializing KB '%s' with %d file(s) using GraphRAG", kb_name, len(file_paths)
        )
        try:
            gr_config.write_settings(root_dir)
            count = await ingestion.prepare_input(file_paths, root_dir)
            if count == 0:
                self.logger.error("GraphRAG: no extractable documents for '%s'", kb_name)
                self._cleanup_failed_version_dir(root_dir)
                return False
            await self._build(root_dir, is_update=False)
            storage.write_meta(root_dir)
            self.logger.info("KB '%s' initialized with GraphRAG (%d docs)", kb_name, count)
            return True
        except Exception as exc:
            self.logger.error("Failed to initialize GraphRAG KB: %s", exc)
            self.logger.error(
                traceback.format_exc(),
                extra={PROCESS_LOG_PRIVATE_ATTR: True},
            )
            self._cleanup_failed_version_dir(root_dir)
            raise

    async def add_documents(self, kb_name: str, file_paths: List[str], **kwargs) -> bool:
        self._ensure_available()
        kb_dir = resolve_kb_dir(self.kb_base_dir, kb_name)
        existing = resolve_storage_dir_for_read(kb_dir, None)
        is_update = existing is not None and storage.has_output(existing)
        root_dir = (
            existing if existing is not None else resolve_storage_dir_for_rebuild(kb_dir, None)
        )

        self.logger.info(
            "Adding %d document(s) to GraphRAG KB '%s' (update=%s)",
            len(file_paths),
            kb_name,
            is_update,
        )
        try:
            # Refresh settings so a changed model/endpoint is picked up.
            settings = gr_config.build_settings()
            if is_update:
                # An update reuses the active version directory. Validate the
                # exact settings snapshot first so a bad provider configuration
                # cannot overwrite a working settings.yaml or add input files.
                await self._preflight_settings(settings)
            gr_config.write_settings_payload(root_dir, settings)
            count = await ingestion.prepare_input(file_paths, root_dir)
            if count == 0:
                self.logger.warning("GraphRAG: no extractable documents to add for '%s'", kb_name)
                return False
            await self._build(
                root_dir,
                is_update=is_update,
                preflight_embedding_model=not is_update,
            )
            storage.write_meta(root_dir)
            self.logger.info("Added %d doc(s) to GraphRAG KB '%s'", count, kb_name)
            return True
        except Exception as exc:
            self.logger.error("Failed to add documents to GraphRAG KB: %s", exc)
            self.logger.error(
                traceback.format_exc(),
                extra={PROCESS_LOG_PRIVATE_ATTR: True},
            )
            if not is_update:
                self._cleanup_failed_version_dir(root_dir)
            raise

    async def _build(
        self,
        root_dir: Path,
        *,
        is_update: bool,
        preflight_embedding_model: bool = True,
    ) -> None:
        from . import engine

        await engine.build(
            root_dir,
            is_update=is_update,
            preflight_embedding_model=preflight_embedding_model,
        )

    # ----- retrieval ------------------------------------------------------

    async def search(self, query: str, kb_name: str, **kwargs) -> Dict[str, Any]:
        kb_dir = resolve_kb_dir(self.kb_base_dir, kb_name)
        root_dir = resolve_storage_dir_for_read(kb_dir, None)

        if root_dir is None or not storage.has_output(root_dir):
            return {
                "query": query,
                "answer": (
                    "This GraphRAG knowledge base has no index yet. Add documents before querying."
                ),
                "content": "",
                "sources": [],
                "provider": storage.PROVIDER,
                "needs_reindex": True,
            }

        mode = self._resolve_mode(kb_name, kwargs)
        try:
            self._ensure_available()
            from . import engine

            response, context_data = await engine.search(root_dir, query, mode)
        except gr_config.GraphRagNotAvailableError as exc:
            return self._error_result(query, exc, error_type="not_configured")
        except Exception as exc:
            self.logger.error("GraphRAG search failed: %s", exc)
            self.logger.error(
                traceback.format_exc(),
                extra={PROCESS_LOG_PRIVATE_ATTR: True},
            )
            return self._error_result(query, exc, error_type="retrieval_error")

        result: Dict[str, Any] = {
            "query": query,
            "answer": response,
            "content": response,
            "sources": _context_to_sources(context_data, _load_text_unit_source_names(root_dir)),
            "provider": storage.PROVIDER,
            "mode": mode,
        }
        graph = _context_to_graph(context_data, _load_graph_entity_metadata(root_dir))
        if graph is not None:
            result["graph"] = graph
        return result

    def _error_result(self, query: str, exc: Exception, *, error_type: str) -> Dict[str, Any]:
        return {
            "query": query,
            "answer": str(exc),
            "content": "",
            "sources": [],
            "provider": storage.PROVIDER,
            "error_type": error_type,
        }

    # ----- lifecycle ------------------------------------------------------

    async def delete(self, kb_name: str, **kwargs) -> bool:
        kb_dir = resolve_kb_dir(self.kb_base_dir, kb_name)
        if kb_dir.exists():
            shutil.rmtree(kb_dir)
            self.logger.info("Deleted GraphRAG KB '%s'", kb_name)
            return True
        return False


def _load_text_unit_source_names(root_dir: Path) -> Dict[str, str]:
    """Map GraphRAG text-unit IDs and document stems to raw KB source names.

    GraphRAG search context exposes text-unit IDs, not the source document name.
    The index output retains ``document_id``; joining it with ``documents`` and
    then matching the prepared ``.txt`` title against KB ``raw/`` lets the UI
    open the original PDF/Markdown source instead of a generated text chunk.
    """
    documents_path = root_dir / "output" / "documents.parquet"
    if not documents_path.exists():
        return {}

    try:
        import pandas as pd

        documents = pd.read_parquet(documents_path)
        text_units_path = root_dir / "output" / "text_units.parquet"
        text_units = (
            pd.read_parquet(text_units_path) if text_units_path.exists() else pd.DataFrame()
        )
    except Exception as exc:  # pragma: no cover - provenance is best-effort
        logger.warning("Could not read GraphRAG documents: %s", exc)
        return {}

    raw_dir = root_dir.parent / "raw"
    raw_by_stem: Dict[str, str] = {}
    if raw_dir.is_dir():
        for path in raw_dir.rglob("*"):
            if path.is_file():
                raw_by_stem[path.stem.casefold()] = path.relative_to(raw_dir).as_posix()

    names_by_document_id: Dict[str, str] = {}
    for row in documents.to_dict(orient="records"):
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        source_name = raw_by_stem.get(Path(title).stem.casefold()) or title
        document_id = str(row.get("id") or "").strip()
        if document_id:
            names_by_document_id[document_id] = source_name

    names: Dict[str, str] = {}
    for row in text_units.to_dict(orient="records"):
        source_name = names_by_document_id.get(str(row.get("document_id") or "").strip(), "")
        if not source_name:
            continue
        for key in (row.get("id"), row.get("human_readable_id")):
            normalized = str(key or "").strip()
            if normalized:
                names[normalized] = source_name
    return names


def _load_graph_entity_metadata(root_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load indexed entity details for enriching a rendered search subgraph.

    GraphRAG's local-search context intentionally clips and reshapes entities;
    relationship endpoints are not guaranteed to appear in that clipped entity
    list. The indexed entity table is small enough for retrieval and contains
    the stable title/type/description/degree fields needed by the UI.
    """
    entities_path = root_dir / "output" / "entities.parquet"
    if not entities_path.exists():
        return {}

    try:
        import pandas as pd

        entities = pd.read_parquet(entities_path)
    except Exception as exc:  # pragma: no cover - enrichment is best-effort
        logger.warning("Could not read GraphRAG entities for graph metadata: %s", exc)
        return {}

    metadata: Dict[str, Dict[str, Any]] = {}
    for row in entities.to_dict(orient="records"):
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        metadata[title.casefold()] = {
            "type": str(row.get("type") or "").strip(),
            "description": str(row.get("description") or "").strip(),
            "degree": row.get("degree") or 0,
        }
    return metadata


def _context_to_sources(
    context_data: dict[str, Any],
    document_sources: Optional[Dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Map GraphRAG context records into DeepMentor's source-citation shape."""
    sources: list[dict[str, Any]] = []
    document_sources = document_sources or {}
    if not isinstance(context_data, dict):
        return sources
    # ``sources`` are the text units; ``reports`` are community summaries. Prefer
    # the most concrete provenance available.
    for key in ("sources", "reports", "entities"):
        records = context_data.get(key)
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            text = str(rec.get("text") or rec.get("content") or rec.get("description") or "")
            chunk_id = str(rec.get("id") or "")
            source_name = (
                str(rec.get("source") or rec.get("filename") or "").strip()
                or document_sources.get(chunk_id)
                or document_sources.get(str(rec.get("source_id") or ""))
                or ""
            )
            sources.append(
                {
                    "title": str(
                        rec.get("title") or rec.get("name") or source_name or f"GraphRAG {key}"
                    ),
                    "content": text[:200],
                    "source": source_name,
                    "page": "",
                    "chunk_id": chunk_id,
                    "score": rec.get("rank") or rec.get("score") or "",
                }
            )
        if sources:
            break
    return sources


# Panel-size guard: the activity sidebar renders a bounded force-layout, so the
# extracted subgraph is clipped even if a mode/context ever returns more.
_GRAPH_MAX_NODES = 60
_GRAPH_MAX_EDGES = 120
_GRAPH_MAX_ENTITY_DESC_CHARS = 200
_GRAPH_MAX_EDGE_DESC_CHARS = 120


def _context_to_graph(
    context_data: dict[str, Any],
    entity_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Extract the retrieved entities/relationships subgraph for rendering.

    GraphRAG's reformatted context carries ``entities`` (records with
    ``entity``/``description`` in GraphRAG 3.x) and ``relationships`` (records
    with ``source``/``target``/``weight``/``description``) for the local and
    drift search modes. Indexed entity metadata enriches records and relationship
    endpoints clipped out of the context. Global and basic searches have no
    entity edges, in which case ``None`` is returned and the panel shows no
    graph section.
    """
    if not isinstance(context_data, dict):
        return None
    entities = context_data.get("entities")
    relationships = context_data.get("relationships")
    if not isinstance(entities, list) or not entities:
        return None
    if not isinstance(relationships, list) or not relationships:
        return None

    nodes: List[Dict[str, Any]] = []
    node_ids: set[str] = set()
    for rec in entities:
        if len(nodes) >= _GRAPH_MAX_NODES:
            break
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title") or rec.get("name") or rec.get("entity") or "").strip()
        if not title or title in node_ids:
            continue
        node_ids.add(title)
        metadata = (entity_metadata or {}).get(title.casefold()) or {}
        nodes.append(
            {
                "id": title,
                "label": title,
                "type": str(rec.get("type") or metadata.get("type") or ""),
                "description": str(
                    rec.get("description") or metadata.get("description") or ""
                ).strip()[:_GRAPH_MAX_ENTITY_DESC_CHARS],
                "degree": rec.get("degree") or metadata.get("degree") or 0,
            }
        )

    edges: List[Dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for rec in relationships:
        if len(edges) >= _GRAPH_MAX_EDGES:
            break
        if not isinstance(rec, dict):
            continue
        source = str(rec.get("source") or "").strip()
        target = str(rec.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        key = (source, target) if source <= target else (target, source)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        source_metadata = (entity_metadata or {}).get(source.casefold()) or {}
        target_metadata = (entity_metadata or {}).get(target.casefold()) or {}
        # local search truncates entities and relationships independently, so
        # an edge endpoint may be missing from the (clipped) node list — add a
        # enriched node so the edge stays connected instead of dropping it.
        for endpoint, metadata in (
            (source, source_metadata),
            (target, target_metadata),
        ):
            if endpoint not in node_ids and len(nodes) < _GRAPH_MAX_NODES:
                node_ids.add(endpoint)
                nodes.append(
                    {
                        "id": endpoint,
                        "label": endpoint,
                        "type": str(metadata.get("type") or ""),
                        "description": str(metadata.get("description") or "").strip()[
                            :_GRAPH_MAX_ENTITY_DESC_CHARS
                        ],
                        "degree": metadata.get("degree") or 0,
                    }
                )
        edges.append(
            {
                "source": source,
                "target": target,
                "description": str(rec.get("description") or "").strip()[
                    :_GRAPH_MAX_EDGE_DESC_CHARS
                ],
                "weight": rec.get("weight") if rec.get("weight") is not None else 1,
            }
        )

    if not nodes or not edges:
        return None
    return {"nodes": nodes, "edges": edges}


__all__ = ["GraphRagPipeline"]
