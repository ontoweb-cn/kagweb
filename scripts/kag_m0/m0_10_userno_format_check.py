# -*- coding: utf-8 -*-
"""M0-10（评审 B2）：userNo 账号格式实测。

验证目标（docs/kag-integration-design.md §10 第 10 项）：
确定 §6.3 T1 `service_user_no` 与 T2 `kagweb-<uid>` 的最终格式。
背景证据：server 端 `ProjectController.check()` 校验"账号格式"；
`BuilderController` 的兜底 userNo 为纯数字（"164072"）——若 server 要求
纯数字账号，`kagweb-<uid>` 会被拒。

做法：
  1. 静态扫描（默认，无副作用）：在 openspgapp server 源码中定位 userNo
     格式校验逻辑（正则/matches/isValid 等），提取校验规则；
  2. 实测（--host 且 --allow-write）：对 POST /public/v1/project 逐一测试
     候选格式，按响应判定通过/格式拒绝；namespace 用 m0_probe 前缀，
     创建成功的 project id 记录在结果中便于事后清理。

用法：
  python m0_10_userno_format_check.py [--openspgapp-dir ~/project/openspgapp]
  python m0_10_userno_format_check.py --host http://127.0.0.1:8887 --allow-write
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

# 候选格式：数字（BuilderController 兜底形态）、T1/T2 设想的字符串形态
CANDIDATE_FORMATS = ["164072", "kagweb", "kagweb-1", "kagweb_1", "kagweb.1"]

# 静态扫描的校验信号：userNo 附近的格式校验关键词
VALIDATION_SIGNALS = re.compile(
    r"(?i)(check|valid|match|pattern|regex|format|account|userNo)"
)


def scan_userno_validation(openspgapp_dir: Path) -> dict:
    """定位 server 侧 userNo 格式校验的实现证据。"""
    server_dir = openspgapp_dir / "openspg" / "server"
    if not server_dir.is_dir():
        server_dir = openspgapp_dir  # 退化为全仓库扫描
    hits = []
    for path in server_dir.rglob("*.java"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines):
            if "userNo" in line or "UserNo" in line:
                if VALIDATION_SIGNALS.search(line):
                    rel = str(path.relative_to(openspgapp_dir))
                    hits.append({"file": f"{rel}:{idx + 1}", "code": line.strip()[:160]})
    return {"hit_count": len(hits), "hits": hits[:30]}


def live_probe(host: str) -> list:
    """对项目创建端点逐一测试 userNo 候选格式（有副作用，需 --allow-write）。"""
    results = []
    url = host.rstrip("/") + "/public/v1/project"
    for fmt in CANDIDATE_FORMATS:
        body = {
            "name": f"m0-probe-userno-{fmt.replace('.', 'dot')}",
            "namespace": f"m0_probe_userno_{re.sub(r'[^0-9a-zA-Z]', '_', fmt)}",
            # tag=LOCAL 时 config 必含 "vectorizer" 键（ProjectController L81-84，仅 containsKey 校验）
            "config": {"vectorizer": {"name": "m0-probe-vectorizer"}},
            "visibility": "PRIVATE",
            "tag": "LOCAL",
            "userNo": fmt,
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, payload = resp.status, resp.read(400).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read(400).decode("utf-8", "replace")
        except Exception as exc:
            results.append({"format": fmt, "ok": False, "error": repr(exc)})
            continue
        accepted = 200 <= status < 300
        results.append(
            {
                "format": fmt,
                "ok": True,
                "status": status,
                "accepted": accepted,
                "verdict": "格式通过" if accepted else ("格式被拒" if status == 400 else "其他错误（人工判读）"),
                "body_head": payload,
            }
        )
    return results


def main():
    ap = argparse.ArgumentParser(description="M0-10 userNo 账号格式实测")
    ap.add_argument("--openspgapp-dir", default=str(Path.home() / "project" / "openspgapp"), help="本地仓库（静态扫描校验逻辑）")
    ap.add_argument("--host", default="", help="OpenSPG server 地址（启用实测）")
    ap.add_argument("--allow-write", action="store_true", help="实测会创建 m0_probe 前缀的测试项目，需显式开启")
    args = ap.parse_args()

    result = {"static": None, "live": None}

    openspgapp_dir = Path(args.openspgapp_dir).expanduser().resolve()
    if openspgapp_dir.is_dir():
        result["static"] = scan_userno_validation(openspgapp_dir)
        print_step(
            bool(result["static"]["hit_count"]),
            "userNo 校验逻辑静态扫描",
            f"命中 {result['static']['hit_count']} 处（人工判读校验规则）",
        )
    else:
        print_step(False, "静态扫描跳过", f"目录不存在：{openspgapp_dir}")

    if args.host:
        if not args.allow_write:
            print("\n[跳过] 实测未执行（--host 需配合 --allow-write：会创建 m0_probe 前缀测试项目）")
        else:
            result["live"] = live_probe(args.host)
            accepted = [r["format"] for r in result["live"] if r.get("accepted")]
            print_step(bool(accepted), "userNo 格式实测", f"通过：{accepted or '无'}；详见 results/m0_10_userno_format_check.json")
            result["conclusion_note"] = (
                "据实测通过的格式回填设计 §6.3 的 service_user_no / kagweb-<uid>；"
                "创建成功的 m0_probe 测试项目需人工清理（记录见 body_head）。"
            )
    else:
        print_step(True, "实测跳过", "未提供 --host（静态扫描结论先行，实测后补）")

    write_result("m0_10_userno_format_check", result)


if __name__ == "__main__":
    main()
