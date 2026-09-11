"""LLDP 验证模块单元测试。

覆盖：
- 接口名归一化（``GE0/0/0`` ↔ ``GigabitEthernet0/0/0`` 等）
- brief / detail 两种输出格式的解析
- ``compare_topology_with_lldp`` 双向比对的 4 种场景
- Markdown 报告渲染
"""

from __future__ import annotations

import pytest

from grbj_ensp_mcp.lldp_verifier import (
    LLDPNeighbor,
    LLDPScanResult,
    compare_topology_with_lldp,
    interfaces_match,
    looks_like_lldp_not_enabled,
    normalize_interface,
    parse_lldp_neighbor,
    parse_lldp_neighbor_brief,
    parse_lldp_output,
    render_comparison_markdown,
)
from grbj_ensp_mcp.topo_parser import Connection, Device, Topology, parse_topo_bytes


# ---------------------------------------------------------------------- #
# 接口名归一化
# ---------------------------------------------------------------------- #
class TestNormalizeInterface:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("GE0/0/0", "GigabitEthernet0/0/0"),
            ("GE 0/0/0", "GigabitEthernet0/0/0"),
            ("GigabitEthernet0/0/0", "GigabitEthernet0/0/0"),
            ("GigabitEthernet 0/0/0", "GigabitEthernet0/0/0"),
            ("Gig 0/0/0", "GigabitEthernet0/0/0"),
            ("GigabitEth 0/0/0", "GigabitEthernet0/0/0"),
            ("Ethernet0/0/0", "Ethernet0/0/0"),
            ("Eth0/0/0", "Ethernet0/0/0"),
            ("Eth 0/0/0", "Ethernet0/0/0"),
            ("LoopBack0", "LoopBack0"),
            ("Loopback0", "LoopBack0"),  # 输入大小写不敏感 → 归一为 B 大写
            ("LoopBack 0", "LoopBack0"),
            ("Loop 0", "LoopBack0"),
            ("Vlanif10", "Vlanif10"),
            ("Vlanif 10", "Vlanif10"),
            ("Eth-Trunk1", "Eth-Trunk1"),
            ("Eth-Trunk 1", "Eth-Trunk1"),
            ("Serial1/0/0", "Serial1/0/0"),
            ("XGE0/0/0", "TenGigabitEthernet0/0/0"),
        ],
    )
    def test_normalize_common(self, raw: str, expected: str) -> None:
        assert normalize_interface(raw) == expected

    def test_normalize_empty(self) -> None:
        assert normalize_interface("") == ""
        assert normalize_interface("   ") == ""

    def test_normalize_unknown_prefix_preserves_case(self) -> None:
        # 未识别的接口族，原样返回（保留大小写）
        assert normalize_interface("MEth0/0/1") == "MEth0/0/1"

    def test_interfaces_match(self) -> None:
        assert interfaces_match("GE0/0/0", "GigabitEthernet0/0/0") is True
        assert interfaces_match("Gig 0/0/0", "GigabitEthernet 0/0/0") is True
        assert interfaces_match("GE0/0/0", "GE0/0/1") is False
        assert interfaces_match("GE0/0/0", "Eth0/0/0") is False


# ---------------------------------------------------------------------- #
# LLDP brief 输出解析
# ---------------------------------------------------------------------- #
BRIEF_OUTPUT = """
GE0/0/0                     AR2                    GE0/0/1                    120
GE0/0/1                     SW1                    GigabitEthernet0/0/24     100

Total neighbors displayed: 2
<Huawei>
"""

# 另一版本：表头完整但列分隔是 | 字符
BRIEF_OUTPUT_PIPE = """\
Local Intf     Neighbor Dev     Neighbor Intf      Aging
GE0/0/0        AR2              GE0/0/1            120
GigabitEthernet0/0/2    AR3         GigabitEthernet0/0/2    110
<Huawei>
"""

# 带 ---- More ---- 的多页输出（罕见，但真实设备遇到过）
BRIEF_OUTPUT_PAGED = """\
GE0/0/0        AR2              GE0/0/1            120
  ---- More ----
GE0/0/1        SW1              GE0/0/24           100
<Huawei>
"""


