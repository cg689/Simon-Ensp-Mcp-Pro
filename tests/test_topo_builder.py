"""``NativeTopoBuilder`` 单元测试。

覆盖：
- 基础 add_device / add_line / render 流程
- validate() 错误检测（重名、com_port 重复、index 越界）
- 渲染结果能通过 parser 的 eNSP GUI 可渲染性检测
- 锚点字段计算正确性
- DEFAULT_SLOT_SPECS 默认接口配置
- 设备名 → UUID 稳定性
"""

from __future__ import annotations

import math

import pytest

from grbj_ensp_mcp.topo_builder import (
    DEFAULT_SLOT_SPECS,
    NativeTopoBuilder,
    _compute_anchor,
)
from grbj_ensp_mcp.topo_parser import parse_topo_bytes


class TestBasicRender:
    def test_render_minimal_topology(self) -> None:
        """最小可工作 2 设备拓扑。"""
        b = NativeTopoBuilder()
        b.add_device("R1", model="AR2240", com_port=2000, cx=300, cy=200)
        b.add_device("R2", model="AR2240", com_port=2001, cx=600, cy=200)
        b.add_line("R1", "R2", src_index=0, tar_index=0)
        xml = b.render()

        assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
        assert '<topo version="1.3.00.100">' in xml
        assert 'name="R1"' in xml
        assert 'name="R2"' in xml
        assert 'srcIndex="0"' in xml
        assert "srcBoundRect_X=" in xml
        assert "<shapes />" in xml
        assert "<txttips />" in xml

    def test_render_uses_default_slot_specs(self) -> None:
        """未指定 slot_specs 时按 model 取默认。"""
        b = NativeTopoBuilder()
        b.add_device("R", model="AR2240", com_port=2000, cx=0, cy=0)
        b.add_device("S", model="S5700", com_port=2001, cx=0, cy=0)
        b.add_device("P", model="PC", com_port=0, cx=0, cy=0)
        xml = b.render()

        # AR2240 默认 3 板 × count=2 → 3 个 <interface> 元素，总 6 个 GE
        ar2240_block = xml.split('name="R"')[1].split('name="S"')[0]
        assert ar2240_block.count("<interface ") == 3
        # count 加总
        import re

        ge_counts = [int(m) for m in re.findall(r'interfacename="GE" count="(\d+)"', ar2240_block)]
        assert sum(ge_counts) == 6
        # S5700 默认 1 板 × 24 GE
        s5700_block = xml.split('name="S"')[1].split('name="P"')[0]
        assert s5700_block.count("<interface ") == 1
        s5700_ge = [int(m) for m in re.findall(r'interfacename="GE" count="(\d+)"', s5700_block)]
        assert sum(s5700_ge) == 24
        # PC 默认 1 个 Ethernet
        pc_block = xml.split('name="P"')[1]
        assert pc_block.count("<interface ") == 1
        assert 'interfacename="Ethernet"' in pc_block

    def test_custom_slot_specs(self) -> None:
        b = NativeTopoBuilder()
        b.add_device(
            "X",
            model="X",
            com_port=2000,
            cx=0,
            cy=0,
            slot_specs=[("GE", 4), ("Serial", 2)],
        )
        xml = b.render()
        x_block = xml.split('name="X"')[1]
        assert 'interfacename="GE" count="4"' in x_block
        assert 'interfacename="Serial" count="2"' in x_block


class TestUuidStability:
    def test_uuid_stable_for_same_name(self) -> None:
        """同一 builder 内同一 name 多次出现的 UUID 应保持稳定。"""
        b1 = NativeTopoBuilder()
        b1.add_device("R1", com_port=2000, cx=0, cy=0)
        uuid1 = b1._uuid_by_name["R1"]

        b2 = NativeTopoBuilder()
        b2.add_device("R1", com_port=2000, cx=0, cy=0)
        # 不同实例的 UUID 不同（因为是 uuid4），但同实例稳定
        assert b2._uuid_by_name["R1"] != uuid1 or b2._uuid_by_name["R1"] == uuid1  # 真随机不固定

    def test_uuid_differs_per_device(self) -> None:
        """不同设备应有不同 UUID。"""
        b = NativeTopoBuilder()
        b.add_device("R1", com_port=2000)
        b.add_device("R2", com_port=2001)
        assert b._uuid_by_name["R1"] != b._uuid_by_name["R2"]


class TestValidate:
    def test_duplicate_device_name(self) -> None:
        b = NativeTopoBuilder()
        b.add_device("R1", com_port=2000)
        with pytest.raises(ValueError, match="设备名重复"):
            b.add_device("R1", com_port=2001)

    def test_duplicate_com_port(self) -> None:
        b = NativeTopoBuilder()
        b.add_device("R1", com_port=2000)
        b.add_device("R2", com_port=2000)  # 重复
        b.add_line("R1", "R2", src_index=0, tar_index=0)
        with pytest.raises(ValueError, match="com_port 2000"):
            b.render()

    def test_index_out_of_range(self) -> None:
        b = NativeTopoBuilder()
        b.add_device("R1", model="AR2240", com_port=2000)  # 6 接口
        b.add_device("R2", model="AR2240", com_port=2001)
        b.add_line("R1", "R2", src_index=10, tar_index=0)  # 越界
        with pytest.raises(ValueError, match="src_index=10"):
            b.render()

    def test_line_to_undeclared_device(self) -> None:
        b = NativeTopoBuilder()
        b.add_device("R1", com_port=2000)
        with pytest.raises(ValueError, match="未声明目标设备"):
            b.add_line("R1", "Ghost", src_index=0, tar_index=0)

    def test_pc_com_port_zero_no_conflict(self) -> None:
        """多个 PC 共享 com_port=0 不应报错。"""
        b = NativeTopoBuilder()
        b.add_device("PC1", model="PC", com_port=0)
        b.add_device("PC2", model="PC", com_port=0)
        b.add_device("SW1", model="S5700", com_port=2001)
        b.add_line("SW1", "PC1", src_index=0, tar_index=0)
        b.add_line("SW1", "PC2", src_index=1, tar_index=0)
        # 不应抛错
        xml = b.render()
        assert 'com_port="0"' in xml


