"""只读诊断：解析给定 .topo 并打印设备/接口/连线 + 端口可达性。

不向设备发送任何配置命令，仅做 TCP 探测。
"""

import sys

TOPO = r"D:/01_Workspace/code/ictstu-gitbook/06网络实验/IPv4编址及IPv4路由基础实验/IPv4编址及IPv4路由基础实验.topo"

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.config import DEFAULT_CONFIG  # noqa: E402
from grbj_ensp_mcp.port_scanner import scan_single_port  # noqa: E402
from grbj_ensp_mcp.topo_parser import parse_topo  # noqa: E402

topo = parse_topo(TOPO)

print("=" * 70)
print(f"拓扑名: {topo.name}  格式: {topo.source_format}")
print(f"设备数: {len(topo.devices)}  连线数: {len(topo.connections)}")
print("=" * 70)

for d in topo.devices:
    print(
        f"\n设备: {d.name}  型号={d.model or '?'}  类型={d.device_type}  console_port={d.console_port}"
    )
    print(f"  接口列表 ({len(d.interfaces)}): {d.interfaces}")

print("\n" + "=" * 70)
print("连线:")
for c in topo.connections:
    print(
        f"  {c.src_device}({c.src_interface}) <-> {c.dst_device}({c.dst_interface})  链路={c.link_type or '?'}"
    )

print("\n" + "=" * 70)
print(f"端口探测 (host={DEFAULT_CONFIG.host}):")
for d in topo.devices:
    port = d.console_port
    if port <= 1:
        print(f"  {d.name}: console_port={port} (跳过)")
        continue
    r = scan_single_port(DEFAULT_CONFIG.host, port, timeout=2.0)
    print(
        f"  {d.name}: port={port} reachable={r.reachable} {('err=' + r.error) if r.error else ''}"
    )
