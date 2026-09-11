# -*- coding: utf-8 -*-
"""eNSP 拓扑生成模板 —— 复制到目标目录后改 D / L 两张表即可。

用法：
    "<技能目录>/.venv/Scripts/python.exe" gen_xxx_topo.py [输出路径]
    （技能目录与解释器路径由 scripts/setup_env.py 打印）

只需改三处（下方有标注）：OUT 默认输出名、D 设备表、L 连线表。
说明书里写接口名（GE0/0/1），脚本用 helper 转 index，**不要手算 index**。
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder
from grbj_ensp_mcp.topo_parser import parse_topo

# ========================== 改这里 1/3：输出路径 ==========================
# 命令行第 1 个参数优先；否则输出到当前目录下的这个名字
OUT = sys.argv[1] if len(sys.argv) > 1 else "新拓扑.topo"

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

def ac_ge(n):        # AC6005: GE0/0/n（1 起，按交换机惯例推定）
    return n - 1

def first():         # 单口设备（PC / Server / STA / AP）
    return 0

# ========================= 改这里 2/3：设备表 =============================
# (名称, 型号, com_port, cx, cy) —— 无 CLI 设备（PC/Server/STA/AP）com_port 填 0
D = [
    ("R1",      "AR2220", 2000, 100, 40),
    ("R2",      "AR2220", 2001, 380, 40),
    ("SW1",     "S5700",  2002, 240, 160),
    ("PC-测试",  "PC",     0,    160, 280),
    ("PC-2",    "PC",     0,    320, 280),
]

# ========================= 改这里 3/3：连线表 =============================
# (源, 源index, 目标, 目标index, 说明)
L = [
    ("R1",     ar_ge(0), "SW1", s5700_ge(1), "R1 GE0/0/0 <-> SW1 GE0/0/1"),
    ("R2",     ar_ge(0), "SW1", s5700_ge(2), "R2 GE0/0/0 <-> SW1 GE0/0/2"),
    ("PC-测试", first(), "SW1", s5700_ge(3), "PC-测试 <-> SW1 GE0/0/3"),
    ("PC-2",   first(), "SW1", s5700_ge(4), "PC-2 <-> SW1 GE0/0/4"),
]
# =========================================================================

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

# ---- parser 回读自检（parser 只认 UTF-8，故写临时 UTF-8 副本再解析） ----
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

# ---- 落盘：eNSP 原生格式（UNICODE 假声明 + CRLF + GBK，无 BOM） ----
# 注意：UTF-8 写盘会让中文设备名在 eNSP 里变乱码，勿改。
xml_ensp = re.sub(r"^<\?xml[^?]*\?>", '<?xml version="1.0" encoding="UNICODE" ?>', xml)
xml_ensp = xml_ensp.replace("\n", "\r\n")
with open(OUT, "wb") as f:
    f.write(xml_ensp.encode("gbk"))
print(f"[3] GBK 落盘 OK -> {OUT}")

with open(OUT, "rb") as f:
    rt = f.read().decode("gbk")
cn = [n for n, *_ in D if any("\u4e00" <= ch <= "\u9fff" for ch in n)]
assert all(n in rt for n in cn), "GBK 回读丢字符"
print(f"[4] GBK 回读校验：中文设备名无损 {cn if cn else '（本次无中文名）'}")

print("\ncom_port 规划：")
for name, model, port, *_ in D:
    if port:
        print(f"    {name:<10} {model:<9} 127.0.0.1:{port}")