class TestParseLLDPNeighborBrief:
    def test_basic(self) -> None:
        neighbors = parse_lldp_neighbor_brief(BRIEF_OUTPUT, local_device="AR1")
        assert len(neighbors) == 2
        assert neighbors[0].local_device == "AR1"
        assert neighbors[0].local_interface == "GigabitEthernet0/0/0"
        assert neighbors[0].remote_device == "AR2"
        assert neighbors[0].remote_interface == "GigabitEthernet0/0/1"
        assert neighbors[0].age == "120"

    def test_pipe_format(self) -> None:
        # 同样的解析策略对 "Local Intf | Neighbor Dev | ..." 表头也要兼容
        neighbors = parse_lldp_neighbor_brief(BRIEF_OUTPUT_PIPE, local_device="AR1")
        # 跳过表头 "Local Intf ..."
        assert len(neighbors) == 2
        assert neighbors[0].local_interface == "GigabitEthernet0/0/0"
        assert neighbors[1].local_interface == "GigabitEthernet0/0/2"

    def test_paged_output(self) -> None:
        # 跳过 "---- More ----" 行
        neighbors = parse_lldp_neighbor_brief(BRIEF_OUTPUT_PAGED, local_device="AR1")
        assert len(neighbors) == 2

    def test_empty(self) -> None:
        assert parse_lldp_neighbor_brief("", local_device="AR1") == []
        assert parse_lldp_neighbor_brief("<Huawei>", local_device="AR1") == []

    def test_only_header(self) -> None:
        neighbors = parse_lldp_neighbor_brief(
            "Local Intf     Neighbor Dev     Neighbor Intf     Aging\n<Huawei>\n",
            local_device="AR1",
        )
        assert neighbors == []


# ---------------------------------------------------------------------- #
# LLDP detail 输出解析
# ---------------------------------------------------------------------- #
DETAIL_OUTPUT = """
LLDP neighbor-information of port GigabitEthernet0/0/0:
Neighbor index : 1
Update time    : 0 days, 0 hours, 1 minutes, 30 seconds
Device type    : AR2240
Device ID      : 0030-1122-3344
Port ID        : GigabitEthernet0/0/1
System name    : AR2
TTL            : 120

LLDP neighbor-information of port GigabitEthernet0/0/1:
Neighbor index : 1
Update time    : 0 days, 0 hours, 2 minutes, 0 seconds
Device type    : S5700
Device ID      : 0050-AABB-CCDD
Port ID        : GigabitEthernet0/0/24
System name    : SW1
TTL            : 100

<Huawei>
"""


class TestParseLLDPNeighbor:
    def test_basic(self) -> None:
        neighbors = parse_lldp_neighbor(DETAIL_OUTPUT, local_device="AR1")
        assert len(neighbors) == 2
        # 第一段：GE0/0/0 -> AR2/GE0/0/1
        n0 = neighbors[0]
        assert n0.local_device == "AR1"
        assert n0.local_interface == "GigabitEthernet0/0/0"
        assert n0.remote_device == "AR2"
        assert n0.remote_interface == "GigabitEthernet0/0/1"
        assert n0.remote_chassis_id == "0030-1122-3344"
        assert n0.remote_system_name == "AR2"
        assert n0.ttl == "120"

        # 第二段：GE0/0/1 -> SW1/GE0/0/24
        n1 = neighbors[1]
        assert n1.local_interface == "GigabitEthernet0/0/1"
        assert n1.remote_device == "SW1"
        assert n1.remote_chassis_id == "0050-AABB-CCDD"

    def test_empty(self) -> None:
        assert parse_lldp_neighbor("", local_device="AR1") == []

    def test_skip_segments_without_port_id(self) -> None:
        # 没 Port ID 的段应被忽略
        output = """
LLDP neighbor-information of port GigabitEthernet0/0/0:
Neighbor index : 1
Device type    : AR2240
System name    : AR2
"""
        neighbors = parse_lldp_neighbor(output, local_device="AR1")
        assert neighbors == []  # 没有 Port ID，段被丢弃


