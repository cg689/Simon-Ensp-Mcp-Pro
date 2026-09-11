# -*- coding: utf-8 -*-
"""端到端自检：跑一遍示例拓扑，验证「生成 → 回读 → GBK 落盘」全链路。

用法：
    "<技能目录>/.venv/Scripts/python.exe" scripts/selfcheck.py
若用系统 python 直接跑，会自动切换到技能目录下的 .venv（存在时）。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VENV_PY = SKILL_DIR / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def ensure_env() -> None:
    try:
        import grbj_ensp_mcp  # noqa: F401
    except ImportError:
        if VENV_PY.exists():
            os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve())])
        sys.exit("[x] 运行环境未就绪，请先执行：python scripts/setup_env.py")


def check(cond: bool, label: str) -> bool:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    return bool(cond)


def main() -> int:
    ensure_env()
    example = SKILL_DIR / "examples" / "gen_example_dual_exit.py"
    if not example.exists():
        print(f"[x] 找不到示例脚本：{example}")
        return 1

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "自检输出.topo"
        r = subprocess.run([sys.executable, str(example), str(out)],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            print("[x] 示例脚本执行失败")
            return 1

        raw = out.read_bytes()
        text = raw.decode("gbk")
        n_dev, n_line = text.count("<dev "), text.count("<line ")
        ok = True
        ok &= check(raw.startswith(b'<?xml version="1.0" encoding="UNICODE" ?>'),
                    "XML 声明为 eNSP 原生 UNICODE 样式")
        ok &= check(b"\r\n" in raw, "换行为 CRLF")
        ok &= check(not raw.startswith(b"\xef\xbb\xbf"), "无 UTF-8 BOM（文件非 UTF-8）")
        ok &= check("PC-办公" in text and "SW-接入" in text, "中文设备名 GBK 回读无损")
        ok &= check(n_dev == 11, f"设备数 = 11（实际 {n_dev}）")
        ok &= check(n_line == 14, f"连线数 = 14（实际 {n_line}）")
        ok &= check('<slot id="1">' in text, "USG6000V 枚举式接口渲染（本地补丁生效）")

        print("\n[结果]", "全部通过，环境可用" if ok else "存在失败项，见上方 FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
