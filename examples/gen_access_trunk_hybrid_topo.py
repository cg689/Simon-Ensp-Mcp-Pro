"""用 :class:`NativeTopoBuilder` 生成 Access / Trunk / Hybrid 接口测试拓扑。

本脚本**只打印 XML 到 stdout**，不写任何文件。
落地 ``.topo`` 由专用写入工具（Write）完成，避免脚本越过工作流边界。

典型学习链路（用户手动启动 eNSP 后再下发配置）：

    SW1 <-> SW2     GE0/0/3     Trunk（演示跨设备 VLAN 透传）
    SW1.GE0/0/0 -> PC1           Access  VLAN 10
    SW1.GE0/0/1 -> PC2           Hybrid  PVID 20 / untagged VLAN 20
    SW1.GE0/0/2 -> PC3           Hybrid  PVID 30 / untagged VLAN 30 + tagged VLAN 20
    SW2.GE0/0/1 -> PC4           Access  VLAN 10

预期可达性（由用户配置后验证）：

    * PC1 <-> PC4   同 VLAN 10，跨 Trunk 互通
    * PC2 独立      VLAN 20，与 PC3 tagged 互通
    * PC3 接收      untagged VLAN 30（自身）+ tagged VLAN 20（来自 PC2）
    * PC1 / PC4 与 PC2 / PC3     默认隔离（除非 Hybrid 加 untagged VLAN 10）

PC 的 IP / 网关直接写进 ``settings``，eNSP 启动 PC 后立即生效，无需手工 ping 命令。
注：脚本只生成 topo，交换机侧（VLAN / 接口模式）配置仍由用户在 eNSP 启动后
通过 MCP 的 ``ensp_send_commands`` 下发。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder
from grbj_ensp_mcp.topo_parser import parse_topo_bytes

# ---------------------------------------------------------------------- #
# PC 预设：每个 PC 携带 IP / 掩码 / 网关 / MAC，eNSP 启动后自动应用
# ---------------------------------------------------------------------- #
# settings 头尾各保留一个空格；MAC 写两处（system_mac + -simpc_mac）保持一致。
_PC_BASE = (
    " -simpc_ip {ip}  -simpc_mask 255.255.255.0  -simpc_gateway {gw}"
    "  -simpc_mac {mac}  -simpc_mc_dstip 0.0.0.0  -simpc_mc_dstmac 00-00-00-00-00-00"
    "  -simpc_dns1 0.0.0.0  -simpc_dns2 0.0.0.0  -simpc_ipv6 ::  -simpc_prefix 128"
    "  -simpc_gatewayv6 ::  -simpc_dhcp_state 0  -simpc_dhcpv6_state 0"
    "  -simpc_dns_auto_state 0  -simpc_igmp_version 1  -simpc_group_ip_start 0.0.0.0"
    "  -simpc_src_ip_start 0.0.0.0  -simpc_group_num 0  -simpc_group_step 0"
    "  -simpc_src_num 0  -simpc_src_step 0  -simpc_type MODE_IS_INCLUDE "
)


def _pc_settings(ip: str, gw: str, mac: str) -> str:
    return _PC_BASE.format(ip=ip, gw=gw, mac=mac)


def build_access_trunk_hybrid_xml() -> str:
    """构造 Access/Trunk/Hybrid 接口测试拓扑的 XML 字符串。"""
    b = NativeTopoBuilder()

    # --- 设备清单（com_port 严格 2000 -> 2001 递增） ---
    b.add_device("SW1", model="S5700", com_port=2000, cx=400, cy=300)
    b.add_device("SW2", model="S5700", com_port=2001, cx=700, cy=300)

    # --- 终端主机（带 IP / 网关 / MAC） ---
    # PC1: VLAN 10
    b.add_device(
        "PC1",
        model="PC",
        com_port=0,
        cx=150,
        cy=500,
        system_mac="54-89-98-14-7D-DE",
        settings=_pc_settings("192.168.1.1", "192.168.1.254", "54-89-98-14-7D-DE"),
    )
    # PC2: VLAN 20
    b.add_device(
        "PC2",
        model="PC",
        com_port=0,
        cx=350,
        cy=500,
        system_mac="54-89-98-22-7D-DE",
        settings=_pc_settings("192.168.2.1", "192.168.2.254", "54-89-98-22-7D-DE"),
    )
    # PC3: VLAN 30（untagged）+ VLAN 20（tagged 通过 Hybrid）
    b.add_device(
        "PC3",
        model="PC",
        com_port=0,
        cx=550,
        cy=500,
        system_mac="54-89-98-33-7D-DE",
        settings=_pc_settings("192.168.3.1", "192.168.3.254", "54-89-98-33-7D-DE"),
    )
    # PC4: VLAN 10（跨设备，与 PC1 同网段）
    b.add_device(
        "PC4",
        model="PC",
        com_port=0,
        cx=900,
        cy=500,
        system_mac="54-89-98-44-7D-DE",
        settings=_pc_settings("192.168.1.2", "192.168.1.254", "54-89-98-44-7D-DE"),
    )

    # --- 连线 ---
    # S5700 的 XML src_index 是 0-based，对应设备真实接口号需 +1：
    # XML 0/1/2/3 → 设备 GE0/0/1/2/3/4。
    # SW1 GE0/0/1 -> PC1（Access VLAN 10）
    b.add_line("SW1", "PC1", src_index=0, tar_index=0)
    # SW1 GE0/0/2 -> PC2（Hybrid PVID 20 / untagged VLAN 20）
    b.add_line("SW1", "PC2", src_index=1, tar_index=0)
    # SW1 GE0/0/3 -> PC3（Hybrid untagged VLAN 30 + tagged VLAN 20）
    b.add_line("SW1", "PC3", src_index=2, tar_index=0)
    # SW1 GE0/0/4 <-> SW2 GE0/0/1（Trunk）
    b.add_line("SW1", "SW2", src_index=3, tar_index=0)
    # SW2 GE0/0/2 -> PC4（Access VLAN 10）
    b.add_line("SW2", "PC4", src_index=1, tar_index=0)

    return b.render()


def main() -> None:
    xml = build_access_trunk_hybrid_xml()

    # 自检：用 parser 解析刚生成的 XML，确认 eNSP GUI 可渲染
    topo = parse_topo_bytes(xml.encode("utf-8"), name="access_trunk_hybrid")
    if not topo.ensp_gui_renderable:
        print(
            "FATAL: builder 输出仍不可被 eNSP GUI 渲染，问题：",
            topo.ensp_gui_render_issues,
            file=sys.stderr,
        )
        sys.exit(1)

    print(xml)


if __name__ == "__main__":
    main()