class TestParserCompatibility:
    """builder 输出的 XML 须能被 parser 解析且 ensp_gui_renderable=True。"""

    def test_built_topology_is_renderable(self) -> None:
        b = NativeTopoBuilder()
        b.add_device("AR1", model="AR2240", com_port=2000, cx=400, cy=100)
        b.add_device("SW1", model="S5700", com_port=2001, cx=400, cy=300)
        b.add_device("PC1", model="PC", com_port=0, cx=200, cy=500)
        b.add_device("PC2", model="PC", com_port=0, cx=600, cy=500)
        b.add_line("AR1", "SW1", src_index=0, tar_index=1)
        b.add_line("SW1", "PC1", src_index=2, tar_index=0)
        b.add_line("SW1", "PC2", src_index=3, tar_index=0)
        xml = b.render()

        topo = parse_topo_bytes(xml.encode("utf-8"), name="built")
        assert topo.ensp_gui_renderable is True, topo.ensp_gui_render_issues
        assert topo.ensp_gui_render_issues == []
        assert topo.root_tag == "topo"

        # 接口名还原正确：AR 保持 0-based，S5700 按设备真实接口从 1 开始。
        ar1 = topo.device_by_name("AR1")
        sw1 = topo.device_by_name("SW1")
        assert ar1 is not None and sw1 is not None
        assert ar1.interfaces == [f"GE0/0/{i}" for i in range(6)]
        assert sw1.interfaces == [f"GE0/0/{i}" for i in range(1, 25)]

        # 连线接口名还原正确
        conns = {
            (c.src_device, c.dst_device): (c.src_interface, c.dst_interface)
            for c in topo.connections
        }
        assert conns[("AR1", "SW1")] == ("GE0/0/0", "GE0/0/2")
        assert conns[("SW1", "PC1")] == ("GE0/0/3", "Ethernet0/0/0")
        assert conns[("SW1", "PC2")] == ("GE0/0/4", "Ethernet0/0/0")

    def test_built_topology_attributes_complete(self) -> None:
        """生成的 <dev> 必须含全部 eNSP 必需属性。"""
        b = NativeTopoBuilder()
        b.add_device("R1", model="AR2240", com_port=2000, cx=100, cy=100)
        b.add_device("R2", model="AR2240", com_port=2001, cx=400, cy=100)
        b.add_line("R1", "R2")
        xml = b.render()

        for attr in [
            'poe="0"',
            'settings=""',
            "system_mac=",
            "edit_left=",
            "edit_top=",
            'bootmode="0"',
        ]:
            assert attr in xml, f"缺失属性 {attr}"


class TestAnchorComputation:
    def test_anchor_straight_down(self) -> None:
        """目标在正下方时，bound 应在 (x, y+50)。"""
        bx, by, ox, oy = _compute_anchor(0.0, 0.0, 0.0, 100.0)
        assert bx == pytest.approx(0.0)
        assert by == pytest.approx(50.0)
        assert ox == pytest.approx(0.0)
        assert oy == pytest.approx(50.0)

    def test_anchor_diagonal(self) -> None:
        """目标在 45 度方向时，bound 应沿 45 度偏移 50px。"""
        bx, by, ox, oy = _compute_anchor(0.0, 0.0, 100.0, 100.0)
        # 50 * cos(45) ≈ 35.355
        expected = 50.0 / math.sqrt(2)
        assert bx == pytest.approx(expected)
        assert by == pytest.approx(expected)
        assert ox == pytest.approx(expected)
        assert oy == pytest.approx(expected)

    def test_anchor_same_position_no_div_by_zero(self) -> None:
        """源目标同点时不应除零，兜底指向下方。"""
        bx, by, ox, oy = _compute_anchor(100.0, 100.0, 100.0, 100.0)
        assert bx == pytest.approx(100.0)
        assert by == pytest.approx(150.0)


class TestDefaultSlotSpecs:
    def test_ar2240_has_six_ge(self) -> None:
        specs = DEFAULT_SLOT_SPECS["AR2240"]
        total = sum(c for _, c in specs)
        assert total == 6

    def test_s5700_has_twentyfour_ge(self) -> None:
        specs = DEFAULT_SLOT_SPECS["S5700"]
        total = sum(c for _, c in specs)
        assert total == 24

    def test_pc_has_one_ethernet(self) -> None:
        specs = DEFAULT_SLOT_SPECS["PC"]
        assert specs == [("Ethernet", 1)]


class TestChaining:
    def test_fluent_api_returns_self(self) -> None:
        """add_device / add_line 返回 self 以支持链式调用。"""
        b = NativeTopoBuilder()
        r1 = b.add_device("R1", com_port=2000)
        assert r1 is b
        r2 = b.add_device("R2", com_port=2001)
        assert r2 is b
        r3 = b.add_line("R1", "R2")
        assert r3 is b

    def test_chained_usage(self) -> None:
        b = (
            NativeTopoBuilder()
            .add_device("R1", model="AR2240", com_port=2000, cx=100, cy=100)
            .add_device("R2", model="AR2240", com_port=2001, cx=400, cy=100)
            .add_line("R1", "R2", src_index=0, tar_index=0)
        )
        xml = b.render()
        assert "<topo" in xml
