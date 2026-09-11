"""诊断引擎单元测试。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from grbj_ensp_mcp.diagnostics import (
    DiagnosticEngine,
    DiagnosticSeverity,
    get_diagnostic_engine,
)


class TestDiagnosticEngine:
    """测试诊断引擎的基本功能。"""

    def test_engine_singleton(self) -> None:
        """单例模式：两次获取返回同一实例。"""
        e1 = get_diagnostic_engine()
        e2 = get_diagnostic_engine()
        assert e1 is e2

    def test_diagnose_unrecognized_command(self) -> None:
        """诊断 Unrecognized command 错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Error: Unrecognized command found at '^' position.",
            "interface GE0/0/0",
        )
        assert result is not None
        assert result.category == "unrecognized_command"
        assert result.severity == DiagnosticSeverity.ERROR
        assert len(result.root_causes) > 0
        assert len(result.fix_suggestions) > 0
        assert "接口名" in " ".join(result.root_causes)
        assert result.command == "interface GE0/0/0"

    def test_diagnose_incomplete_command(self) -> None:
        """诊断 Incomplete command 错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Error: Incomplete command found at '^' position.",
            "ip address",
        )
        assert result is not None
        assert result.category == "incomplete_command"
        assert result.severity == DiagnosticSeverity.ERROR
        assert any("缺少" in rc for rc in result.root_causes)

    def test_diagnose_wrong_parameter(self) -> None:
        """诊断 Wrong parameter 错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Error: Wrong parameter found at '^' position.",
            "ip address 10.0.0.1",
        )
        assert result is not None
        assert result.category == "wrong_parameter"
        assert result.severity == DiagnosticSeverity.ERROR

    def test_diagnose_ambiguous_command(self) -> None:
        """诊断 Ambiguous command 错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Error: Ambiguous command found at '^' position.",
            "di",
        )
        assert result is not None
        assert result.category == "ambiguous_command"
        assert result.severity == DiagnosticSeverity.ERROR

    def test_diagnose_connection_refused(self) -> None:
        """诊断连接被拒绝错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Connection refused: 无法连接到 127.0.0.1:2000",
            "",
        )
        assert result is not None
        assert result.category == "connection_refused"
        assert result.severity == DiagnosticSeverity.CRITICAL

    def test_diagnose_connection_closed(self) -> None:
        """诊断连接关闭错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "对端关闭了连接 127.0.0.1:2000",
            "",
        )
        assert result is not None
        assert result.category == "connection_closed"
        assert result.severity == DiagnosticSeverity.CRITICAL

    def test_diagnose_ping_loss(self) -> None:
        """诊断 ping 丢包错误。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "5 packets transmitted, 0 received, 100% packet loss",
            "ping 10.0.0.2",
        )
        assert result is not None
        assert result.category == "ping_loss"
        assert result.severity == DiagnosticSeverity.WARNING

    def test_diagnose_unknown_error(self) -> None:
        """诊断未知错误（应返回 low confidence）。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Something completely unexpected happened",
            "magic command",
        )
        assert result is not None
        assert result.category == "unknown"
        assert result.confidence == "low"

    def test_diagnose_case_insensitive(self) -> None:
        """诊断应不区分大小写。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "error: unrecognized command found at '^' position.",
            "interface GE0/0/0",
        )
        assert result is not None
        assert result.category == "unrecognized_command"

    def test_diagnose_with_device_context(self) -> None:
        """诊断应能携带设备上下文。"""
        engine = DiagnosticEngine()
        result = engine.diagnose(
            "Error: Unrecognized command",
            "interface GE0/0/0",
            device_context={"device_type": "AR2220", "hostname": "AR1"},
        )
        assert result is not None
        assert result.device_context["device_type"] == "AR2220"
        assert result.device_context["hostname"] == "AR1"

    def test_result_to_dict(self) -> None:
        """DiagnosticResult.to_dict() 应返回可序列化结构。"""
        engine = DiagnosticEngine()
        result = engine.diagnose("Error: Unrecognized command", "test")
        assert result is not None
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["category"] == "unrecognized_command"
        assert d["severity"] == "error"
        assert isinstance(d["root_causes"], list)
        assert isinstance(d["fix_suggestions"], list)


class TestDiagnosticEngineBatch:
    """测试批量诊断功能。"""

    def test_batch_all_success(self) -> None:
        """全部成功时不应有诊断。"""
        engine = DiagnosticEngine()
        batch = engine.diagnose_batch(
            [
                {"command": "system-view", "errored": False, "errors": []},
                {"command": "interface GigabitEthernet0/0/0", "errored": False, "errors": []},
            ],
            session_name="AR1",
        )
        assert batch.failed_commands == 0
        assert len(batch.diagnostics) == 0
        assert "成功" in batch.summary

    def test_batch_with_errors(self) -> None:
        """有错误时应返回诊断。"""
        engine = DiagnosticEngine()
        batch = engine.diagnose_batch(
            [
                {"command": "system-view", "errored": False, "errors": []},
                {
                    "command": "interface GE0/0/0",
                    "errored": True,
                    "errors": ["Error: Unrecognized command found at '^' position."],
                },
            ],
            session_name="AR1",
        )
        assert batch.failed_commands == 1
        assert len(batch.diagnostics) == 1
        assert batch.diagnostics[0].category == "unrecognized_command"

    def test_batch_to_dict(self) -> None:
        """BatchDiagnosticResult.to_dict() 应可序列化。"""
        engine = DiagnosticEngine()
        batch = engine.diagnose_batch(
            [
                {"command": "system-view", "errored": False, "errors": []},
            ],
            session_name="AR1",
        )
        d = batch.to_dict()
        assert isinstance(d, dict)
        assert d["session_name"] == "AR1"
        assert d["total_commands"] == 1
        assert d["failed_commands"] == 0


