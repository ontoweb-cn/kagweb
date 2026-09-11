"""Cross-platform lifecycle tests for short-lived heavy workers."""

from __future__ import annotations

import asyncio
import gc
from pathlib import Path

import pytest

from kagweb.runtime.isolated_worker import (
    IsolatedWorkerError,
    IsolatedWorkerTimeout,
    run_in_isolated_process,
    run_in_isolated_process_sync,
)
from kagweb.utils.document_extractor import (
    EmptyDocumentError,
    extract_text_from_path_isolated,
)


def test_sync_worker_returns_a_pickled_result() -> None:
    assert run_in_isolated_process_sync("operator:add", 20, 22, timeout=5) == 42


@pytest.mark.asyncio
async def test_async_worker_preserves_remote_error_details() -> None:
    with pytest.raises(IsolatedWorkerError) as raised:
        await run_in_isolated_process("builtins:int", "not-an-int", timeout=5)
    assert raised.value.remote_module == "builtins"
    assert raised.value.remote_type == "ValueError"
    assert "not-an-int" in str(raised.value)


@pytest.mark.asyncio
async def test_worker_timeout_terminates_the_child() -> None:
    with pytest.raises(IsolatedWorkerTimeout):
        await run_in_isolated_process("time:sleep", 10, timeout=0.05)


@pytest.mark.asyncio
async def test_worker_cancellation_terminates_the_child() -> None:
    task = asyncio.create_task(run_in_isolated_process("time:sleep", 10, timeout=20))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_document_wrapper_restores_public_error_type(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(EmptyDocumentError) as raised:
        await extract_text_from_path_isolated(empty, timeout=5)
    assert raised.value.filename == "empty.txt"


def test_child_allocation_does_not_raise_parent_memory_plateau() -> None:
    psutil = pytest.importorskip("psutil")
    process = psutil.Process()

    try:
        process.memory_full_info().uss
        uss_supported = True
    except AttributeError:  # platform without USS support
        uss_supported = False

    def private_bytes() -> int:
        # USS (unique set size) ignores shared pages, so allocator noise from
        # unrelated tests moves the number far less than RSS does.
        if uss_supported:
            return process.memory_full_info().uss
        return process.memory_info().rss

    # One warmup round: the first spawn pays fork/COW page-table and pool
    # setup costs in the parent, which are startup overhead — not retained
    # payload — and would otherwise flake the baseline.
    assert (
        run_in_isolated_process_sync(
            "kagweb.runtime.worker_tasks:test_allocate_bytes",
            64 * 1024 * 1024,
            timeout=10,
        )
        == 64 * 1024 * 1024
    )
    gc.collect()
    before = private_bytes()
    for _ in range(3):
        assert (
            run_in_isolated_process_sync(
                "kagweb.runtime.worker_tasks:test_allocate_bytes",
                64 * 1024 * 1024,
                timeout=10,
            )
            == 64 * 1024 * 1024
        )
    gc.collect()
    # Allocations happen only in children. Allow normal allocator/test noise in
    # the parent, but reject retaining anything close to one 64 MB payload
    # (RSS fallback gets the wider band because it counts shared pages too).
    threshold = 20 * 1024 * 1024 if uss_supported else 48 * 1024 * 1024
    assert private_bytes() - before < threshold


def test_text_only_parser_writes_result_from_worker(tmp_path: Path) -> None:
    from kagweb.services.parsing.engines.text_only.engine import TextOnlyParser

    source = tmp_path / "source.txt"
    source.write_text("isolated parser output", encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    TextOnlyParser().parse(source, workdir, config={})
    assert (workdir / "source.md").read_text(encoding="utf-8") == "isolated parser output"
