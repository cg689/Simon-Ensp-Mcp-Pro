"""eNSP 拓扑解析单元测试。

测试数据采用**真实 eNSP ``.topo`` 文件的格式**（来自广然笔记的 IPv4 路由基础实验），
不再用自造样例，确保 parser 兼容实际生产格式。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from grbj_ensp_mcp.topo_parser import (
    Connection,
    Device,
    _guess_device_type,
    parse_topo,
    parse_topo_bytes,
)

# 真实 eNSP .topo 样本（取自 ictstu-gitbook 06网络实验/IPv4编址及IPv4路由基础实验.topo）
REAL_TOPO_XML = """<?xml version="1.0" encoding="UNICODE" ?>
<topo version="1.3.00.100">
    <devices>
        <dev id="D1" name="PC1" model="PC" com_port="0" cx="70" cy="260" system_mac="00-00-00-00-00-01">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="Ethernet" count="1" />
            </slot>
        </dev>
        <dev id="D2" name="AR1" model="AR2240" com_port="2000" cx="384" cy="105" system_mac="00-E0-FC-41-57-4F">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="1" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
        <dev id="D3" name="AR2" model="AR2240" com_port="2001" cx="207" cy="250" system_mac="00-E0-FC-8B-31-1C">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="1" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
        <dev id="D4" name="AR3" model="AR2240" com_port="2002" cx="513" cy="253" system_mac="00-E0-FC-75-75-72">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="1" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
        <dev id="D5" name="AR4" model="AR2240" com_port="2003" cx="797" cy="253" system_mac="00-E0-FC-32-6D-D7">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="1" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
    </devices>
    <lines>
        <line srcDeviceID="D2" destDeviceID="D3">
            <interfacePair lineName="Copper" srcIndex="0" tarIndex="0" />
        </line>
        <line srcDeviceID="D2" destDeviceID="D4">
            <interfacePair lineName="Copper" srcIndex="1" tarIndex="1" />
        </line>
        <line srcDeviceID="D4" destDeviceID="D3">
            <interfacePair lineName="Copper" srcIndex="2" tarIndex="2" />
        </line>
        <line srcDeviceID="D3" destDeviceID="D1">
            <interfacePair lineName="Copper" srcIndex="1" tarIndex="0" />
        </line>
        <line srcDeviceID="D4" destDeviceID="D5">
            <interfacePair lineName="Copper" srcIndex="0" tarIndex="0" />
        </line>
    </lines>
    <shapes />
    <txttips />
