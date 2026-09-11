"""OSPF 单区域全网互联互通实验下发脚本（设备驱动）。

范围（用户确认：AR4 作为 PC 使用）：
- OSPF 路由器：AR1 / AR2 / AR3（三台网关），进程 1，Area 0，精准反掩码宣告，无静态/默认路由。
- AR4 当作终端：配 IP + 默认网关指向 AR3，不跑 OSPF。
- PC1 为真实 PC，无 Telnet，需在 eNSP GUI 配 192.168.2.0/24、网关 192.168.2.2。

IP 规划（依据实际拓扑连线 + 用户需求文档固定地址）：
  AR1 GE0/0/0 - AR2 GE0/0/0 : 192.168.12.0/24  (AR1 .1 / AR2 .2)
  AR1 GE0/0/1 - AR3 GE0/0/1 : 192.168.13.0/24  (AR1 .1 / AR3 .3)  [拓扑存在但需求未定义，纳入全网]
  AR2 GE0/0/2 - AR3 GE0/0/2 : 192.168.23.0/24  (AR2 .2 / AR3 .3)
  AR1 GE0/0/2 (LAN)         : 192.168.1.0/24   gw 192.168.1.1
  AR2 GE0/0/1 - PC1 (LAN)   : 192.168.2.0/24   gw 192.168.2.2
  AR3 GE0/0/0 - AR4 (LAN)   : 192.168.3.0/24   gw 192.168.3.3
  AR4 GE0/0/0 (作为PC)       : 192.168.3.4/24   默认网关 192.168.3.3
"""

import sys
import time

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient
from grbj_ensp_mcp.topo_parser import parse_topo

TOPO = r"D:/01_Workspace/code/ictstu-gitbook/06网络实验/IPv4编址及IPv4路由基础实验/IPv4编址及IPv4路由基础实验.topo"
HOST = "127.0.0.1"

# 三台 OSPF 网关路由器的接口与 OSPF 网络（精准反掩码）
ROUTERS = {
    "AR1": {
        "port": 2000,
        "router_id": "1.1.1.1",
        "interfaces": {
            "GigabitEthernet0/0/0": "192.168.12.1 255.255.255.0",
            "GigabitEthernet0/0/1": "192.168.13.1 255.255.255.0",
            # 真实拓扑里 AR1 的 GE0/0/2 未接任何设备（无物理 PC），该接口 protocol 永远 down，
            # VRP 的 OSPF 不会通告 protocol-down 接口的网段。故用 Loopback0 承载 AR1 的 LAN，
            # 保证 192.168.1.0/24 可被通告且全网可达（如需物理 LAN，在 eNSP 给 AR1 接一台 PC 即可）。
            "LoopBack0": "192.168.1.1 255.255.255.0",
        },
        "ospf": ["192.168.12.0 0.0.0.255", "192.168.13.0 0.0.0.255", "192.168.1.0 0.0.0.255"],
    },
    "AR2": {
        "port": 2001,
        "router_id": "2.2.2.2",
        "interfaces": {
            "GigabitEthernet0/0/0": "192.168.12.2 255.255.255.0",
            "GigabitEthernet0/0/2": "192.168.23.2 255.255.255.0",
            "GigabitEthernet0/0/1": "192.168.2.2 255.255.255.0",
        },
        "ospf": ["192.168.12.0 0.0.0.255", "192.168.23.0 0.0.0.255", "192.168.2.0 0.0.0.255"],
    },
    "AR3": {
        "port": 2002,
        "router_id": "3.3.3.3",
        "interfaces": {
            "GigabitEthernet0/0/1": "192.168.13.3 255.255.255.0",
            "GigabitEthernet0/0/2": "192.168.23.3 255.255.255.0",
            "GigabitEthernet0/0/0": "192.168.3.3 255.255.255.0",
        },
        "ospf": ["192.168.13.0 0.0.0.255", "192.168.23.0 0.0.0.255", "192.168.3.0 0.0.0.255"],
    },
}

