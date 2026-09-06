"""Unified MinerU parsing entrypoint.

Hides the local-CLI vs cloud-API split behind one function so callers never
branch on backend. Both branches converge on the
same contract: write MinerU artifacts into a working directory and return its
path. Backend selection comes from ``document_parsing.json`` via
:func:`resolve_mineru_config`.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .config import (
    MinerUConfig,
    MinerUError,
    resolve_mineru_config,
)
from .formats import MINERU_PDF_FORMATS, MINERU_SUPPORTED_FORMATS

logger = logging.getLogger(__name__)

# PATH-lookup order matches ``check_mineru_installed`` in local.py so the
# probe reports the same command the parse subprocess will actually use.
_LOCAL_CLI_COMMANDS = ("mineru", "magic-pdf")
# MinerU's hosted Precision API rejects a PDF above this page count. Keep the
# limit here rather than in a UI so every ParseService consumer behaves alike.
MAX_CLOUD_PDF_PAGES = 200


def parse_document_to_workdir(
    source_path: str | Path,
    output_base: str | Path,
    *,
    config: MinerUConfig | None = None,
    on_output: Callable[[str], None] | None = None,
) -> Path:
    """Parse a MinerU-supported document or image into ``output_base``.

    The current MinerU input set is intentionally validated here as well as in
    :class:`ParseService`, because this module is also used directly by a few
    adapters and tests.
    """
    cfg = config or resolve_mineru_config()
    source_path = Path(source_path)
    suffix = source_path.suffix.lower()
    if suffix not in MINERU_SUPPORTED_FORMATS:
        raise MinerUError(
            f"MinerU does not support {suffix or 'files without an extension'}. "
            "Supported inputs are PDF, common raster images, DOCX, PPTX, and XLSX."
        )
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    if cfg.is_cloud:
        from .cloud import parse_cloud

        logger.info("Parsing %s via MinerU cloud API", source_path.name)
        if suffix in MINERU_PDF_FORMATS and _pdf_page_count(source_path) > MAX_CLOUD_PDF_PAGES:
            return _parse_cloud_pdf_in_chunks(source_path, output_base, cfg, on_output=on_output)
        return parse_cloud(source_path, output_base, cfg, on_progress=on_output)

    return _parse_local(source_path, output_base, config=cfg, on_output=on_output)


def _pdf_page_count(source_path: Path) -> int:
    """Return the physical page count without parsing document text."""
    try:
        import pymupdf

        with pymupdf.open(source_path) as document:
            return len(document)
    except Exception as exc:
        raise MinerUError(f"Could not inspect PDF page count for {source_path.name}: {exc}") from exc


def _parse_cloud_pdf_in_chunks(
    source_path: Path,
    output_base: Path,
    config: MinerUConfig,
    *,
    on_output: Callable[[str], None] | None = None,
) -> Path:
    """Send a long PDF to MinerU cloud in page-limit-safe chunks and merge it.

    Cloud chunks are temporary implementation details.  The final output is a
    normal MinerU-shaped workdir, so ParseService, RAG, and Immersive Reading
    all keep their existing contracts.  ``page_idx`` values are offset while
    merging, preserving physical PDF page locators for consumers that use the
    structured content-list output.
    """
    page_count = _pdf_page_count(source_path)
    chunk_count = (page_count + MAX_CLOUD_PDF_PAGES - 1) // MAX_CLOUD_PDF_PAGES
    _report(
        on_output,
        f"MinerU cloud: splitting {page_count} pages into {chunk_count} parts "
        f"(up to {MAX_CLOUD_PDF_PAGES} pages each)",
    )

    with tempfile.TemporaryDirectory(prefix=".mineru-parts-", dir=output_base) as raw_temp:
        temp_root = Path(raw_temp)
        chunks = _write_pdf_chunks(source_path, temp_root / "input")
        parsed_chunks: list[tuple[int, Path]] = []

        from .cloud import parse_cloud

        for index, (first_page, chunk_path) in enumerate(chunks, start=1):
            _report(on_output, f"MinerU cloud: parsing part {index}/{len(chunks)}")
            part_base = temp_root / "output" / f"part-{index:03d}"
            parsed_chunks.append(
                (
                    first_page,
                    parse_cloud(chunk_path, part_base, config, on_progress=on_output),
                )
            )

        _merge_cloud_pdf_chunks(parsed_chunks, output_base, source_path.stem)

    logger.info(
        "MinerU cloud parsed %s pages from %s in %s chunks",
        page_count,
        source_path.name,
        chunk_count,
    )
    return output_base


def _write_pdf_chunks(source_path: Path, destination: Path) -> list[tuple[int, Path]]:
    """Create sequential PDF fragments, returning ``(zero_based_page, path)``."""
    try:
        import pymupdf

        destination.mkdir(parents=True, exist_ok=True)
        chunks: list[tuple[int, Path]] = []
        with pymupdf.open(source_path) as document:
            for first_page in range(0, len(document), MAX_CLOUD_PDF_PAGES):
                last_page = min(first_page + MAX_CLOUD_PDF_PAGES, len(document)) - 1
                chunk_path = destination / (
                    f"{source_path.stem}.part-{len(chunks) + 1:03d}{source_path.suffix}"
                )
                fragment = pymupdf.open()
                try:
                    fragment.insert_pdf(document, from_page=first_page, to_page=last_page)
                    fragment.save(chunk_path)
                finally:
                    fragment.close()
                chunks.append((first_page, chunk_path))
        return chunks
    except MinerUError:
        raise
    except Exception as exc:
        raise MinerUError(f"Could not split {source_path.name} for MinerU cloud: {exc}") from exc


def _merge_cloud_pdf_chunks(
    parsed_chunks: list[tuple[int, Path]], output_base: Path, source_stem: str
) -> None:
    """Write a single cache-compatible MinerU result from parsed PDF chunks."""
    from kagweb.services.parsing import cache

    markdown_parts: list[str] = []
    merged_blocks: list[dict[str, Any]] = []
    images_root = output_base / "images"

    for index, (first_page, chunk_dir) in enumerate(parsed_chunks, start=1):
        markdown, blocks, asset_dir = cache.load_ir(chunk_dir)
        if markdown.strip():
            markdown_parts.append(_rewrite_markdown_image_paths(markdown, index))
        if asset_dir is not None:
            shutil.copytree(asset_dir, images_root / f"part-{index:03d}", dirs_exist_ok=True)
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            merged_blocks.append(_merge_block(block, first_page, index, asset_dir))

    if not markdown_parts and not merged_blocks:
        raise MinerUError("MinerU cloud returned no readable content for any PDF part.")
    if markdown_parts:
        (output_base / f"{source_stem}.md").write_text(
            "\n\n".join(markdown_parts), encoding="utf-8"
        )
    if merged_blocks:
        (output_base / f"{source_stem}_content_list.json").write_text(
            json.dumps(merged_blocks, ensure_ascii=False), encoding="utf-8"
        )


def _merge_block(
    block: dict[str, Any], first_page: int, chunk_index: int, asset_dir: Path | None
) -> dict[str, Any]:
    """Copy one parser block and translate page/image references to the merge."""
    merged = dict(block)
    page_index = merged.get("page_idx")
    if isinstance(page_index, int) and not isinstance(page_index, bool) and page_index >= 0:
        merged["page_idx"] = page_index + first_page

    image_path = merged.get("img_path")
    if isinstance(image_path, str) and image_path and asset_dir is not None:
        try:
            relative = Path(image_path).resolve().relative_to(asset_dir.resolve())
        except ValueError:
            pass
        else:
            merged["img_path"] = str(Path("images") / f"part-{chunk_index:03d}" / relative)
    return merged


def _rewrite_markdown_image_paths(markdown: str, chunk_index: int) -> str:
    """Keep common relative image links valid after each chunk gets a namespace."""
    prefix = f"images/part-{chunk_index:03d}/"
    return (
        markdown.replace("](images/", f"]({prefix}")
        .replace('src="images/', f'src="{prefix}')
        .replace("src='images/", f"src='{prefix}")
    )


def _report(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        logger.debug("MinerU progress callback failed", exc_info=True)


def parse_pdf_to_workdir(
    pdf_path: str | Path,
    output_base: str | Path,
    *,
    config: MinerUConfig | None = None,
    on_output: Callable[[str], None] | None = None,
) -> Path:
    """Parse ``pdf_path`` and return the directory holding MinerU artifacts.

    The returned directory contains the parsed markdown +
    ``*_content_list.json`` (+ ``images/``) in whichever layout the active
    backend produces; :func:`load_parsed_paper` locates the content
    sub-directory regardless. ``on_output`` (if given) receives short progress
    lines from whichever backend runs — raw CLI output locally, task-state
    summaries from the cloud poller. Raises :class:`MinerUError` on failure.
    """
    pdf_path = Path(pdf_path)
    if pdf_path.suffix.lower() not in MINERU_PDF_FORMATS:
        raise MinerUError(f"parse_pdf_to_workdir expects a PDF file: {pdf_path}")
    return parse_document_to_workdir(
        pdf_path,
        output_base,
        config=config,
        on_output=on_output,
    )


def local_cli_probe(configured_path: str = "") -> dict[str, Any]:
    """Fast (no-subprocess) check for a local MinerU CLI.

    ``configured_path`` (the ``local_cli_path`` setting) takes precedence over
    PATH lookup so MinerU can live in an isolated env (uv tool / pipx /
    separate conda) without PATH games. Returns ``{found, command, path,
    source}`` where ``source`` is ``"configured"`` or ``"path"``. Cheap enough
    to run on every settings GET; the slower ``--version`` confirmation lives
    in :func:`local_cli_version` and only runs behind the explicit Test button.
    """
    configured = (configured_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        found = candidate.is_file() and os.access(candidate, os.X_OK)
        return {
            "found": found,
            "command": candidate.name,
            "path": str(candidate),
            "source": "configured",
        }
    for command in _LOCAL_CLI_COMMANDS:
        path = shutil.which(command)
        if path:
            return {"found": True, "command": command, "path": path, "source": "path"}
    return {"found": False, "command": "", "path": "", "source": "path"}


def local_cli_version(command: str, timeout: float = 60.0) -> str:
    """Run ``<command> --version`` and return the first output line ("" on any
    failure). ``command`` must be a whitelisted name or an existing executable
    path (the validated ``local_cli_path``) — anything else is refused. Heavy
    CLIs import slowly on first run, hence kept out of the settings GET path."""
    if command not in _LOCAL_CLI_COMMANDS:
        candidate = Path(command).expanduser()
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            return ""
        command = str(candidate)
    try:
        result = subprocess.run(  # nosec B603 — whitelisted name or validated executable
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0][:120] if output else ""


def _parse_local(
    source_path: Path,
    output_base: Path,
    *,
    config: MinerUConfig,
    on_output: Callable[[str], None] | None = None,
) -> Path:
    """Local-CLI branch: delegate to the existing subprocess parser and return
    the deterministic output directory it writes to (``<base>/<stem>``)."""
    from .local import parse_document_with_mineru
    from .models import model_env_overrides, render_env_overrides

    cli_command = None
    if (config.local_cli_path or "").strip():
        probe = local_cli_probe(config.local_cli_path)
        if not probe["found"]:
            raise MinerUError(
                f"Configured MinerU CLI path is not an executable file: {probe['path']}. "
                "Fix it in Settings → MinerU (or clear it to auto-detect from PATH)."
            )
        cli_command = probe["path"]
    else:
        probe = local_cli_probe()
        if probe["found"]:
            cli_command = probe["path"]

    if (
        cli_command
        and Path(cli_command).name == "magic-pdf"
        and source_path.suffix.lower() != ".pdf"
    ):
        raise MinerUError(
            "The legacy magic-pdf CLI only accepts PDF files. Install the current "
            "MinerU CLI (`pip install -U 'mineru[all]>=3.4.5'`) to parse images, "
            "DOCX, PPTX, or XLSX."
        )

    # A lazy first-parse model download must honor the configured source and
    # custom address, not just the explicit Download button.
    download_env = model_env_overrides(config.model_download_source, config.model_download_endpoint)
    # Only the local CLI renders pages in this process tree; cloud mode never
    # does, so the Windows render-thread guard belongs on this branch alone.
    subprocess_env = {**download_env, **render_env_overrides()}

    logger.info("Parsing %s via local MinerU CLI (%s)", source_path.name, cli_command or "PATH")
    ok = parse_document_with_mineru(
        str(source_path),
        str(output_base),
        on_output=on_output,
        cli_command=cli_command,
        extra_env=subprocess_env,
    )
    if not ok:
        raise MinerUError(
            "Local MinerU parsing failed. Ensure MinerU is installed "
            "(`pip install -U 'mineru[all]>=3.4.5'`) or switch to cloud mode in "
            "Settings → MinerU."
        )
    working_dir = output_base / source_path.stem
    if not working_dir.is_dir():
        # Defensive: the CLI names its output dir after the source stem, but fall
        # back to the newest sub-directory if that assumption ever breaks.
        subdirs = sorted(
            (d for d in output_base.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not subdirs:
            raise MinerUError("MinerU produced no output directory.")
        working_dir = subdirs[0]
    return working_dir


__all__ = [
    "MinerUError",
    "local_cli_probe",
    "local_cli_version",
    "parse_document_to_workdir",
    "parse_pdf_to_workdir",
]
