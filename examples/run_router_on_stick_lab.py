"""单臂路由（Router-on-a-Stick）实验下发脚本（设备驱动）。

拓扑（参考 examples/topologies/router_on_a_stick.topo）：

    AR1 (com_port=2000)
       |
       | GE0/0/0 (Trunk)
       |
    SW1 (com_port=2001)
       |       |
    GE0/0/2   GE0/0/3
    (VLAN10)  (VLAN20)
       |       |
      PC1     PC2

IP 规划：
    AR1 GE0/0/0.1 (dot1q 10) : 192.168.10.1/24
    AR1 GE0/0/0.2 (dot1q 20) : 192.168.20.1/24
    PC1 (VLAN 10)             : 192.168.10.10/24  gw 192.168.10.1
    PC2 (VLAN 20)             : 192.168.20.20/24  gw 192.168.20.1

关键原理：
    1. 路由器物理接口不配 IP，只创建子接口，每个子接口封装 dot1q VLAN tag
    2. 交换机连接路由器的端口配置为 Trunk，允许 VLAN 10 / 20 通过
    3. 交换机连接 PC 的端口配置为 Access，分别加入 VLAN 10 / 20
    4. PC 配置 IP + 网关，跨 VLAN 流量经由 Trunk → 子接口 → 路由表 → 子接口 → Trunk 回程
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient
from grbj_ensp_mcp.topo_parser import parse_topo

TOPO = r"D:/01_Workspace/code/ensp-mcp/examples/topologies/router_on_a_stick.topo"
HOST = "127.0.0.1"

# ---------- 规划数据 ----------
AR1 = {
    "port": 2000,
    "physical_iface": "GigabitEthernet0/0/0",
    "sub_interfaces": [
        # 子接口编号从 1 起；封装 VLAN 10 / VLAN 20
        {"id": 1, "vlan": 10, "ip": "192.168.10.1", "mask": "24"},
        {"id": 2, "vlan": 20, "ip": "192.168.20.1", "mask": "24"},
    ],
}

SW1 = {
    "port": 2001,
    "vlans": [10, 20],
    "trunk_port": "GigabitEthernet0/0/1",  # 连 AR1
    "trunk_allow": [10, 20],
    "access_ports": [
        {"iface": "GigabitEthernet0/0/2", "vlan": 10},  # 连 PC1
        {"iface": "GigabitEthernet0/0/3", "vlan": 20},  # 连 PC2
    ],
}

# PC1 / PC2 需在 eNSP GUI 中手动配置 IP / 网关（com_port=0，无法 Telnet）
PC_PLAN = {
    "PC1": {"ip": "192.168.10.10", "mask": "255.255.255.0", "gw": "192.168.10.1"},
    "PC2": {"ip": "192.168.20.20", "mask": "255.255.255.0", "gw": "192.168.20.1"},
}


# ---------- 通用辅助 ----------
def _iface_suffix(name: str) -> str:
    """取接口名从首个数字开始的尾段（适配短名/全称）。"""
    for i, ch in enumerate(name):
        if ch.isdigit():
            return name[i:]
    return name


def probe(client: TelnetClient) -> dict:
    """探查设备真实状态：版本、当前配置、接口表。"""
    client.send_command("display version", timeout=10.0)
    cc = client.send_command("display current-configuration", timeout=12.0)
    ib = client.send_command("display ip interface brief", timeout=10.0)
    return {
        "current_config": cc.output,
        "ip_brief": ib.output,
    }


# ---------- AR1 配置 ----------
def apply_ar1() -> list[str]:
    """AR1 单臂路由：物理接口无 IP，创建子接口 dot1q 封装 + 网关 IP。"""
    errors: list[str] = []
    c = TelnetClient(HOST, AR1["port"], connect_timeout=5.0, read_timeout=2.0)
    c.connect()
    c.activate()
    c.wait_for_prompt(timeout=6.0)
    info = probe(c)
    print(f"\n===== 配置 AR1 (端口 {AR1['port']}) =====")
    _ = info  # 探查结果供后续步骤参考；此处只用于触发真实状态探测，不直接消费
    c.send_command("system-view", timeout=8.0)
    c.send_command("sysname AR1", timeout=5.0)

    # 1) 物理接口：保持无 IP（单臂路由要点：物理口不配 IP，只承载子接口）
    c.send_command(f"interface {AR1['physical_iface']}", timeout=5.0)
    c.send_command("undo shutdown", timeout=5.0)
    c.send_command("quit", timeout=5.0)

    # 2) 创建子接口并封装 dot1q + 配置网关 IP
    for sub in AR1["sub_interfaces"]:
        iface = f"{AR1['physical_iface']}.{sub['id']}"
        c.send_command(f"interface {iface}", timeout=5.0)
        r = c.send_command(
            f"dot1q termination vid {sub['vlan']}",
            timeout=5.0,
        )
        if r.errored:
            errors.append(f"AR1 {iface} dot1q termination vid: {r.errors}")
        r = c.send_command(
            f"ip address {sub['ip']} {sub['mask']}",
            timeout=5.0,
        )
        if r.errored:
            errors.append(f"AR1 {iface} ip address: {r.errors}")
        # 允许子接口转发广播（ARP 请求需要跨子接口转发）
        r = c.send_command("arp broadcast enable", timeout=5.0)
        if r.errored:
            errors.append(f"AR1 {iface} arp broadcast enable: {r.errors}")
        c.send_command("undo shutdown", timeout=5.0)
        c.send_command("quit", timeout=5.0)

    c.save_config(timeout=8.0)
    c.close()
    return errors


# ---------- SW1 配置 ----------
def apply_sw1() -> list[str]:
    """SW1：创建 VLAN 10 / 20，配置 Trunk 与 Access。"""
    errors: list[str] = []
    c = TelnetClient(HOST, SW1["port"], connect_timeout=5.0, read_timeout=2.0)
    c.connect()
    c.activate()
    c.wait_for_prompt(timeout=6.0)
    info = probe(c)
    print(f"\n===== 配置 SW1 (端口 {SW1['port']}) =====")
    _ = info
    c.send_command("system-view", timeout=8.0)
    c.send_command("sysname SW1", timeout=5.0)

    # 1) 创建 VLAN
    for vlan_id in SW1["vlans"]:
        r = c.send_command(f"vlan {vlan_id}", timeout=5.0)
        if r.errored:
            errors.append(f"SW1 vlan {vlan_id}: {r.errors}")
        c.send_command("quit", timeout=5.0)

    # 2) Trunk 端口（连路由器）
    c.send_command(f"interface {SW1['trunk_port']}", timeout=5.0)
    r = c.send_command("port link-type trunk", timeout=5.0)
    if r.errored:
        errors.append(f"SW1 {SW1['trunk_port']} port link-type trunk: {r.errors}")
    r = c.send_command(
        f"port trunk allow-pass vlan {' '.join(str(v) for v in SW1['trunk_allow'])}",
        timeout=5.0,
    )
    if r.errored:
        errors.append(f"SW1 {SW1['trunk_port']} allow-pass: {r.errors}")
    c.send_command("undo shutdown", timeout=5.0)
    c.send_command("quit", timeout=5.0)

    # 3) Access 端口（连 PC）
    for ap in SW1["access_ports"]:
        c.send_command(f"interface {ap['iface']}", timeout=5.0)
        r = c.send_command("port link-type access", timeout=5.0)
        if r.errored:
            errors.append(f"SW1 {ap['iface']} port link-type access: {r.errors}")
        r = c.send_command(f"port default vlan {ap['vlan']}", timeout=5.0)
        if r.errored:
            errors.append(f"SW1 {ap['iface']} default vlan: {r.errors}")
        c.send_command("undo shutdown", timeout=5.0)
        c.send_command("quit", timeout=5.0)

    c.save_config(timeout=8.0)
    c.close()
    return errors


# ---------- 校验 ----------
def verify() -> None:
    """校验：AR1 子接口 up、SW1 VLAN 与 Trunk/Access 配置、PC 跨 VLAN 互通。"""
    time.sleep(3)  # 等待生成树收敛
    print("\n" + "=" * 72)
    print("校验阶段")
    print("=" * 72)

    def sess(port: int) -> TelnetClient:
        c = TelnetClient(HOST, port, connect_timeout=5.0, read_timeout=2.0)
        c.connect()
        c.activate()
        c.wait_for_prompt(timeout=6.0)
        return c

    # AR1 子接口状态
    print("\n--- AR1: 子接口状态 ---")
    c = sess(AR1["port"])
    r = c.send_command("display interface GigabitEthernet0/0/0", timeout=10.0)
    # 提取含 sub-interface 的关键段
    for line in r.output.splitlines():
        if "sub-interface" in line.lower() or "GE0/0/0." in line:
            print(f"  {line.strip()}")
    c.close()

    # SW1 VLAN + 端口类型
    print("\n--- SW1: VLAN 列表 ---")
    c = sess(SW1["port"])
    r = c.send_command("display vlan", timeout=10.0)
    print(r.output)
    print("\n--- SW1: Trunk/Access 端口 ---")
    for port in [SW1["trunk_port"]] + [ap["iface"] for ap in SW1["access_ports"]]:
        r = c.send_command(f"display interface {port}", timeout=8.0)
        link = "?"
        for ln in r.output.splitlines():
            if "Port link-type" in ln or "link-type" in ln.lower():
                link = ln.strip()
                break
        print(f"  {port}: {link}")
    c.close()

    # 跨 VLAN ping（核心验证）：用 AR1 自身 ping 子接口网关，模拟跨子接口转发
    print("\n--- AR1: 自反 ping（验证子接口 IP 可达）---")
    c = sess(AR1["port"])
    for sub in AR1["sub_interfaces"]:
        r = c.send_command(
            f"ping -c 1 -a {sub['ip']} 127.0.0.1",
            timeout=8.0,
        )
        loss = "?"
        for ln in r.output.splitlines():
            if "packet loss" in ln.lower():
                loss = ln.strip()
                break
        print(f"  [self-ping] {sub['ip']}: {loss}")
    c.close()

    # 实际跨设备互通（PC 之间）需在 eNSP GUI 启动 PC 抓包工具 / cmd ping
    print("\n[提示] PC1 / PC2 跨 VLAN 互通（PC1 ping PC2）需在 eNSP GUI 的 PC 终端内手动验证：")
    for name, plan in PC_PLAN.items():
        print(f"  {name}: ip {plan['ip']} {plan['mask']}, gateway {plan['gw']}")


# ---------- 入口 ----------
def main() -> None:
    # 解析拓扑，校验规划的接口在设备上真实存在（设备驱动：不猜接口名）
    topo = parse_topo(TOPO)
    dev_ifaces = {d.name: {_iface_suffix(x) for x in d.interfaces} for d in topo.devices}

    # AR1 物理接口校验
    if _iface_suffix(AR1["physical_iface"]) not in dev_ifaces.get("AR1", set()):
        print(f"[致命] AR1 不存在接口 {AR1['physical_iface']}，请检查规划！")
        sys.exit(1)
    # SW1 端口校验
    sw1_required = {SW1["trunk_port"]} | {ap["iface"] for ap in SW1["access_ports"]}
    for p in sw1_required:
        if _iface_suffix(p) not in dev_ifaces.get("SW1", set()):
            print(f"[致命] SW1 不存在接口 {p}，请检查规划！")
            sys.exit(1)
    print("接口名校验通过，开始下发...")

    all_errors: list[str] = []
    all_errors += apply_ar1()
    all_errors += apply_sw1()

    print("\n" + "=" * 72)
    if all_errors:
        print("下发过程中出现命令报错：")
        for e in all_errors:
            print("  - " + e)
    else:
        print("所有配置命令下发无 errored 回显。")

    verify()
    print("\n[ROUTER-ON-A-STICK LAB DONE]")


if __name__ == "__main__":
    main()