# ---------------------------------------------------------------------- #
# parse_lldp_output 自动识别
# ---------------------------------------------------------------------- #
class TestParseLLDPOutput:
    def test_auto_detect_detail(self) -> None:
        neighbors = parse_lldp_output(DETAIL_OUTPUT, local_device="AR1")
        assert len(neighbors) == 2

    def test_auto_detect_brief(self) -> None:
        neighbors = parse_lldp_output(BRIEF_OUTPUT, local_device="AR1")
        assert len(neighbors) == 2

    def test_force_brief(self) -> None:
        # force=True 时即便传入 detail 格式，也强制走 brief 解析
        neighbors = parse_lldp_output(
            DETAIL_OUTPUT, local_device="AR1", brief=True, force=True
        )
        # detail 格式按 brief 解析会找不到任何数据行（因为格式完全不同）
        assert neighbors == []


# ---------------------------------------------------------------------- #
# 拓扑交叉验证
# ---------------------------------------------------------------------- #
def _build_simple_topo() -> Topology:
    """构造一个简单的 AR1—AR2—AR3 拓扑。"""
    topo = Topology(name="unit-test")
    topo.devices = [
        Device(
            id="d1",
            name="AR1",
            model="AR2240",
            console_port=2000,
            device_type="router",
            interfaces=["GigabitEthernet0/0/0", "GigabitEthernet0/0/1"],
        ),
        Device(
            id="d2",
            name="AR2",
            model="AR2240",
            console_port=2001,
            device_type="router",
            interfaces=["GigabitEthernet0/0/0", "GigabitEthernet0/0/1"],
        ),
        Device(
            id="d3",
            name="AR3",
            model="AR2240",
            console_port=2002,
            device_type="router",
            interfaces=["GigabitEthernet0/0/0"],
        ),
    ]
    topo.connections = [
        Connection(
            src_device="AR1",
            dst_device="AR2",
            src_interface="GigabitEthernet0/0/0",
            dst_interface="GigabitEthernet0/0/0",
            link_type="Copper",
        ),
        Connection(
            src_device="AR2",
            dst_device="AR3",
            src_interface="GigabitEthernet0/0/1",
            dst_interface="GigabitEthernet0/0/0",
            link_type="Copper",
        ),
    ]
    return topo