# AR4 作为终端（PC）
AR4 = {
    "port": 2003,
    "interface": "GigabitEthernet0/0/0",
    "ip": "192.168.3.4 255.255.255.0",
    "gateway": "192.168.3.3",
}


def probe(client: TelnetClient) -> dict:
    """探查设备真实状态（版本 / 当前配置 / 接口），用于生成命令前确认。"""
    client.send_command("display version", timeout=10.0)
    cc = client.send_command("display current-configuration", timeout=12.0)
    ib = client.send_command("display ip interface brief", timeout=10.0)
    return {
        "current_config": cc.output,
        "ip_brief": ib.output,
    }


def apply_router(name: str, spec: dict) -> list[str]:
    """对单台 OSPF 路由器执行：探查 → system-view → 配接口 → 配 OSPF → save。返回错误列表。"""
    errors: list[str] = []
    c = TelnetClient(HOST, spec["port"], connect_timeout=5.0, read_timeout=2.0)
    c.connect()
    c.activate()
    c.wait_for_prompt(timeout=6.0)
    info = probe(c)

    # 设备驱动校验：当前配置应为空（裸设备），否则告警
    if "ospf" in info["current_config"] or "ip address" in info["current_config"]:
        print(f"  [警告] {name} 已有配置，继续覆盖式下发")

    print(f"\n===== 配置 {name} (端口 {spec['port']}) =====")
    c.send_command("system-view", timeout=8.0)
    c.send_command(f"sysname {name}", timeout=5.0)

    # 1) 接口 IP
    for iface, ipmask in spec["interfaces"].items():
        c.send_command(f"interface {iface}", timeout=5.0)
        r = c.send_command(f"ip address {ipmask}", timeout=5.0)
        if r.errored:
            errors.append(f"{name} {iface} ip address: {r.errors}")
        c.send_command("undo shutdown", timeout=5.0)
        c.send_command("quit", timeout=5.0)

    # 2) OSPF 进程 1 / Area 0
    c.send_command("ospf 1", timeout=5.0)
    c.send_command(f"router-id {spec['router_id']}", timeout=5.0)
    c.send_command("area 0", timeout=5.0)
    for net in spec["ospf"]:
        r = c.send_command(f"network {net}", timeout=5.0)
        if r.errored:
            errors.append(f"{name} network {net}: {r.errors}")
    c.send_command("quit", timeout=5.0)  # 退出 area
    c.send_command("quit", timeout=5.0)  # 退出 ospf -> system-view

    # 3) save（此时仍在 system-view，save_config 的 quit 会退回用户视图）
    c.save_config(timeout=8.0)
    c.close()
    return errors


def apply_ar4_pc() -> list[str]:
    """AR4 作为终端：配 IP + 默认网关，不跑 OSPF。"""
    errors: list[str] = []
    c = TelnetClient(HOST, AR4["port"], connect_timeout=5.0, read_timeout=2.0)
    c.connect()
    c.activate()
    c.wait_for_prompt(timeout=6.0)
    probe(c)
    print(f"\n===== 配置 AR4 (作为 PC, 端口 {AR4['port']}) =====")
    c.send_command("system-view", timeout=8.0)
    c.send_command("sysname AR4", timeout=5.0)
    c.send_command("interface " + AR4["interface"], timeout=5.0)
    r = c.send_command(f"ip address {AR4['ip']}", timeout=5.0)
    if r.errored:
        errors.append(f"AR4 ip address: {r.errors}")
    c.send_command("undo shutdown", timeout=5.0)
    c.send_command("quit", timeout=5.0)
    r = c.send_command(f"ip route-static 0.0.0.0 0.0.0.0 {AR4['gateway']}", timeout=5.0)
    if r.errored:
        errors.append(f"AR4 default route: {r.errors}")
    # 结束于 system-view，save_config 的 quit 退回用户视图
    c.save_config(timeout=8.0)
    c.close()
    return errors


