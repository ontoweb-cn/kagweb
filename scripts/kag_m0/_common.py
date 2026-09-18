# -*- coding: utf-8 -*-
"""M0 验证脚本共享工具。

设计文档：docs/kag-integration-design.md §10（M0 验证清单）。
所有脚本把结果写出到 scripts/kag_m0/results/<name>.json，便于归档评审。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def env_snapshot() -> dict:
    """采集环境快照：KAG 版本基线（M0-6）、关键环境变量。"""
    snapshot = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "argv": sys.argv[1:],
    }
    try:
        from kag import __version__ as kag_version  # noqa: F401
    except Exception:
        try:
            # KAG_VERSION 文件不在包内，退化为 setuptools 元数据
            from importlib.metadata import version as _v

            kag_version = _v("openspg-kag")
        except Exception:
            kag_version = "unknown"
    snapshot["kag_version"] = kag_version
    for var in (
        "KAG_PROJECT_HOST_ADDR",
        "KAG_PROJECT_ID",
        "KAG_PROJECT_NAMESPACE",
        "KAG_GRAPH_STORE_URI",
    ):
        snapshot[f"env:{var}"] = os.environ.get(var, "")
    return snapshot


def write_result(name: str, payload: dict) -> Path:
    """写出 JSON 结果并打印路径。payload 会附上环境快照。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload["__env__"] = env_snapshot()
    out = RESULTS_DIR / f"{name}.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n[结果已写出] {out}")
    return out


def print_step(ok: bool, title: str, detail: str = "") -> None:
    """统一的步骤打印。"""
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {title}"
    if detail:
        line += f" — {detail[:300]}"
    print(line)


def elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)
