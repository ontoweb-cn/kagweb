# -*- coding: utf-8 -*-
"""M0-7：KAG 多轮/记忆机制静态扫描。

验证目标（docs/kag-integration-design.md §10 第 7 项）：
确认 KAG 是否存在 memorizer/会话/多轮机制，决定 kag_solve"无状态单问单答"
语义的最终描述（设计 §5.1 R2：多轮上下文由 agent loop 拼装，若有原生
memorizer 则作为 M3 增强）。

做法：纯静态扫描 kag/ 与 knext/ 源码的关键词命中（file:line:code），
输出按文件分组，附人工确认指引——静态命中≠可用的多轮机制，结论需人工复核。

用法：
  python m0_7_memorizer_scan.py --kag-dir ~/project/KAG
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

# 关键词 → 命中权重（高=强信号，低=需人工甄别）
PATTERNS = [
    (r"memorizer|Memorizer", "high"),
    (r"multi[_\s-]?turn", "high"),
    (r"chat[_\s]?history|conversation[_\s]?history", "high"),
    (r"\bmemory\b|MemoryMgr|memory_manager", "low"),
    (r"\bsession\b", "low"),
]

# 已知噪声（与多轮对话无关的 session/memory 用法）
NOISE_SUBSTRINGS = (
    "SchemaSession",      # knext/schema 的 schema 会话
    "requests.Session",   # HTTP 连接复用
    "sqlalchemy",         # 数据库会话
)


def scan(kag_dir: Path) -> dict:
    hits = defaultdict(list)
    total = 0
    for py in sorted(list((kag_dir / "kag").rglob("*.py")) + list((kag_dir / "knext").rglob("*.py"))):
        rel = str(py.relative_to(kag_dir))
        try:
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, 1):
            if any(n in line for n in NOISE_SUBSTRINGS):
                continue
            for pat, weight in PATTERNS:
                if re.search(pat, line):
                    hits[rel].append({"line": idx, "weight": weight, "code": line.strip()[:160]})
                    total += 1
                    break
    return {"files": dict(hits), "total_hits": total}


def main():
    ap = argparse.ArgumentParser(description="M0-7 KAG 多轮/记忆机制扫描")
    ap.add_argument("--kag-dir", default=str(Path.home() / "project" / "KAG"), help="KAG 仓库路径")
    args = ap.parse_args()

    kag_dir = Path(args.kag_dir).expanduser().resolve()
    if not (kag_dir / "kag").is_dir():
        raise SystemExit(f"未找到 KAG 源码：{kag_dir}/kag（用 --kag-dir 指定）")

    scan_result = scan(kag_dir)
    high_files = {
        f: [h for h in v if h["weight"] == "high"] for f, v in scan_result["files"].items()
    }
    high_files = {f: v for f, v in high_files.items() if v}

    print_step(True, "扫描完成", f"命中 {scan_result['total_hits']} 行，涉及 {len(scan_result['files'])} 个文件")
    print("\n=== 强信号命中（memorizer / multi-turn / chat history，需人工确认可用性）===")
    for f, v in high_files.items():
        print(f"\n{kag_dir / f}")
        for h in v:
            print(f"  L{h['line']}: {h['code']}")
    if not high_files:
        print("（无强信号命中 → 支持 kag_solve 无状态语义的假设，设计 §5.1 R2 维持）")

    write_result(
        "m0_7_memorizer_scan",
        {
            "kag_dir": str(kag_dir),
            "scan": scan_result,
            "manual_review_note": (
                "静态命中≠多轮机制可用。人工确认要点：是否可在 ainvoke 时注入历史/记忆；"
                "若可用，Bridge 的 kag_solve 增加 session/memorizer 参数作为 M3 增强。"
            ),
        },
    )


if __name__ == "__main__":
    main()
