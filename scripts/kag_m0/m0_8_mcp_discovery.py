# -*- coding: utf-8 -*-
"""M0-8：Claude Code workdir .mcp.json 发现机制实测。

验证目标（docs/kag-integration-design.md §10 第 8 项）：
  1. Claude Code 能否从会话工作目录自动发现 .mcp.json（KAGWeb CLI 后端
     session_workspace:true 的接线前提，设计 §5.3）；
  2. env 注入路径：.mcp.json 的 mcpServers.<name>.env 是否传给 server 子进程
     （对应 KAGWeb env allowlist 下 Bridge 凭据的传递形态）。

做法：生成一个隔离的测试 workdir（含 .mcp.json + 最小 stdio MCP stub），
stub 声明 kag_solve 工具并把收到的 env/调用记录到 probe_log.jsonl，
然后打印人工验证命令（本脚本不调 claude，避免环境差异导致误判）。

附注：此 stub 即 M1 KAG Bridge MCP server 的种子代码。

用法：
  python m0_8_mcp_discovery.py [--workdir /tmp/m0_mcp_workdir]
  然后按打印出的命令在终端执行验证。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

MCP_CONFIG = {
    "mcpServers": {
        "kag-bridge-stub": {
            "command": "python3",
            "args": ["stub_server.py"],
            "env": {
                # 模拟 Bridge 凭据注入（KAGWeb 侧经 profile env 块下发，
                # 见设计 §6.5 的 scoped token 传递路径）
                "KAG_BRIDGE_URL": "http://127.0.0.1:8930",
                "KAG_BRIDGE_TOKEN": "m0-probe-token",
            },
        }
    }
}

STUB_SERVER = r'''# -*- coding: utf-8 -*-
"""最小 stdio MCP stub：验证 .mcp.json 发现 + env 注入。

协议：MCP stdio transport，每行一个 JSON-RPC 2.0 消息。
处理 initialize / notifications/initialized / tools/list / tools/call，
把观察到的环境变量与调用记录写入脚本同目录的 probe_log.jsonl（M0-8 的判定证据）。
"""
import json
import os
import sys
import time

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_log.jsonl")
LOG = open(LOG_PATH, "a", encoding="utf-8")


def log(event):
    LOG.write(json.dumps({"ts": time.time(), **event}, ensure_ascii=False) + "\n")
    LOG.flush()


def reply(msg_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}), flush=True)


TOOLS = [
    {
        "name": "kag_solve",
        "description": "KAG 推理工具（M0 stub：返回固定答案，验证调用链）",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    }
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, msg_id = msg.get("method"), msg.get("id")
    if method == "initialize":
        log({"event": "initialize", "params": msg.get("params", {}),
             "env": {k: v for k, v in os.environ.items() if k.startswith("KAG_BRIDGE")}})
        reply(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kag-bridge-stub", "version": "0.0.1"},
        })
    elif method == "notifications/initialized":
        log({"event": "initialized-notification"})
    elif method == "tools/list":
        log({"event": "tools/list"})
        reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        args = msg.get("params", {}).get("arguments", {})
        log({"event": "tools/call", "tool": msg.get("params", {}).get("name"), "arguments": args,
             "env": {k: v for k, v in os.environ.items() if k.startswith("KAG_BRIDGE")}})
        reply(msg_id, {"content": [{"type": "text", "text":
            f"[m0-stub] 已收到 kag_solve 调用：{args.get('question', '')}"}], "isError": False})
'''

VERIFY_NOTES = """\
实测步骤（M0-8 判定）：
  1. cd {workdir}
  2. claude mcp list
     → 期望出现 kag-bridge-stub（发现机制 OK）
  3. claude -p "调用 kag_solve 工具，问题：测试" --allowedTools "mcp__kag-bridge-stub__kag_solve"
     （参数名随 claude CLI 版本可能不同，以 claude --help 为准）
  4. cat probe_log.jsonl
     → 期望看到 initialize 的 env 字段含 KAG_BRIDGE_TOKEN=m0-probe-token
     （env 注入路径 OK，即 KAGWeb scoped token 传递形态可行）
判定：2+4 同时通过 → KAGWeb 对话链路接线（设计 §5.3）前提成立。
"""


def main():
    ap = argparse.ArgumentParser(description="M0-8 Claude Code .mcp.json 发现机制实测准备")
    ap.add_argument("--workdir", default="/tmp/m0_mcp_workdir", help="测试工作目录")
    args = ap.parse_args()

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / ".mcp.json").write_text(json.dumps(MCP_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    (workdir / "stub_server.py").write_text(STUB_SERVER, encoding="utf-8")

    print_step(True, "测试 workdir 已生成", str(workdir))
    print(f"\n{VERIFY_NOTES.format(workdir=workdir)}")

    write_result(
        "m0_8_mcp_discovery",
        {
            "workdir": str(workdir),
            "generated": [".mcp.json", "stub_server.py"],
            "manual_steps": VERIFY_NOTES.format(workdir=str(workdir)).strip().splitlines(),
            "note": "本脚本只做环境准备；判定命令需人工执行（避免本机 claude CLI 版本差异造成误判）。",
        },
    )


if __name__ == "__main__":
    main()
