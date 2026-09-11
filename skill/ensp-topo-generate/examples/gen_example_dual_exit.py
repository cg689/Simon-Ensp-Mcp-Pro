# -*- coding: utf-8 -*-
"""示例拓扑：双出口冗余 + 双防火墙（USG6000V）+ 双核心 + 接入 + 终端。

演示要点：
  - 各型号接口 index 换算（AR 0 起 / USG6000V 枚举式 / S5700 偏移 +1 / S3700 22E+2GE）
  - 中文设备名（GBK 落盘，eNSP 内不乱码）
  - 分层坐标布局（出口 → 路由 → 防火墙 → 核心 → 接入 → 终端）
  - 支持第 1 个命令行参数指定输出路径（便于自检脚本调用）

运行：
    "<技能目录>/.venv/Scripts/python.exe" gen_example_dual_exit.py [输出路径]
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder
from grbj_ensp_mcp.topo_parser import parse_topo

OUT = sys.argv[1] if len(sys.argv) > 1 else "示例-双出口拓扑.topo"

# ---------------------- 接口名 -> XML index 映射 --------------------------
def ar_ge(n):        # AR2220: GE0/0/n（0 起）
    return n

def usg_ge1(n):      # USG6000V: GE1/0/n（0 起；MGMT=GE0/0/0 恒为 0，不用于连线）
    return n + 1

def s5700_ge(n):     # S5700: GE0/0/n（1 起，偏移 +1）
    return n - 1

def s3700_eth(n):    # S3700: Ethernet0/0/n（1 起）
    return n - 1

def s3700_ge(n):     # S3700: GE0/0/n（1 起；22 个 Ethernet 之后）
    return 21 + n

def first():         # 单口设备（PC / Server / STA / AP）
    return 0

# ------------------------------ 设备表 ------------------------------------
# (名称, 型号, com_port, cx, cy) —— 无 CLI 设备（PC/Server/STA/AP）com_port 填 0
D = [
    ("ISP1",     "AR2220",   2000, 140,  40),
    ("ISP2",     "AR2220",   2001, 420,  40),
    ("AR-EG-1",  "AR2220",   2002, 180, 140),
    ("AR-EG-2",  "AR2220",   2003, 380, 140),
    ("USG-FW1",  "USG6000V", 2004, 180, 240),
    ("USG-FW2",  "USG6000V", 2005, 380, 240),
    ("SW-Core1", "S5700",    2006, 240, 340),
    ("SW-Core2", "S5700",    2007, 380, 340),
    ("SW-接入",   "S3700",    2008, 310, 460),
    ("PC-办公",   "PC",       0,    250, 560),
    ("PC-访客",   "PC",       0,    370, 560),
]

# ------------------------------ 连线表 ------------------------------------
# (源, 源index, 目标, 目标index, 说明)
L = [
    ("ISP1",    ar_ge(0),  "AR-EG-1",  ar_ge(0),       "ISP1 GE0/0/0 <-> AR-EG-1 GE0/0/0"),
    ("ISP2",    ar_ge(0),  "AR-EG-2",  ar_ge(0),       "ISP2 GE0/0/0 <-> AR-EG-2 GE0/0/0"),
    ("AR-EG-1", ar_ge(1),  "AR-EG-2",  ar_ge(1),       "AR1 GE0/0/1 <-> AR2 GE0/0/1 双机互联"),
    ("AR-EG-1", ar_ge(2),  "USG-FW1",  usg_ge1(0),     "AR1 GE0/0/2 <-> USG1 GE1/0/0"),
    ("AR-EG-2", ar_ge(2),  "USG-FW2",  usg_ge1(0),     "AR2 GE0/0/2 <-> USG2 GE1/0/0"),
    ("USG-FW1", usg_ge1(3), "USG-FW2", usg_ge1(3),     "USG1 GE1/0/3 <-> USG2 GE1/0/3 HRP 心跳"),
    ("USG-FW1", usg_ge1(1), "SW-Core1", s5700_ge(1),   "USG1 GE1/0/1 <-> Core1 GE0/0/1"),
    ("USG-FW2", usg_ge1(1), "SW-Core2", s5700_ge(1),   "USG2 GE1/0/1 <-> Core2 GE0/0/1"),
    ("SW-Core1", s5700_ge(23), "SW-Core2", s5700_ge(23), "Core1 GE0/0/23 <-> Core2 GE0/0/23 互联①"),
    ("SW-Core1", s5700_ge(24), "SW-Core2", s5700_ge(24), "Core1 GE0/0/24 <-> Core2 GE0/0/24 互联②"),
    ("SW-Core1", s5700_ge(2), "SW-接入",  s3700_ge(1),  "Core1 GE0/0/2 <-> 接入 GE0/0/1"),
    ("SW-Core2", s5700_ge(2), "SW-接入",  s3700_ge(2),  "Core2 GE0/0/2 <-> 接入 GE0/0/2"),
    ("PC-办公",  first(),   "SW-接入",  s3700_eth(3),  "PC-办公 <-> 接入 Ethernet0/0/3"),
    ("PC-访客",  first(),   "SW-接入",  s3700_eth(4),  "PC-访客 <-> 接入 Ethernet0/0/4"),
]

# ------------------------------ 生成 + 自检 -------------------------------
b = NativeTopoBuilder()
for name, model, port, cx, cy in D:
    b.add_device(name, model=model, com_port=port, cx=cx, cy=cy)
for src, si, dst, ti, _ in L:
    b.add_line(src, dst, src_index=si, tar_index=ti)

errors = b.validate()
if errors:
    print("builder 校验失败:", *errors, sep="\n  - ")
    sys.exit(1)
xml = b.render()

import tempfile
with tempfile.NamedTemporaryFile("w", suffix=".topo", encoding="utf-8", delete=False) as tf:
    tf.write(xml)
    tmp_path = tf.name

t = parse_topo(tmp_path)
assert {d.name for d in t.devices} == {n for n, *_ in D}, "回读设备清单不符"
print(f"[1] parser 回读：设备 {len(t.devices)} 台，连线 {len(t.connections)} 条")

by_pair = {}
for c in t.connections:
    by_pair.setdefault(frozenset([c.src_device, c.dst_device]), []).append(c)
miss = []
for src, si, dst, ti, desc in L:
    hits = by_pair.get(frozenset([src, dst]), [])
    if not any((c.src_index, c.dst_index) in [(si, ti), (ti, si)] for c in hits):
        miss.append(desc)
if miss:
    print("[2] FAIL 以下连线回读未命中:", *("  - " + m for m in miss), sep="\n")
    sys.exit(1)
print(f"[2] 连线回读 {len(L)} 条逐条命中（含 index）")

# 落盘：eNSP 原生格式（UNICODE 假声明 + CRLF + GBK，无 BOM）——UTF-8 会让中文乱码
xml_ensp = re.sub(r"^<\?xml[^?]*\?>", '<?xml version="1.0" encoding="UNICODE" ?>', xml)
xml_ensp = xml_ensp.replace("\n", "\r\n")
with open(OUT, "wb") as f:
    f.write(xml_ensp.encode("gbk"))
print(f"[3] GBK 落盘 OK -> {OUT}")

with open(OUT, "rb") as f:
    rt = f.read().decode("gbk")
cn = [n for n, *_ in D if any("\u4e00" <= ch <= "\u9fff" for ch in n)]
assert all(n in rt for n in cn), "GBK 回读丢字符"
print(f"[4] GBK 回读校验：中文设备名无损 {cn}")
