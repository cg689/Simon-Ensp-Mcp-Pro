# -*- coding: utf-8 -*-
"""一键建环境：建 venv → 安装 grbj-ensp-mcp → 应用本地补丁（USG6000V + S3700）。

用法（用任意 Python 3.10+ 执行一次即可）：
    python scripts/setup_env.py
    python scripts/setup_env.py --venv D:/myenv/ensp

完成后会打印解释器绝对路径，用它执行生成脚本。
依赖需要联网（拉 mcp SDK 等）；纯离线时把全部依赖 wheel 放进 assets/wheels/ 再执行。
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
PATCHED = SKILL_DIR / "scripts" / "patched" / "topo_builder.py"


def run(cmd):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=str(SKILL_DIR / ".venv"),
                    help="venv 目标目录（默认建在技能目录下 .venv）")
    ap.add_argument("--python", default=sys.executable,
                    help="用于创建 venv 的解释器（需 3.10+）")
    args = ap.parse_args()

    if sys.version_info < (3, 10):
        print(f"[x] 需要 Python 3.10+，当前 {sys.version.split()[0]}")
        return 1

    wheels = sorted(ASSETS.glob("grbj_ensp_mcp-*.whl"))
    if not wheels:
        print(f"[x] assets 目录下未找到 grbj_ensp_mcp-*.whl：{ASSETS}")
        return 1
    whl = wheels[-1]

    venv_dir = Path(args.venv).expanduser().resolve()
    if not (venv_dir / "pyvenv.cfg").exists():
        print(f"[1] 创建 venv -> {venv_dir}")
        run([args.python, "-m", "venv", str(venv_dir)])
    else:
        print(f"[1] venv 已存在 -> {venv_dir}")

    py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        print(f"[x] venv 解释器不存在：{py}")
        return 1

    print(f"[2] 安装 {whl.name}")
    run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    extra = []
    local = ASSETS / "wheels"
    if local.is_dir():
        extra = ["--find-links", str(local)]
        print(f"    检测到离线依赖目录：{local}")
    run([py, "-m", "pip", "install", "--quiet", str(whl), *extra])

    print("[3] 应用本地补丁（USG6000V 枚举式支持 + S3700 实测接口表）")
    probe = subprocess.check_output(
        [py, "-c", "import grbj_ensp_mcp, os; print(os.path.dirname(grbj_ensp_mcp.__file__))"],
        text=True,
    ).strip()
    target = Path(probe) / "topo_builder.py"
    shutil.copyfile(PATCHED, target)
    if "USG6000V" not in target.read_text(encoding="utf-8"):
        print(f"[x] 补丁写入失败：{target}")
        return 1
    print(f"    已写入 {target}（补丁标记 USG6000V 命中）")

    print("\n[done] 环境就绪，解释器路径：")
    print(f"    {py}")
    print("下一步自检：")
    print(f'    "{py}" "{SKILL_DIR / "scripts" / "selfcheck.py"}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
