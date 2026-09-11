# -*- coding: utf-8 -*-
"""端到端自检：现场生成一个自包含演示拓扑，验证「生成 → 回读 → GBK 落盘」全链路。

用法：
    "<技能目录>/.venv/Scripts/python.exe" scripts/selfcheck.py
若用系统 python 直接跑，会自动切换到技能目录下的 .venv（存在时）。
"""
import os
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
    sys.path.insert(0, str(SKILL_DIR.parent.parent / "src"))  # 仓库内直跑时用仓库 src

    from grbj_ensp_mcp.topo_builder import NativeTopoBuilder

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "自检输出.topo"

        # 自包含演示拓扑：双 AR + 双 USG6000V + 核心 + 接入（7 设备 6 连线）
        devices = [
            ("R1", "AR2220", 2000, 250, 120), ("R2", "AR2220", 2001, 450, 120),
            ("FW1", "USG6000V", 2002, 250, 280), ("FW2", "USG6000V", 2003, 450, 280),
            ("SW-核心", "S5700", 2004, 350, 430),
            ("PC-办公", "PC", 0, 250, 560), ("PC-访客", "PC", 0, 450, 560),
        ]
        lines = [
            ("R1", "FW1", 0, 1), ("R2", "FW2", 0, 1),
            ("FW1", "SW-核心", 2, 1), ("FW2", "SW-核心", 2, 2),
            ("PC-办公", "SW-核心", 0, 3), ("PC-访客", "SW-核心", 0, 4),
        ]
        b = NativeTopoBuilder()
        for name, model, port, x, y in devices:
            b.add_device(name, model=model, com_port=port, cx=x, cy=y)
        for a, bb, si, ti in lines:
            b.add_line(a, bb, src_index=si, tar_index=ti)
        errors = b.validate()
        if errors:
            print("builder 校验失败：", *errors, sep="\n  - ")
            return 1
        xml = b.render()
        # 声明归一化为 eNSP 原版样式（"UNICODE" ?> 前带空格）
        head = xml.index("?>")
        xml = '<?xml version="1.0" encoding="UNICODE" ?>' + xml[head + 2:]
        out.write_bytes(xml.replace("\n", "\r\n").encode("gbk"))

        raw = out.read_bytes()
        text = raw.decode("gbk")
        n_dev, n_line = text.count("<dev "), text.count("<line ")
        ok = True
        ok &= check(raw.startswith(b'<?xml version="1.0" encoding="UNICODE" ?>'),
                    "XML 声明为 eNSP 原生 UNICODE 样式")
        ok &= check(b"\r\n" in raw, "换行为 CRLF")
        ok &= check(not raw.startswith(b"\xef\xbb\xbf"), "无 UTF-8 BOM（文件非 UTF-8）")
        ok &= check("PC-办公" in text and "SW-核心" in text, "中文设备名 GBK 回读无损")
        ok &= check(n_dev == len(devices), f"设备数 = {len(devices)}（实际 {n_dev}）")
        ok &= check(n_line == len(lines), f"连线数 = {len(lines)}（实际 {n_line}）")
        ok &= check('<slot id="1">' in text, "USG6000V 枚举式接口渲染（本地补丁生效）")

        print("\n[结果]", "全部通过，环境可用" if ok else "存在失败项，见上方 FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