def verify() -> None:
    """校验：OSPF 邻居 FULL、路由表 O 路由、跨段 ping。"""
    time.sleep(8)  # 等待 OSPF 邻居收敛
    print("\n" + "=" * 72)
    print("校验阶段")
    print("=" * 72)

    def sess(port: int) -> TelnetClient:
        c = TelnetClient(HOST, port, connect_timeout=5.0, read_timeout=2.0)
        c.connect()
        c.activate()
        c.wait_for_prompt(timeout=6.0)
        return c

    for name, spec in ROUTERS.items():
        c = sess(spec["port"])
        print(f"\n--- {name}: OSPF 邻居 ---")
        r = c.send_command("display ospf peer brief", timeout=10.0)
        print(r.output)
        print(f"--- {name}: 路由表(O 路由) ---")
        r = c.send_command("display ip routing-table protocol ospf", timeout=10.0)
        print(r.output)
        c.close()

    # 跨设备 ping（取每台路由器各 ping 对端两个网关）
    pings = {
        "AR1": ["192.168.2.2", "192.168.3.3", "192.168.23.3"],
        "AR2": ["192.168.1.1", "192.168.3.3"],
        "AR3": ["192.168.1.1", "192.168.2.2"],
    }
    for name, targets in pings.items():
        c = sess(ROUTERS[name]["port"])
        for t in targets:
            r = c.send_command(f"ping {t}", timeout=12.0)
            loss = "?"
            for ln in r.output.splitlines():
                if "packet loss" in ln or "loss" in ln.lower():
                    loss = ln.strip()
            print(f"[ping] {name} -> {t}: {loss}")
        c.close()

    # AR4 作为 PC：默认网关 ping 全网
    c = sess(AR4["port"])
    for t in ["192.168.1.1", "192.168.2.2"]:
        r = c.send_command(f"ping {t}", timeout=12.0)
        loss = "?"
        for ln in r.output.splitlines():
            if "packet loss" in ln or "loss" in ln.lower():
                loss = ln.strip()
        print(f"[ping] AR4(PC) -> {t}: {loss}")
    c.close()


def _iface_suffix(name: str) -> str:
    """取接口名中从首个数字开始的尾段（如 GE0/0/0 / GigabitEthernet0/0/0 均得 0/0/0）。

    用于跨格式校验接口是否存在，避免短名(GE0/0/0)与规范全称(GigabitEthernet0/0/0)误判。
    """
    for i, ch in enumerate(name):
        if ch.isdigit():
            return name[i:]
    return name


def main() -> None:
    # 解析拓扑，校验规划的接口在设备上真实存在（设备驱动：不猜接口名）
    topo = parse_topo(TOPO)
    dev_ifaces = {d.name: {_iface_suffix(x) for x in d.interfaces} for d in topo.devices}
    for name, spec in ROUTERS.items():
        for iface in spec["interfaces"]:
            if _iface_suffix(iface) not in dev_ifaces.get(name, set()):
                print(f"[致命] {name} 不存在接口 {iface}，请检查规划！")
                sys.exit(1)
    if _iface_suffix(AR4["interface"]) not in dev_ifaces.get("AR4", set()):
        print(f"[致命] AR4 不存在接口 {AR4['interface']}！")
        sys.exit(1)
    print("接口名校验通过，开始下发...")

    all_errors: list[str] = []
    for name, spec in ROUTERS.items():
        all_errors += apply_router(name, spec)
    all_errors += apply_ar4_pc()

    print("\n" + "=" * 72)
    if all_errors:
        print("下发过程中出现命令报错：")
        for e in all_errors:
            print("  - " + e)
    else:
        print("所有配置命令下发无 errored 回显。")

    verify()
    print("\n[OSPF LAB DONE]")


if __name__ == "__main__":
    main()