class TestCompareTopologyWithLLDP:
    """交叉验证的 4 种场景。"""

    def test_matched_all(self) -> None:
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GigabitEthernet0/0/0",
                        remote_device="AR2",
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                ],
            ),
            LLDPScanResult(
                device="AR2",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR2",
                        local_interface="GigabitEthernet0/0/1",
                        remote_device="AR3",
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                    # 反向通告同一物理边，应被去重
                    LLDPNeighbor(
                        local_device="AR2",
                        local_interface="GigabitEthernet0/0/0",
                        remote_device="AR1",
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        assert len(cmp.matched) == 2
        assert cmp.topo_only == []
        assert cmp.lldp_only == []
        assert cmp.mismatched == []

    def test_topo_only(self) -> None:
        """AR1↔AR2 在 .topo 里，但 AR2 没启 → LLDP 只看到 AR2↔AR3 一边。"""
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(device="AR1", neighbors=[]),  # AR1 没看到任何邻居
            LLDPScanResult(
                device="AR2",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR2",
                        local_interface="GigabitEthernet0/0/1",
                        remote_device="AR3",
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        # matched: AR2↔AR3
        assert len(cmp.matched) == 1
        # topo_only: AR1↔AR2（拓扑有，LLDP 没看到）
        assert len(cmp.topo_only) == 1
        assert cmp.topo_only[0]["src_device"] in ("AR1", "AR2")
        assert cmp.lldp_only == []
        # 备注里应该有「AR1 没看到任何 LLDP 邻居」
        assert any("AR1" in n for n in cmp.notes)

    def test_lldp_only(self) -> None:
        """LLDP 看到了一根 .topo 没记录的线。"""
        topo = _build_simple_topo()  # 只有 AR1↔AR2 和 AR2↔AR3
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GigabitEthernet0/0/1",
                        remote_device="SW1",  # 拓扑里没有 SW1
                        remote_interface="GigabitEthernet0/0/24",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        # 这条新边在 LLDP 里出现，但不在 .topo 中
        assert any(e["dst_device"] == "SW1" for e in cmp.lldp_only)
        # 备注里应该提示 SW1 不在拓扑里
        assert any("SW1" in n for n in cmp.notes)

    def test_mismatched_interface(self) -> None:
        """.topo 说 AR1↔AR2 是 GE0/0/0↔GE0/0/0，但 LLDP 实际是 GE0/0/0↔GE0/0/1。"""
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GigabitEthernet0/0/0",
                        remote_device="AR2",
                        remote_interface="GigabitEthernet0/0/1",  # 拓扑说是 0/0/0
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        # AR1 端点匹配（AR1 GE0/0/0 一致），但 AR2 端错位
        assert len(cmp.mismatched) == 1
        assert cmp.mismatched[0]["match_status"] == "interface_mismatch"
        assert cmp.mismatched[0]["dst_interface"] == "GigabitEthernet0/0/0"  # 拓扑
        assert cmp.mismatched[0]["lldp_dst_interface"] == "GigabitEthernet0/0/1"  # LLDP

    def test_short_interface_names_match(self) -> None:
        """接口名归一化：拓扑用 GE0/0/0，LLDP 用 GE0/0/0，应匹配。"""
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GE0/0/0",  # 缩写
                        remote_device="AR2",
                        remote_interface="GE0/0/0",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        assert len(cmp.matched) == 1

    def test_alias_mapping(self) -> None:
        """LLDP system-name 是 AR2，但 .topo 用 R2 → 用 device_aliases 映射。"""
        topo = Topology(name="alias-test")
        topo.devices = [
            Device(id="d1", name="R1", model="AR2240", console_port=2000, device_type="router",
                   interfaces=["GigabitEthernet0/0/0"]),
            Device(id="d2", name="R2", model="AR2240", console_port=2001, device_type="router",
                   interfaces=["GigabitEthernet0/0/0"]),
        ]
        topo.connections = [
            Connection(
                src_device="R1",
                dst_device="R2",
                src_interface="GigabitEthernet0/0/0",
                dst_interface="GigabitEthernet0/0/0",
            ),
        ]
        scans = [
            LLDPScanResult(
                device="R1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="R1",
                        local_interface="GigabitEthernet0/0/0",
                        remote_device="AR2",  # 没映射时这是个未知名
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                ],
            ),
        ]
        # 没别名 → LLDP 端点 "AR2" 不在 topo 里，会被视为 lldp_only
        cmp = compare_topology_with_lldp(topo, scans)
        assert any(e["dst_device"] == "AR2" for e in cmp.lldp_only)
        # 加别名 → 应被归并到 R2，匹配上
        cmp2 = compare_topology_with_lldp(
            topo, scans, device_aliases={"AR2": "R2"}
        )
        assert len(cmp2.matched) == 1
        assert cmp2.lldp_only == []

    def test_lldp_collection_failure_noted(self) -> None:
        """LLDP 采集失败的设备应被记入 notes。"""
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(
                device="AR2",
                neighbors=[],
                errored=True,
                error_message="LLDP not enabled",
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        assert any("LLDP 采集失败" in n for n in cmp.notes)
        assert any("未使能 LLDP" in n or "未使能" in n for n in cmp.notes)


# ---------------------------------------------------------------------- #
# Markdown 报告渲染
# ---------------------------------------------------------------------- #
class TestRenderMarkdown:
    def test_render_with_matched(self) -> None:
        topo = _build_simple_topo()
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GigabitEthernet0/0/0",
                        remote_device="AR2",
                        remote_interface="GigabitEthernet0/0/0",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        md = render_comparison_markdown(cmp)
        assert "# LLDP × .topo 拓扑交叉验证报告" in md
        assert "AR1" in md
        assert "AR2" in md

    def test_render_empty(self) -> None:
        empty_topo = Topology(name="empty")
        md = render_comparison_markdown(compare_topology_with_lldp(empty_topo, []))
        assert "未采集到任何连线数据" in md


# ---------------------------------------------------------------------- #
# 与真实 .topo 解析结果的对接
# ---------------------------------------------------------------------- #
class TestWithRealTopo:
    """确认对接到 parse_topo_bytes 输出时也能正常工作。"""

    TOPO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<topo version="1.3.00.100">
  <devices>
    <dev id="d1" name="AR1" model="AR2240" com_port="2000" cx="100" cy="100" system_mac="00-00-00-00-00-01">
      <slot number="slot17" isMainBoard="1">
        <interface sztype="Ethernet" interfacename="GE" count="2" />
      </slot>
    </dev>
    <dev id="d2" name="AR2" model="AR2240" com_port="2001" cx="300" cy="100" system_mac="00-00-00-00-00-02">
      <slot number="slot17" isMainBoard="1">
        <interface sztype="Ethernet" interfacename="GE" count="2" />
      </slot>
    </dev>
  </devices>
  <lines>
    <line srcDeviceID="d1" destDeviceID="d2">
      <interfacePair lineName="Copper" srcIndex="0" tarIndex="0" />
    </line>
  </lines>
  <shapes />
  <txttips />
</topo>
"""

    def test_compare_with_parsed_topo(self) -> None:
        topo = parse_topo_bytes(self.TOPO_XML.encode("utf-8"), name="real-topo")
        # 拓扑：AR1.GE0/0/0 <-> AR2.GE0/0/0
        scans = [
            LLDPScanResult(
                device="AR1",
                neighbors=[
                    LLDPNeighbor(
                        local_device="AR1",
                        local_interface="GE0/0/0",
                        remote_device="AR2",
                        remote_interface="GE0/0/0",
                    ),
                ],
            ),
        ]
        cmp = compare_topology_with_lldp(topo, scans)
        # 拓扑用 GE0/0/0 / GigabitEthernet0/0/0 都能匹配
        assert len(cmp.matched) == 1


# ---------------------------------------------------------------------- #
# looks_like_lldp_not_enabled —— LLDP 未使能检测
# ---------------------------------------------------------------------- #
class TestLooksLikeLLDPNotEnabled:
    """覆盖华为 eNSP / VRP 在 LLDP 未使能时的各种回显。"""

    @pytest.mark.parametrize(
        "raw",
        [
            "LLDP is not enabled.",
            "Error: LLDP is not enabled globally",
            "LLDP is not enabled globally.",
            "Warning: LLDP is disabled on this device.",
            "LLDP功能未使能",
            "LLDP 功能未使能",
            "LLDP未使能",
            "LLDP 全局未使能",
            "Info: LLDP is not enabled. Please run 'lldp enable' first.",
            "<Huawei>display lldp nei brief\nLLDP is not enabled.\n<Huawei>",
            # 大小写不敏感
            "lldp is not enabled",
            "LLDP IS NOT ENABLED",
            "LLDP功能未使能",
        ],
    )
    def test_detect_not_enabled(self, raw: str) -> None:
        assert looks_like_lldp_not_enabled(raw) is True

    @pytest.mark.parametrize(
        "raw",
        [
            "",  # 空串
            "<Huawei>",  # 普通 prompt
            "GE0/0/0                     AR2                    GE0/0/1                    120\n<Huawei>",  # 正常的 brief 输出
            "LLDP neighbor-information of port GigabitEthernet0/0/0:\n...\n<Huawei>",  # 正常的 detail 输出
            "LLDP is enabled.",  # 强调"已使能"
            "Total neighbors displayed: 2",  # 计数行
        ],
    )
    def test_does_not_misclassify(self, raw: str) -> None:
        assert looks_like_lldp_not_enabled(raw) is False
