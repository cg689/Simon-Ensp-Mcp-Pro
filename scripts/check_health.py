# -*- coding: utf-8 -*-
"""
grbj-ensp-mcp (enhanced) 工具链 · 一键健康自检
================================================
用途：安装后跑一遍，确认「拓扑生成 + MCP 注册」这条链路完整可用。

用法（Windows / Git Bash）：
  python scripts/check_health.py                 # 默认：WorkBuddy 规范路径 + 仓库内 src
  python scripts/check_health.py --venv <路径>    # 指定自定义 venv
  python scripts/check_health.py --python <路径>  # 直接指定用于端到端测试的解释器

检查项：
  1. 目标解释器可用且 grbj_ensp_mcp 可导入
  2. 补丁生效（USG6000V 枚举式 + S3700 接口表 22E+2GE）
  3. 仓库 src 与运行环境中的 topo_builder.py 一致（逐字）
  4. MCP 注册状态（默认查 ~/.workbuddy/mcp.json，可 --mcp-json 覆盖）
  5. 配套技能在位（skill/ 目录）
  6. 端到端：现场渲染一个含 USG6000V 的最小拓扑
  7. eNSP 是否正在运行（仅提示，不影响判定）

退出码：0 = 全绿；1 = 有 FAIL
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.path.expanduser("~"))

DEFAULT_VENV_PY = HOME / ".workbuddy" / "binaries" / "python" / "envs" / "grbj-ensp-mcp" / "Scripts" / "python.exe"
DEFAULT_MCP_JSON = HOME / ".workbuddy" / "mcp.json"
DEFAULT_SKILLS_DIR = HOME / ".workbuddy" / "skills"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)


def main():
    ap = argparse.ArgumentParser(description="grbj-ensp-mcp 工具链健康自检")
    ap.add_argument("--venv", help="venv 目录（默认 ~/.workbuddy/.../grbj-ensp-mcp，不存在时回退当前解释器）")
    ap.add_argument("--python", help="直接指定用于测试的解释器（优先于 --venv）")
    ap.add_argument("--mcp-json", help=f"mcp.json 路径（默认 {DEFAULT_MCP_JSON}）")
    ap.add_argument("--skip-e2e", action="store_true", help="跳过端到端渲染测试")
    args = ap.parse_args()

    # ---- 解析目标解释器 -----------------------------------------------
    if args.python:
        target_py = Path(args.python)
        src_label = "指定解释器"
    else:
        venv_dir = Path(args.venv) if args.venv else DEFAULT_VENV_PY.parent.parent
        venv_py = venv_dir / "Scripts" / "python.exe"
        if venv_py.is_file():
            target_py = venv_py
            src_label = f"venv {venv_dir.name}"
        else:
            target_py = Path(sys.executable)
            src_label = "当前解释器（未找到专用 venv，已回退）"

    print("=" * 62)
    print(" grbj-ensp-mcp (enhanced) 工具链健康自检")
    print(f" 测试解释器：{target_py}  [{src_label}]")
    print("=" * 62)

    # 1 -------------------------------------------------- 解释器 + 导入
    pkg_ver = ""
    ok = False
    try:
        out = subprocess.run(
            [str(target_py), "-c", "import grbj_ensp_mcp as m; print(getattr(m,'__version__','?'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        ok = out.returncode == 0
        pkg_ver = (out.stdout or "").strip() or "import 失败"
    except Exception as e:
        pkg_ver = str(e)
    check(f"1. 解释器可用且 grbj_ensp_mcp 可导入 [{src_label}]", ok, f"包内 __version__ = {pkg_ver}（wheel 实为 0.2.2，作者未同步字符串，无碍）")

    # 2 ---------------------------------------------- 运行环境中的补丁
    usg_ok = s3700_ok = has_builder = False
    try:
        code = (
            "import grbj_ensp_mcp, inspect, pathlib\n"
            "p = pathlib.Path(inspect.getfile(grbj_ensp_mcp)).parent / 'topo_builder.py'\n"
            "print('BUILDER_PATH', p)\n"
            "s = p.read_text(encoding='utf-8', errors='replace')\n"
            "print('USG', ('USG6000V' in s) and ('<slot id=\"1\">' in s))\n"
            "print('S3700', ('(\"Ethernet\", 22)' in s) or (\"('Ethernet', 22)\" in s))\n"
        )
        out = subprocess.run([str(target_py), "-c", code], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        lines = dict(l.split(" ", 1) for l in (out.stdout or "").strip().splitlines() if " " in l)
        builder_path = Path(lines.get("BUILDER_PATH", "?"))
        usg_ok = lines.get("USG") == "True"
        s3700_ok = lines.get("S3700") == "True"
        has_builder = lines.get("BUILDER_PATH", "?") != "?"
        check("2a. 运行环境含 topo_builder.py", has_builder, str(builder_path))
        check("2b. 补丁 · USG6000V 枚举式支持", usg_ok)
        check("2c. 补丁 · S3700 接口表 (22E+2GE)", s3700_ok)
    except Exception as e:
        check("2a. 运行环境含 topo_builder.py", False, str(e))
        check("2b. 补丁 · USG6000V 枚举式支持", False)
        check("2c. 补丁 · S3700 接口表 (22E+2GE)", False)

    # 3 ------------------------------------ 仓库 src 与运行环境一致
    repo_builder = REPO_ROOT / "src" / "grbj_ensp_mcp" / "topo_builder.py"
    try:
        same = repo_builder.read_bytes() == builder_path.read_bytes()
        check("3. 仓库 src == 运行环境 topo_builder.py（逐字）", same,
              "一致" if same else "不一致！请用仓库 src 覆盖 site-packages 以恢复补丁")
    except Exception as e:
        check("3. 仓库 src == 运行环境 topo_builder.py（逐字）", False, str(e))

    # 4 ------------------------------------------------------ MCP 注册
    mcp_json = Path(args.mcp_json) if args.mcp_json else DEFAULT_MCP_JSON
    try:
        cfg = json.loads(mcp_json.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", {})
        hit = [k for k, v in servers.items() if "grbj_ensp_mcp.server" in " ".join(v.get("args", []))]
        disabled = servers[hit[0]].get("disabled", False) if hit else None
        check("4. MCP 已注册且未禁用", bool(hit) and not disabled,
              f"服务名 = {hit[0] if hit else '未找到'} | 文件 = {mcp_json}")
    except FileNotFoundError:
        check("4. MCP 已注册且未禁用", False, f"未找到 {mcp_json}（未安装 MCP 或非 WorkBuddy 环境）")
    except Exception as e:
        check("4. MCP 已注册且未禁用", False, str(e))

    # 5 ------------------------------------------------------------ 技能
    skills = ["grbj-ensp-smart-config", "ensp-topo-generate"]
    in_repo = [(s, REPO_ROOT / "skill" / s / "SKILL.md") for s in skills]
    missing_repo = [n for n, p in in_repo if not p.is_file()]
    installed = [s for s in skills if (DEFAULT_SKILLS_DIR / s / "SKILL.md").is_file()]
    check("5. 配套技能（仓库 skill/ 齐全 + 本机已安装）",
          not missing_repo and bool(installed),
          f"仓库 {len(skills)-len(missing_repo)}/{len(skills)}；本机已装 {len(installed)}/{len(skills)}")

    # 6 ------------------------------------------- 端到端最小拓扑渲染
    if args.skip_e2e:
        print("  [SKIP] 6. 端到端渲染（--skip-e2e）")
        results.append(("e2e", True))
    else:
        e2e_ok, e2e_detail = False, ""
        try:
            code = (
                "from grbj_ensp_mcp.topo_builder import NativeTopoBuilder\n"
                "b = NativeTopoBuilder()\n"
                "b.add_device('FW-test', model='USG6000V', com_port=2000)\n"
                "b.add_device('SW-test', model='S3700', com_port=2001)\n"
                "b.add_line('FW-test', 'SW-test', src_index=1, tar_index=22)\n"
                "s = b.render()\n"
                "assert '<slot id=\"1\">' in s, 'USG 枚举式未生效'\n"
                "assert 'count=\"22\"' in s, 'S3700 接口表未生效'\n"
                "print('RENDER_OK', len(s))\n"
            )
            out = subprocess.run([str(target_py), "-c", code], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
            e2e_ok = "RENDER_OK" in (out.stdout or "")
            e2e_detail = (out.stdout or out.stderr or "").strip().splitlines()[-1][:80] if (out.stdout or out.stderr) else ""
        except Exception as e:
            e2e_detail = str(e)
        check("6. 端到端渲染（含 USG6000V）", e2e_ok, e2e_detail)

    # 7 ------------------------------------------- eNSP 运行状态（提示项）
    ensp_running = False
    try:
        tl = subprocess.run(["tasklist"], capture_output=True, text=True,
                            encoding="gbk", errors="ignore", timeout=20).stdout.lower()
        ensp_running = "ensp" in tl
    except Exception:
        pass
    print(f"  [INFO] 7. eNSP 当前{'正在运行' if ensp_running else '未运行'}"
          f"{'（可直接开工配置下发）' if ensp_running else '（生成拓扑不受影响；配置下发前请先打开并启动设备）'}")

    # --------------------------------------------------------------- 汇总
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    print("-" * 62)
    print(f" 结果：{passed}/{total} 项通过")
    print(" 工具链健康，可以开工。" if passed == total else " 有检查未通过，见上方 FAIL 行。")
    print("=" * 62)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
