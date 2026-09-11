# -*- coding: utf-8 -*-
"""
USG6000V / S3700 本地补丁验证（enhanced fork 核心增强项）
=========================================================
直接使用仓库 src/（无需安装包），渲染一个含 USG6000V + S3700 的最小拓扑，
断言：枚举式 USG 节点、S3700 修正接口表、中文设备名、连线解析。

用法：
  python tests/verify_usg6000v.py          # 任意 Python 3.10+，无需 pip 安装
"""

import sys
import tempfile
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
REPO_ROOT = REPO_SRC.parent
sys.path.insert(0, str(REPO_SRC))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder  # noqa: E402
from grbj_ensp_mcp.topo_parser import parse_topo  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        fails.append(name)


print("=" * 62)
print(" USG6000V / S3700 补丁验证")
print("=" * 62)

# 1 ------------------------------------------------------ 渲染最小拓扑
b = NativeTopoBuilder()
b.add_device("FW-测试", model="USG6000V", com_port=2000)
b.add_device("SW-测试", model="S3700", com_port=2001)
b.add_line("FW-测试", "SW-测试", src_index=1, tar_index=22)  # USG GE1/0/0 <-> S3700 GE0/0/23
xml = b.render()

check("1a. USG6000V 枚举式节点（<slot id=\"1\">）", '<slot id="1">' in xml)
check("1b. USG6000V 逐口声明（slotIndex）", 'slotIndex="1"' in xml)
check("1c. S3700 接口表 = 22 Ethernet + 2 GE", 'count="22"' in xml)
check("1d. builder 校验通过", not b.validate(), "" if not b.validate() else str(b.validate()))

# 2 ---------------------------------------- 落盘（GBK + UNICODE 声明）
s = xml.replace('encoding="UTF-8"', 'encoding="UNICODE"', 1)
s = s.replace("\n", "\r\n")
raw = s.encode("gbk")
with tempfile.NamedTemporaryFile(suffix=".topo", delete=False, dir=str(REPO_ROOT)) as tf:
    tmp = Path(tf.name)
    tmp.write_bytes(raw)
check("2. GBK 落盘成功", tmp.stat().st_size > 0, f"{tmp.stat().st_size} bytes")

# 3 --------------------------------------------- parser 回读（UTF-8 副本）
# 注：parser 只认 UTF-8，GBK 文件须先转码为临时副本再解析
u8 = tmp.read_bytes().decode("gbk")
u8_tmp = tmp.with_suffix(".utf8.topo")
u8_tmp.write_text(u8.replace('encoding="UNICODE"', 'encoding="utf-8"', 1), encoding="utf-8")
t = parse_topo(str(u8_tmp))
check("3a. 回读设备数 = 2", len(t.devices) == 2, f"实际 {len(t.devices)}")
check("3b. 回读连线数 = 1", len(t.connections) == 1, f"实际 {len(t.connections)}")
check("3c. 中文设备名回读无损",
      {d.name for d in t.devices} == {"FW-测试", "SW-测试"})

tmp.unlink(missing_ok=True)
u8_tmp.unlink(missing_ok=True)

# ------------------------------------------------------------------ 汇总
print("-" * 62)
print(" 补丁验证 ALL PASS" if not fails else f" {len(fails)} 项 FAIL：{'; '.join(fails)}")
print("=" * 62)
sys.exit(0 if not fails else 1)