</topo>
"""


class TestParseRealTopo:
    """真实 eNSP ``.topo`` 格式解析测试。"""

    def test_device_count(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        assert len(topo.devices) == 5

    def test_device_types(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        types = {d.name: d.device_type for d in topo.devices}
        assert types["PC1"] == "pc"
        for r in ("AR1", "AR2", "AR3", "AR4"):
            assert types[r] == "router", f"{r} 应该是 router，实际 {types[r]}"

    def test_console_ports(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        ports = {d.name: d.console_port for d in topo.devices}
        assert ports == {"PC1": 0, "AR1": 2000, "AR2": 2001, "AR3": 2002, "AR4": 2003}

    def test_interface_list(self) -> None:
        """AR2240 的两个 GE 板应还原为 GE0/0/0, GE0/0/1, GE0/0/2。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        ar1 = topo.device_by_name("AR1")
        assert ar1 is not None
        assert ar1.interfaces == ["GE0/0/0", "GE0/0/1", "GE0/0/2"]
        assert ar1.interface_name(0) == "GE0/0/0"
        assert ar1.interface_name(2) == "GE0/0/2"
        pc1 = topo.device_by_name("PC1")
        assert pc1 is not None
        assert pc1.interfaces == ["Ethernet0/0/0"]

    def test_interface_lookup(self) -> None:
        """Device.interface_name(idx) 应能根据 XML 全局索引返回设备真实接口名。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        ar1 = topo.device_by_name("AR1")
        assert ar1 is not None
        assert ar1.interface_name(0) == "GE0/0/0"
        assert ar1.interface_name(1) == "GE0/0/1"
        assert ar1.interface_name(2) == "GE0/0/2"
        assert ar1.interface_name(99) == ""  # 越界返回空串

    def test_s5700_interface_index_offset(self) -> None:
        """S5700 的 XML srcIndex=0 应对应设备真实 GE0/0/1。"""
        xml = """<topo version="1.3.00.100">
          <devices>
            <dev id="D1" name="SW1" model="S5700" com_port="2000" poe="0"
                 settings="" system_mac="00-E0-FC-00-00-01" bootmode="0" cx="0" cy="0"
                 edit_left="27" edit_top="54">
              <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="24" />
              </slot>
            </dev>
          </devices>
          <lines>
            <line srcDeviceID="D1" destDeviceID="D1">
              <interfacePair lineName="Copper" srcIndex="0" tarIndex="1" />
            </line>
          </lines>
          <shapes /><txttips />
        </topo>"""
        topo = parse_topo_bytes(xml.encode(), name="s5700")
        sw1 = topo.device_by_name("SW1")
        assert sw1 is not None
        assert sw1.interface_name(0) == "GE0/0/1"
        assert sw1.interface_name(1) == "GE0/0/2"
        assert sw1.interface_name(23) == "GE0/0/24"
        assert sw1.interface_name(24) == ""
        assert sw1.to_dict()["interface_index_offset"] == 1

    def test_connection_count(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        assert len(topo.connections) == 5

    def test_connection_interface_names(self) -> None:
        """srcIndex/tarIndex 应被还原为真实接口名（GE0/0/0 等）。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        # AR1 srcIndex=0 ↔ AR2 srcIndex=0 → GE0/0/0 <-> GE0/0/0
        c01 = next(c for c in topo.connections if {c.src_device, c.dst_device} == {"AR1", "AR2"})
        assert c01.src_interface == "GE0/0/0"
        assert c01.dst_interface == "GE0/0/0"
        # AR2 srcIndex=1 ↔ PC1 srcIndex=0
        c_pc = next(c for c in topo.connections if "PC1" in (c.src_device, c.dst_device))
        if c_pc.src_device == "AR2":
            assert c_pc.src_interface == "GE0/0/1"
            assert c_pc.dst_interface == "Ethernet0/0/0"
        else:
            assert c_pc.src_interface == "Ethernet0/0/0"
            assert c_pc.dst_interface == "GE0/0/1"

    def test_link_type(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        assert all(c.link_type == "Copper" for c in topo.connections)

    def test_render_summary(self) -> None:
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        s = topo.render_summary()
        assert "AR1" in s
        assert "PC1" in s
        assert "GE0/0/0" in s


class TestParseLegacyFormat:
    """旧 eNSP 格式（<device>/<connection>）兼容测试。"""

    LEGACY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<topo version="1.0">
  <device id="dev-1" name="R1" model="AR2220" com_port="2000" cx="100" cy="100"/>
  <device id="dev-2" name="R2" model="AR2220" com_port="2001" cx="400" cy="100"/>
  <connection srcDeviceID="dev-1" destDeviceID="dev-2">
    <InterfacePair lineName="GE0/0/0" srcIndex="0" tarIndex="0"/>
  </connection>
</topo>
"""

    def test_legacy_device(self) -> None:
        topo = parse_topo_bytes(self.LEGACY_XML.encode("utf-8"), name="legacy")
        assert len(topo.devices) == 2
        r1 = topo.device_by_name("R1")
        assert r1 is not None
        assert r1.console_port == 2000
        assert r1.device_type == "router"

    def test_legacy_connection(self) -> None:
        topo = parse_topo_bytes(self.LEGACY_XML.encode("utf-8"), name="legacy")
        assert len(topo.connections) == 1
        c = topo.connections[0]
        assert c.src_device == "R1"
        assert c.dst_device == "R2"
        assert c.src_interface == "GE0/0/0"
        assert c.dst_interface == "GE0/0/0"


class TestParseFromFile:
    """用磁盘上的真实 .topo 文件做端到端测试（如果存在）。"""

    REAL_FILE = Path(
        r"D:/01_Workspace/code/ictstu-gitbook/06网络实验/"
        r"IPv4编址及IPv4路由基础实验/IPv4编址及IPv4路由基础实验.topo"
    )

    def test_real_file_exists(self) -> None:
        if not self.REAL_FILE.exists():
            pytest.skip(f"真实拓扑文件不存在: {self.REAL_FILE}")
        topo = parse_topo(str(self.REAL_FILE))
        assert topo.name == "IPv4编址及IPv4路由基础实验"
        assert len(topo.devices) == 5
        assert len(topo.connections) == 5
        # console port 范围 2000-2003
        ports = sorted(d.console_port for d in topo.devices if d.console_port > 1)
        assert ports == [2000, 2001, 2002, 2003]


class TestParseZip:
    def test_zip_basic(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("topo.xml", REAL_TOPO_XML)
            zf.writestr("device/AR1.cfg", "sysname AR1\n")
        topo = parse_topo_bytes(buf.getvalue(), name="lab-zip")
        assert topo.source_format == "zip"
        assert len(topo.devices) == 5
        assert len(topo.configs) == 1
        assert topo.configs[0].device_name == "AR1"

    def test_zip_without_topo_xml(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("device/SW1.cfg", "sysname SW1\n")
        topo = parse_topo_bytes(buf.getvalue(), name="x")
        assert topo.source_format == "zip"
        assert topo.devices == []
        assert len(topo.configs) == 1

    def test_invalid_zip(self) -> None:
        with pytest.raises(ValueError):
            parse_topo_bytes(b"PK\x03\x04 not a real zip", name="x")


class TestEnspGuiRenderable:
    """eNSP GUI 可渲染性自动检测。"""

    def test_real_sample_is_not_renderable(self) -> None:
        """REAL_TOPO_XML 用的是 D1/D2 简写 id，缺少必需字段，
        应被自动识别为「不可渲染」并报告问题列表。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        assert topo.ensp_gui_renderable is False
        assert topo.root_tag == "topo"  # 根元素本身是 <topo>，所以根元素不是问题
        # D1/D2 不是 UUID
        assert any("不是 UUID" in i for i in topo.ensp_gui_render_issues)
        # 缺必需字段
        assert any("poe" in i for i in topo.ensp_gui_render_issues)

    def test_simplified_root_not_renderable(self) -> None:
        """项目旧约定（<topology> 根元素 + D1 简写）应被识别为不可渲染。"""
        simplified = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<topology name="demo" version="1.0">\n'
            b"  <devices>\n"
            b'    <dev id="D1" name="R1" model="AR2240" com_port="2000" '
            b'cx="300" cy="200">\n'
            b'      <slot number="slot17" isMainBoard="1">\n'
            b'        <interface sztype="Ethernet" interfacename="GE" count="2" />\n'
            b"      </slot>\n"
            b"    </dev>\n"
            b"  </devices>\n"
            b"</topology>\n"
        )
        topo = parse_topo_bytes(simplified, name="simplified")
        assert topo.ensp_gui_renderable is False
        # 根元素问题
        assert any("根元素是 <topology>" in i for i in topo.ensp_gui_render_issues)

    def test_render_summary_warns(self) -> None:
        """render_summary() 在不可渲染时应包含明确警告与建议。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        summary = topo.render_summary()
        assert "eNSP GUI 可渲染: False" in summary
        assert "不会被 eNSP GUI 渲染" in summary
        assert "NativeTopoBuilder" in summary
        assert "docs/topo-drawing-rules.md" in summary

    def test_native_format_renderable(self) -> None:
        """完整原生格式（UUID + 全部必需属性）应被识别为可渲染。"""
        uuid1 = "3A360F02-D2C3-4b08-A7C4-2F4121737ABF"
        uuid2 = "68FA160B-0F50-4ca8-92E6-D132EBE49843"
        native = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<topo version="1.3.00.100">\n'
            "  <devices>\n"
            f'    <dev id="{uuid1}" name="AR1" poe="0" model="AR2240" '
            'settings="" system_mac="00-E0-FC-00-00-01" com_port="2000" '
            'bootmode="0" cx="400.000000" cy="100.000000" edit_left="427" edit_top="154">\n'
            '      <slot number="slot17" isMainBoard="1">\n'
            '        <interface sztype="Ethernet" interfacename="GE" count="2" />\n'
            '        <interface sztype="Ethernet" interfacename="GE" count="2" />\n'
            "      </slot>\n"
            "    </dev>\n"
            f'    <dev id="{uuid2}" name="AR2" poe="0" model="AR2240" '
            'settings="" system_mac="00-E0-FC-00-00-02" com_port="2001" '
            'bootmode="0" cx="600.000000" cy="200.000000" edit_left="627" edit_top="254">\n'
            '      <slot number="slot17" isMainBoard="1">\n'
            '        <interface sztype="Ethernet" interfacename="GE" count="2" />\n'
            "      </slot>\n"
            "    </dev>\n"
            "  </devices>\n"
            "  <lines>\n"
            f'    <line srcDeviceID="{uuid1}" destDeviceID="{uuid2}">\n'
            '      <interfacePair lineName="Copper" srcIndex="0" '
            'srcBoundRectIsMoved="1" srcBoundRect_X="450.000000" '
            'srcBoundRect_Y="150.000000" srcOffset_X="50.000000" '
            'srcOffset_Y="50.000000" tarIndex="0" tarBoundRectIsMoved="1" '
            'tarBoundRect_X="550.000000" tarBoundRect_Y="150.000000" '
            'tarOffset_X="-50.000000" tarOffset_Y="-50.000000" />\n'
            "    </line>\n"
            "  </lines>\n"
            "  <shapes />\n"
            "  <txttips />\n"
            "</topo>\n"
        ).encode()
        topo = parse_topo_bytes(native, name="native")
        assert topo.ensp_gui_renderable is True, topo.ensp_gui_render_issues
        assert topo.ensp_gui_render_issues == []

    def test_s5700_offset_is_exposed_in_mcp_payload(self) -> None:
        """S5700 的偏移量必须在 MCP JSON 中显式可见，便于调用方排错。"""
        native = b'''<topo version="1.3.00.100"><devices>
          <dev id="A" name="SW1" model="S5700" poe="0" settings="" system_mac="00-E0-FC-00-00-01" com_port="2000" bootmode="0" cx="0" cy="0" edit_left="27" edit_top="54">
            <slot number="slot17" isMainBoard="1"><interface sztype="Ethernet" interfacename="GE" count="2" /></slot>
          </dev></devices><lines /><shapes /><txttips /></topo>'''
        topo = parse_topo_bytes(native, name="s5700")
        sw1 = topo.device_by_name("SW1")
        assert sw1 is not None
        assert sw1.to_dict()["interface_index_offset"] == 1

    def test_to_dict_includes_renderability(self) -> None:
        """to_dict() 应包含 root_tag / ensp_gui_renderable / issues 字段。"""
        topo = parse_topo_bytes(REAL_TOPO_XML.encode("utf-8"), name="real")
        d = topo.to_dict()
        assert "root_tag" in d
        assert "ensp_gui_renderable" in d
        assert "ensp_gui_render_issues" in d
        assert d["ensp_gui_renderable"] is False


class TestGuessDeviceType:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("AR2220", "router"),
            ("AR201", "router"),
            ("NE40E", "router"),
            ("S5700", "switch"),
            ("S3700-SI", "switch"),
            ("USG5500", "firewall"),
            ("NGFW", "firewall"),
            ("AC6605", "wireless_controller"),
            ("AP6050", "ap"),
            ("CLOUD", "cloud"),
            ("PC", "pc"),
        ],
    )
    def test_known_models(self, model: str, expected: str) -> None:
        assert _guess_device_type(model) == expected

    def test_unknown(self) -> None:
        result = _guess_device_type("X12345")
        assert result in {"router", "switch", "firewall", "unknown"}


class TestTopologyModel:
    def test_device_to_dict(self) -> None:
        d = Device(
            id="x",
            name="R1",
            model="AR2220",
            console_port=2000,
            device_type="router",
            interfaces=["GE0/0/0", "GE0/0/1"],
        )
        out = d.to_dict()
        assert out["id"] == "x"
        assert out["name"] == "R1"
        assert out["console_port"] == 2000
        assert out["interfaces"] == ["GE0/0/0", "GE0/0/1"]

    def test_connection_to_dict(self) -> None:
        c = Connection(
            src_device="R1",
            dst_device="R2",
            src_interface="GE0/0/0",
            dst_interface="GE0/0/0",
            link_type="Copper",
        )
        out = c.to_dict()
        assert out["src_device"] == "R1"
        assert out["link_type"] == "Copper"
