# -*- coding: utf-8 -*-
"""M0-9（评审 B1）：OpenSPG server 独立开源发行版验证。

验证目标（docs/kag-integration-design.md §10 第 9 项 + §12 红线）：
确认可部署开源发行版的获取途径与 License；**部署物不得取自闭源 openspgapp
仓库的构建产物**。

做法（全部标准库，网络探测失败不影响骨架运行，仅记录供人工复核）：
  1. GitHub API：OpenSPG/openspg 仓库存在性、License、最新 release；
  2. Docker Hub：候选镜像名探测（openspg/openspg-server 等），记录存在的镜像与 tag 样本；
  3. 版本对照：读本地 openspgapp/openspg 的 pom 版本，与上游 release 对照（差异记录，人工决策）；
  4. 部署物声明核验（--deployed-from）：声明的来源若指向本地闭源仓库 → FAIL（红线）。

用法：
  python m0_9_openspg_distribution_check.py [--openspgapp-dir ~/project/openspgapp] [--deployed-from "<镜像名或路径>"]
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

GITHUB_REPO_API = "https://api.github.com/repos/OpenSPG/openspg"
GITHUB_RELEASE_API = "https://api.github.com/repos/OpenSPG/openspg/releases/latest"
DOCKERHUB_CANDIDATES = ["openspg/openspg-server", "openspg/openspg"]


def http_get_json(url: str, timeout: float = 10):
    """GET 并解析 JSON；失败返回 (None, 错误说明)。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kagweb-m0-probe"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as exc:
        return None, repr(exc)


def probe_github(result: dict) -> None:
    repo, err = http_get_json(GITHUB_REPO_API)
    if repo is None:
        result["github_repo"] = {"ok": False, "error": err, "note": "匿名限流或网络问题，请人工复核 github.com/OpenSPG/openspg"}
        print_step(False, "GitHub 仓库探测", err)
        return
    license_info = (repo.get("license") or {}).get("spdx_id") or "unknown"
    result["github_repo"] = {
        "ok": True,
        "html_url": repo.get("html_url"),
        "license": license_info,
        "default_branch": repo.get("default_branch"),
        "pushed_at": repo.get("pushed_at"),
    }
    print_step(True, "GitHub 仓库探测", f"{repo.get('html_url')} license={license_info}")

    release, err = http_get_json(GITHUB_RELEASE_API)
    if release is None:
        result["github_release"] = {"ok": False, "error": err, "note": "可能无 release 或限流，请人工复核 Releases 页"}
        print_step(False, "GitHub release 探测", err)
    else:
        result["github_release"] = {"ok": True, "tag": release.get("tag_name"), "published_at": release.get("published_at")}
        print_step(True, "GitHub release 探测", f"latest={release.get('tag_name')}")


def probe_dockerhub(result: dict) -> None:
    found = []
    for name in DOCKERHUB_CANDIDATES:
        data, _ = http_get_json(f"https://hub.docker.com/v2/repositories/{name}/")
        if data and data.get("count", 0) > 0:
            tags, _ = http_get_json(f"https://hub.docker.com/v2/repositories/{name}/tags/?page_size=5")
            tag_names = [t.get("name") for t in (tags or {}).get("results", [])]
            found.append({"image": name, "pull_count": data.get("pull_count"), "tag_samples": tag_names})
    result["dockerhub"] = found
    print_step(bool(found), "Docker Hub 镜像探测", f"{[f['image'] for f in found] or '候选镜像均未命中，人工确认镜像名'}")


def read_local_pom_version(openspgapp_dir: Path) -> str:
    """读本地 openspg 模块的版本声明（<revision> 或 <version>），供与上游对照。"""
    for pom in [openspgapp_dir / "openspg" / "pom.xml", openspgapp_dir / "pom.xml"]:
        if pom.is_file():
            text = pom.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<revision>([^<]+)</revision>", text) or re.search(
                r"<version>([^<]+)</version>", text
            )
            if m:
                return m.group(1)
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="M0-9 OpenSPG 开源发行版验证")
    ap.add_argument("--openspgapp-dir", default=str(Path.home() / "project" / "openspgapp"), help="本地闭源仓库（仅读版本号对照）")
    ap.add_argument("--deployed-from", default="", help="实际部署物来源（镜像名或路径），用于红线核验")
    args = ap.parse_args()

    result = {}
    probe_github(result)
    probe_dockerhub(result)

    openspgapp_dir = Path(args.openspgapp_dir).expanduser().resolve()
    result["local_openspgapp_version"] = read_local_pom_version(openspgapp_dir)
    print_step(True, "本地版本对照", f"openspgapp/openspg pom 版本 = {result['local_openspgapp_version']}（与上游 release 差异由人工决策）")

    # §12 红线：部署物不得来自闭源仓库
    verdict = "PASS"
    if args.deployed_from:
        if "openspgapp" in str(Path(args.deployed_from)) or "openspgapp" in args.deployed_from:
            verdict = "FAIL_RED_LINE"
        result["deployed_from"] = args.deployed_from
    else:
        result["deployed_from"] = "（未声明——部署时必须显式声明来源）"
    result["verdict"] = verdict
    print_step(verdict == "PASS", "部署物来源红线核验", f"{result['deployed_from']} -> {verdict}")

    write_result("m0_9_openspg_distribution_check", result)


if __name__ == "__main__":
    main()