class TestInterfaceAnalysis:
    """测试接口状态分析。"""

    def test_interface_up(self) -> None:
        """正常 up 接口不应有 issue。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_interface_state(
            [
                {
                    "interface": "GigabitEthernet0/0/0",
                    "ip": "10.0.0.1/24",
                    "physical": "up",
                    "protocol": "up",
                },
            ],
            expected_up=["GigabitEthernet0/0/0"],
        )
        assert len(issues) == 0

    def test_interface_physical_down(self) -> None:
        """物理 down 应被检测。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_interface_state(
            [
                {
                    "interface": "GigabitEthernet0/0/0",
                    "ip": "10.0.0.1/24",
                    "physical": "down",
                    "protocol": "down",
                },
            ],
            expected_up=["GigabitEthernet0/0/0"],
        )
        assert len(issues) >= 1
        assert any("物理状态 down" in i.get("issue", "") for i in issues)

    def test_interface_protocol_down(self) -> None:
        """协议 down 应被检测。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_interface_state(
            [
                {
                    "interface": "GigabitEthernet0/0/0",
                    "ip": "10.0.0.1/24",
                    "physical": "up",
                    "protocol": "down",
                },
            ],
            expected_up=["GigabitEthernet0/0/0"],
        )
        assert len(issues) >= 1
        assert any("协议状态 down" in i.get("issue", "") for i in issues)

    def test_interface_not_exist(self) -> None:
        """不存在的接口应被检测。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_interface_state(
            [],
            expected_up=["GigabitEthernet0/0/0"],
        )
        assert len(issues) >= 1
        assert any("不存在" in i.get("issue", "") for i in issues)


class TestOspfAnalysis:
    """测试 OSPF 邻居分析。"""

    def test_all_full(self) -> None:
        """全部 Full 时不应有 issue。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_ospf_peers(
            [
                {"neighbor_id": "10.0.0.2", "state": "Full"},
                {"neighbor_id": "10.0.0.3", "state": "Full"},
            ],
            expected_neighbors=2,
        )
        assert len(issues) == 0

    def test_neighbor_not_full(self) -> None:
        """非 Full 邻居应被检测。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_ospf_peers(
            [
                {"neighbor_id": "10.0.0.2", "state": "Init"},
            ],
            expected_neighbors=1,
        )
        assert len(issues) >= 1
        assert any("Init" in i.get("state", "") for i in issues)


class TestRoutingAnalysis:
    """测试路由表分析。"""

    def test_route_exists(self) -> None:
        """路由存在时不应有 issue。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_routing_table(
            [
                {"destination": "10.0.0.0", "mask": "24", "protocol": "Direct"},
                {"destination": "192.168.1.0", "mask": "24", "protocol": "OSPF"},
            ],
            expected_destinations=["10.0.0.0/24"],
        )
        # 10.0.0.0 with mask 24 covers 10.0.0.0/24, so no issue
        assert len(issues) == 0

    def test_route_missing(self) -> None:
        """路由缺失应被检测。"""
        engine = DiagnosticEngine()
        issues = engine.analyze_routing_table(
            [],
            expected_destinations=["10.0.0.0/24"],
        )
        assert len(issues) >= 1
        assert any("缺少" in i.get("issue", "") for i in issues)


class TestSubnetContains:
    """测试子网包含判断。"""

    def test_exact_match(self) -> None:
        from grbj_ensp_mcp.diagnostics import _subnet_contains

        assert _subnet_contains("10.0.0.0", "255.255.255.0", "10.0.0.0/24")

    def test_within_subnet(self) -> None:
        from grbj_ensp_mcp.diagnostics import _subnet_contains

        assert _subnet_contains("10.0.0.0", "255.0.0.0", "10.1.0.0/16")

    def test_outside_subnet(self) -> None:
        from grbj_ensp_mcp.diagnostics import _subnet_contains

        assert not _subnet_contains("10.0.0.0", "255.255.255.0", "192.168.1.0/24")

    def test_cidr_mask(self) -> None:
        from grbj_ensp_mcp.diagnostics import _subnet_contains

        assert _subnet_contains("10.0.0.0", "24", "10.0.0.0/24")
        assert _subnet_contains("10.0.0.0", "16", "10.0.1.0/24")
