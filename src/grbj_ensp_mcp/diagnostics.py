"""eNSP 智能诊断引擎。

提供命令错误的根因分析、上下文感知诊断与自动修复策略。
双层诊断架构：
- 规则层：基于 VRP 已知错误模式做快速匹配（高置信度时直接返回）
- LLM 层：生成结构化上下文供 LLM 深度推理（低置信度或复杂场景时启用）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiagnosticSeverity(str, Enum):
    """诊断严重级别。"""

    CRITICAL = "critical"  # 连接断开 / 设备不可达
    ERROR = "error"  # 命令本身错误
    WARNING = "warning"  # 配置可能不完整但命令没报错
    INFO = "info"  # 建议性提示


# ---------------------------------------------------------------------- #
# 错误模式知识库
# ---------------------------------------------------------------------- #
# 每个条目包含：
#   patterns: 正则列表（匹配设备回显）
#   root_causes: 常见根因列表
#   probe_commands: 建议先执行的探查命令
#   fix_suggestions: 修复建议
#   severity: 严重级别
#   category: 错误分类标签
# ---------------------------------------------------------------------- #
_ERROR_KNOWLEDGE: list[dict[str, Any]] = [
    # -- 命令层错误 ------------------------------------------------------ #
    {
        "category": "unrecognized_command",
        "patterns": [
            r"Error:\s*Unrecognized command",
        ],
        "root_causes": [
            "命令在当前 VRP 版本中不存在或拼写错误",
            "接口名使用了缩写（如 GE0/0/0），VRP 要求全称 GigabitEthernet0/0/0",
            "当前视图不支持该命令（如用户视图下执行 system-view 级命令）",
            "前置特性未使能（如 dhcp enable 未执行就配 DHCP 相关命令）",
            "命令关键字大小写错误（LoopBack0 而非 Loopback0）",
        ],
        "probe_commands": [
            "ensp_get_device_info — 确认 VRP 版本与设备型号",
            "ensp_get_running_config — 查看同类命令在已有配置中的写法",
            "在出错视图下输入 ? 查看可用命令列表",
        ],
        "fix_suggestions": [
            "将接口名改为全称（如 GigabitEthernet0/0/0）",
            "确认关键字大小写（如 LoopBack0 而非 Loopback0）",
            "检查是否在正确的视图层级（system-view / 接口视图 / VLAN 视图等）",
            "确保前置特性已使能（如 dhcp enable / stp enable 等）",
            "用 display version 确认 VRP 版本，对照版本文档",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "incomplete_command",
        "patterns": [
            r"Error:\s*Incomplete command",
        ],
        "root_causes": [
            "命令缺少必需的参数",
            "关键字后缺少参数值",
        ],
        "probe_commands": [
            "在该视图下输入命令关键字后按 ? 查看所需参数",
        ],
        "fix_suggestions": [
            "补全命令的必需参数",
            "在命令关键字后输入 ? 查看参数列表和格式要求",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "wrong_parameter",
        "patterns": [
            r"Error:\s*Wrong parameter",
        ],
        "root_causes": [
            "参数值格式不正确（如 IP 地址缺少掩码）",
            "参数类型不匹配（需要数字却给了字符串）",
            "参数值超出合法范围",
        ],
        "probe_commands": [
            "在命令后输入 ? 查看参数格式要求",
        ],
        "fix_suggestions": [
            "检查参数格式和取值范围",
            "确认 IP 地址、掩码、端口号等格式正确",
            "参考 display current-configuration 中同类配置的写法",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "too_many_parameters",
        "patterns": [
            r"Error:\s*Too many parameters",
        ],
        "root_causes": [
            "命令参数数量超出预期",
        ],
        "probe_commands": [],
        "fix_suggestions": [
            "减少参数数量，使用 ? 查看命令允许的参数个数",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "ambiguous_command",
        "patterns": [
            r"Error:\s*Ambiguous command",
        ],
        "root_causes": [
            "命令缩写存在歧义，匹配到多个候选命令",
        ],
        "probe_commands": [],
        "fix_suggestions": [
            "使用完整的命令关键字，避免缩写",
            "输入命令前缀后按 ? 查看所有匹配的命令",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    # -- 连接/会话层错误 -------------------------------------------------- #
    {
        "category": "connection_refused",
        "patterns": [
            r"Connection refused",
            r"无法连接到",
            r"连接失败",
        ],
        "root_causes": [
            "设备 TCP 端口未监听（eNSP 设备未启动）",
            "端口号错误（实际端口与预期不符）",
        ],
        "probe_commands": [
            "ensp_scan_devices — 扫描实际开放的端口",
            "在 eNSP GUI 中确认设备已启动（指示灯变绿）",
        ],
        "fix_suggestions": [
            "调用 ensp_scan_devices 确认实际端口",
            "在 eNSP GUI 中启动设备",
            "重新调用 ensp_load_topology 或 ensp_connect_device 建连",
        ],
        "severity": DiagnosticSeverity.CRITICAL,
    },
    {
        "category": "connection_closed",
        "patterns": [
            r"对端关闭了连接",
            r"Connection closed",
            r"connection lost",
        ],
        "root_causes": [
            "设备 console 5 分钟无操作自动登出",
            "eNSP 设备被手动关闭/重启",
            "网络异常导致 TCP 连接断开",
        ],
        "probe_commands": [
            "ensp_list_sessions — 查看会话状态",
            "ensp_scan_devices — 确认端口是否仍可达",
        ],
        "fix_suggestions": [
            "调用 ensp_disconnect_device 关闭旧会话",
            "重新调用 ensp_connect_device 建立新会话",
            "若持续断开，检查 eNSP 设备运行状态",
        ],
        "severity": DiagnosticSeverity.CRITICAL,
    },
    {
        "category": "console_timeout",
        "patterns": [
            r"timed out",
            r"timeout",
            r"超时",
        ],
        "root_causes": [
            "命令执行时间过长（如 ping 大量包、tracert 等）",
            "设备响应缓慢（高负载或启动中）",
            "网络延迟导致读取超时",
        ],
        "probe_commands": [
            "ensp_list_sessions — 查看会话状态",
        ],
        "fix_suggestions": [
            "增加命令超时时间（timeout 参数）",
            "等待设备就绪后重试",
            "对于长命令（ping/save 等），使用更大的 timeout 值",
        ],
        "severity": DiagnosticSeverity.WARNING,
    },
    # -- 配置逻辑错误 ---------------------------------------------------- #
    {
        "category": "interface_not_exist",
        "patterns": [
            r"Error:\s*Wrong parameter.*interface",
            r"interface.*not exist",
            r"interface.*does not exist",
        ],
        "root_causes": [
            "接口名与设备实际接口不匹配",
            "接口名使用了缩写形式",
            "槽位/端口号超出设备实际范围",
        ],
        "probe_commands": [
            "display ip interface brief — 列出所有接口",
            "ensp_verify_interfaces — 查看接口清单",
        ],
        "fix_suggestions": [
            "用 display ip interface brief 确认设备实际接口名",
            "使用全称接口名（GigabitEthernet0/0/0）",
            "检查槽位号和端口号是否在设备范围内",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "ip_address_conflict",
        "patterns": [
            r"Error:.*IP address.*conflict",
            r"Error:.*address.*already",
            r"Error:.*overlap",
        ],
        "root_causes": [
            "IP 地址与已有接口地址冲突",
            "网段与已有路由/接口重叠",
        ],
        "probe_commands": [
            "display ip interface brief — 查看已有 IP",
            "display ip routing-table — 查看路由表",
        ],
        "fix_suggestions": [
            "更换不冲突的 IP 地址",
            "先删除已有冲突配置再重新配置",
            "检查 IP 规划是否与现有配置冲突",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "ospf_config_issue",
        "patterns": [
            r"OSPF.*not enabled",
            r"ospf.*process.*not exist",
        ],
        "root_causes": [
            "OSPF 进程未创建",
            "OSPF 未在接口上启用",
            "OSPF 区域配置错误",
        ],
        "probe_commands": [
            "display ospf brief — 查看 OSPF 进程状态",
            "display current-configuration | include ospf",
        ],
        "fix_suggestions": [
            "先创建 OSPF 进程（ospf <process-id>）",
            "在接口视图下启用 OSPF（ospf enable <process-id> area <area>）",
            "确认 OSPF 区域号与对端一致",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "vlan_not_exist",
        "patterns": [
            r"Error:.*VLAN.*not exist",
            r"Error:.*vlan.*does not exist",
        ],
        "root_causes": [
            "VLAN 未创建就尝试配置",
            "VLAN ID 超出范围",
        ],
        "probe_commands": [
            "display vlan — 查看已有 VLAN",
        ],
        "fix_suggestions": [
            "先创建 VLAN（vlan <vlan-id>）",
            "确认 VLAN ID 在 1-4094 范围内",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    # -- 校验/状态异常 --------------------------------------------------- #
    {
        "category": "ospf_neighbor_down",
        "patterns": [
            r"ospf.*neighbor.*down",
            r"OSPF.*neighbor.*Init",
            r"OSPF.*neighbor.*Exchange",
        ],
        "root_causes": [
            "OSPF 区域号不匹配",
            "网络类型不匹配（broadcast vs p2p）",
            "Hello/Dead 间隔不一致",
            "认证配置不匹配",
            "接口 MTU 不一致",
            "对端接口未配置 IP 或 protocol down",
        ],
        "probe_commands": [
            "display ospf peer brief — 查看邻居状态",
            "display ospf error — 查看 OSPF 错误统计",
            "ensp_verify_interfaces — 检查接口状态",
        ],
        "fix_suggestions": [
            "确认两端 OSPF 区域号一致",
            "检查接口 IP 地址在同一网段",
            "确认接口物理状态为 up",
            "检查 OSPF 网络类型配置",
            "若接口 protocol down，检查对端配置",
        ],
        "severity": DiagnosticSeverity.WARNING,
    },
    {
        "category": "ping_loss",
        "patterns": [
            r"(\d+)% packet loss",
        ],
        "root_causes": [
            "路由不可达（缺少路由条目）",
            "中间设备 ACL 过滤",
            "接口 protocol down",
            "VLAN 未放行",
            "ARP 解析失败",
        ],
        "probe_commands": [
            "display ip routing-table — 检查路由表",
            "tracert <target> — 跟踪路径",
            "ensp_verify_interfaces — 检查沿途接口状态",
        ],
        "fix_suggestions": [
            "用 tracert 定位丢包跳",
            "检查沿途路由表是否有目的网段",
            "检查中间设备是否配置了 ACL 过滤",
            "确认 VLAN 在 trunk 链路中已放行",
        ],
        "severity": DiagnosticSeverity.WARNING,
    },
    # -- 视图/上下文错误 -------------------------------------------------- #
    {
        "category": "wrong_view",
        "patterns": [
            r"Error:.*command.*not.*in.*view",
            r"Error:.*please.*quit.*first",
        ],
        "root_causes": [
            "在错误视图下执行命令（如 system-view 下执行用户视图命令）",
            "需要先退出当前视图",
        ],
        "probe_commands": [
            "ensp_send_command 查看当前提示符",
        ],
        "fix_suggestions": [
            "用 quit 退到正确视图",
            "用 return 从接口视图退回 system-view",
            "确认当前提示符类型（<...> 用户视图 / [...] system-view）",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
    {
        "category": "system_view_required",
        "patterns": [
            r"Error:.*system.view.*required",
        ],
        "root_causes": [
            "需要在 system-view 下执行的命令在用户视图下执行",
        ],
        "probe_commands": [],
        "fix_suggestions": [
            "先执行 system-view 进入系统视图",
            "调用 ensp_enter_system_view 进入系统视图",
        ],
        "severity": DiagnosticSeverity.ERROR,
    },
]


# ---------------------------------------------------------------------- #
# 诊断结果数据类
# ---------------------------------------------------------------------- #
@dataclass
class DiagnosticResult:
    """单次诊断的结果。"""

    category: str  # 错误分类标签
    severity: DiagnosticSeverity  # 严重级别
    matched_pattern: str  # 匹配到的错误模式
    root_causes: list[str] = field(default_factory=list)  # 可能的根因
    probe_commands: list[str] = field(default_factory=list)  # 建议探查命令
    fix_suggestions: list[str] = field(default_factory=list)  # 修复建议
    raw_error: str = ""  # 原始错误文本
    command: str = ""  # 触发错误的命令
    device_context: dict[str, Any] = field(default_factory=dict)  # 设备上下文
    confidence: str = "medium"  # 诊断置信度：high / medium / low

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity.value,
            "matched_pattern": self.matched_pattern,
            "root_causes": self.root_causes,
            "probe_commands": self.probe_commands,
            "fix_suggestions": self.fix_suggestions,
            "raw_error": self.raw_error,
            "command": self.command,
            "device_context": self.device_context,
            "confidence": self.confidence,
        }


@dataclass
class BatchDiagnosticResult:
    """批量诊断结果。"""

    session_name: str
    total_commands: int
    failed_commands: int
    diagnostics: list[DiagnosticResult] = field(default_factory=list)
    summary: str = ""  # 人类可读的总结

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "total_commands": self.total_commands,
            "failed_commands": self.failed_commands,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------- #
# 诊断引擎
# ---------------------------------------------------------------------- #
class DiagnosticEngine:
    """基于规则的 eNSP 命令错误诊断引擎。

    使用方式::

        engine = DiagnosticEngine()
        result = engine.diagnose("Error: Unrecognized command", "interface GE0/0/0")
        print(result.root_causes)
        print(result.fix_suggestions)
    """

    def __init__(self) -> None:
        # 预编译所有错误模式的正则
        self._compiled: list[tuple[dict[str, Any], re.Pattern[str]]] = []
        for entry in _ERROR_KNOWLEDGE:
            for pat in entry["patterns"]:
                self._compiled.append((entry, re.compile(pat, re.IGNORECASE)))

    def diagnose(
        self,
        error_text: str,
        command: str = "",
        device_context: dict[str, Any] | None = None,
    ) -> DiagnosticResult | None:
        """对单条错误信息进行诊断。

        参数:
            error_text: 设备回显中的错误文本
            command: 触发错误的命令
            device_context: 设备上下文（型号、VRP 版本、当前视图等）

        返回:
            DiagnosticResult 或 None（未匹配任何已知模式）
        """
        for entry, pattern in self._compiled:
            match = pattern.search(error_text)
            if match:
                # 确定置信度
                confidence = "high"
                # 如果一条错误文本匹配到多个模式，降低置信度
                matching_count = sum(1 for _, p in self._compiled if p.search(error_text))
                if matching_count > 1:
                    confidence = "medium"

                return DiagnosticResult(
                    category=str(entry["category"]),
                    severity=DiagnosticSeverity(entry["severity"]),
                    matched_pattern=pattern.pattern,
                    root_causes=list(entry["root_causes"]),
                    probe_commands=list(entry.get("probe_commands", [])),
                    fix_suggestions=list(entry.get("fix_suggestions", [])),
                    raw_error=error_text.strip(),
                    command=command,
                    device_context=device_context or {},
                    confidence=confidence,
                )

        # 未匹配任何已知模式
        return DiagnosticResult(
            category="unknown",
            severity=DiagnosticSeverity.ERROR,
            matched_pattern="",
            root_causes=["未匹配到已知错误模式，建议人工分析"],
            probe_commands=[
                "ensp_get_device_info — 确认设备型号与版本",
                "ensp_get_running_config — 查看当前配置",
            ],
            fix_suggestions=[
                "检查命令拼写是否正确",
                "确认当前视图是否支持该命令",
                "查看设备手册确认命令语法",
            ],
            raw_error=error_text.strip(),
            command=command,
            device_context=device_context or {},
            confidence="low",
        )

    def diagnose_batch(
        self,
        command_results: list[dict[str, Any]],
        session_name: str = "",
        device_context: dict[str, Any] | None = None,
    ) -> BatchDiagnosticResult:
        """对批量命令结果进行诊断。

        参数:
            command_results: ensp_send_commands 返回的 results 列表
            session_name: 会话名称
            device_context: 设备上下文

        返回:
            BatchDiagnosticResult
        """
        diagnostics: list[DiagnosticResult] = []
        failed_count = 0

        for r in command_results:
            if not r.get("errored"):
                continue
            failed_count += 1
            errors = r.get("errors", [])
            error_text = "\n".join(errors) if isinstance(errors, list) else str(errors)

            diag = self.diagnose(
                error_text=error_text,
                command=r.get("command", ""),
                device_context=device_context,
            )
            if diag:
                diagnostics.append(diag)

        # 如果有多条相同类型的错误，合并去重后的修复建议
        summary_parts: list[str] = []
        if failed_count == 0:
            summary_parts.append("所有命令执行成功，无需诊断。")
        else:
            categories = list({d.category for d in diagnostics})
            summary_parts.append(f"共 {len(command_results)} 条命令，{failed_count} 条失败。")
            summary_parts.append(f"错误类型: {', '.join(categories)}。")
            # 合并修复建议
            all_fixes: list[str] = []
            for d in diagnostics:
                for fix in d.fix_suggestions:
                    if fix not in all_fixes:
                        all_fixes.append(fix)
            if all_fixes:
                summary_parts.append("建议修复步骤:\n  - " + "\n  - ".join(all_fixes))

        return BatchDiagnosticResult(
            session_name=session_name,
            total_commands=len(command_results),
            failed_commands=failed_count,
            diagnostics=diagnostics,
            summary="\n".join(summary_parts),
        )

    def analyze_interface_state(
        self,
        interface_brief: list[dict[str, str]],
        expected_up: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """分析接口状态并诊断异常。

        参数:
            interface_brief: ensp_verify_interfaces 返回的 interfaces 列表
            expected_up: 期望 up 的接口名列表

        返回:
            诊断结果列表
        """
        issues: list[dict[str, Any]] = []
        iface_map = {i.get("interface", ""): i for i in interface_brief}

        if expected_up:
            for ifname in expected_up:
                iface = iface_map.get(ifname)
                if not iface:
                    issues.append(
                        {
                            "interface": ifname,
                            "issue": "接口不存在",
                            "severity": "error",
                            "suggestion": "用 display ip interface brief 确认实际接口名",
                        }
                    )
                    continue
                if iface.get("physical") != "up":
                    issues.append(
                        {
                            "interface": ifname,
                            "issue": "物理状态 down",
                            "severity": "critical",
                            "suggestion": "检查物理连线是否连接、对端设备是否启动",
                        }
                    )
                elif iface.get("protocol") != "up":
                    issues.append(
                        {
                            "interface": ifname,
                            "issue": "协议状态 down",
                            "severity": "warning",
                            "suggestion": (
                                "检查接口 IP 配置、对端 IP 是否在同一网段、链路层协议是否匹配"
                            ),
                        }
                    )

        # 检查所有接口中 down 的
        for iface in interface_brief:
            if iface.get("interface", "").startswith("NULL"):
                continue
            if (
                (iface.get("physical") != "up" or iface.get("protocol") != "up")
                and not any(i.get("interface") == iface.get("interface") for i in issues)
                and iface.get("ip")
            ):
                issues.append(
                    {
                        "interface": iface.get("interface"),
                        "issue": f"接口状态异常 (physical={iface.get('physical')}, protocol={iface.get('protocol')})",
                        "severity": "warning",
                        "suggestion": "检查该接口的对端配置与连线状态",
                    }
                )

        return issues

    def analyze_ospf_peers(
        self,
        peers: list[dict[str, str]],
        expected_neighbors: int | None = None,
    ) -> list[dict[str, Any]]:
        """分析 OSPF 邻居状态并诊断异常。

        参数:
            peers: ensp_verify_ospf 返回的 peers 列表
            expected_neighbors: 期望的邻居数量

        返回:
            诊断结果列表
        """
        issues: list[dict[str, Any]] = []

        full_count = sum(1 for p in peers if p.get("state") == "Full")
        if expected_neighbors is not None and full_count < expected_neighbors:
            issues.append(
                {
                    "issue": f"OSPF 邻居数量不足 (期望 {expected_neighbors}，实际 Full {full_count})",
                    "severity": "warning",
                    "suggestion": "检查未到达 Full 的邻居，确认 OSPF 区域、网络类型、认证配置一致",
                }
            )

        for peer in peers:
            state = peer.get("state", "")
            if state != "Full":
                issues.append(
                    {
                        "neighbor": peer.get("neighbor_id", "?"),
                        "state": state,
                        "issue": f"OSPF 邻居 {peer.get('neighbor_id', '?')} 状态为 {state}（非 Full）",
                        "severity": "warning",
                        "suggestion": _ospf_state_advice(state),
                    }
                )

        return issues

    def analyze_routing_table(
        self,
        routes: list[dict[str, str]],
        expected_destinations: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """分析路由表，检查期望的路由是否存在。

        参数:
            routes: ensp_verify_routes 返回的 routes 列表
            expected_destinations: 期望存在的目的网段列表（支持 "10.0.0.0/24" 或 "10.0.0.0" 格式）

        返回:
            诊断结果列表
        """
        issues: list[dict[str, Any]] = []

        if expected_destinations:
            for dest_spec in expected_destinations:
                # 解析 expected_destinations：支持 "10.0.0.0/24" 或 "10.0.0.0"
                dest_ip = dest_spec.rsplit("/", 1)[0] if "/" in dest_spec else dest_spec

                # 精确匹配：destination == dest_ip
                exact_match = any(r.get("destination", "") == dest_ip for r in routes)
                if exact_match:
                    continue

                # 子网包含匹配：检查是否有路由覆盖该网段
                found = False
                for r in routes:
                    r_dest = r.get("destination", "")
                    r_mask = r.get("mask", "")
                    if _subnet_contains(r_dest, r_mask, dest_spec):
                        found = True
                        break
                if found:
                    continue

                issues.append(
                    {
                        "destination": dest_spec,
                        "issue": f"路由表中缺少到 {dest_spec} 的路由",
                        "severity": "warning",
                        "suggestion": (
                            "检查 OSPF/静态路由配置是否正确宣告了该网段，"
                            "确认宣告接口的 protocol 状态为 up"
                        ),
                    }
                )

        return issues


# ---------------------------------------------------------------------- #
# LLM 诊断上下文
# ---------------------------------------------------------------------- #
@dataclass
class LLMDiagnosticContext:
    """供 LLM 深度推理的结构化诊断上下文。

    包含设备全貌、失败命令、相关配置片段、接口状态、错误详情，
    以及引导 LLM 逐步推理的 prompt。
    """

    # 设备基础信息
    device_name: str = ""
    device_type: str = ""
    software_version: str = ""
    # 失败命令与错误
    failed_command: str = ""
    error_text: str = ""
    # 设备状态快照
    current_view: str = ""  # "system-view" / "user-view"
    running_config_snippet: str = ""  # 相关配置片段
    interface_brief: str = ""  # display ip interface brief 输出
    recent_output: str = ""  # 最近几条命令的输出
    # 规则引擎预判（可选）
    rule_diagnosis: dict[str, Any] | None = None
    # LLM 推理引导 prompt
    reasoning_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_name": self.device_name,
            "device_type": self.device_type,
            "software_version": self.software_version,
            "failed_command": self.failed_command,
            "error_text": self.error_text,
            "current_view": self.current_view,
            "running_config_snippet": self.running_config_snippet,
            "interface_brief": self.interface_brief,
            "recent_output": self.recent_output,
            "rule_diagnosis": self.rule_diagnosis,
            "reasoning_prompt": self.reasoning_prompt,
        }


def generate_llm_context(
    *,
    device_name: str = "",
    device_type: str = "",
    software_version: str = "",
    failed_command: str = "",
    error_text: str = "",
    current_view: str = "",
    running_config_snippet: str = "",
    interface_brief: str = "",
    recent_output: str = "",
    rule_diagnosis: dict[str, Any] | None = None,
    experiment_description: str = "",
) -> LLMDiagnosticContext:
    """生成供 LLM 深度推理的结构化诊断上下文。

    此函数不执行任何诊断逻辑——它只负责收集和整理上下文，
    生成一个引导 LLM 逐步推理的 prompt。真正的推理由 LLM 完成。

    参数:
        device_name: 设备名（如 AR1）
        device_type: 设备型号（如 AR2220）
        software_version: VRP 版本
        failed_command: 触发错误的命令
        error_text: 设备回显的错误文本
        current_view: 当前视图（如 "system-view"）
        running_config_snippet: 运行配置的相关片段
        interface_brief: 接口状态摘要
        recent_output: 最近几条命令的输出
        rule_diagnosis: 规则引擎的预判结果（如有）
        experiment_description: 用户实验需求描述

    返回:
        LLMDiagnosticContext，其中的 reasoning_prompt 可直接用于 LLM 推理
    """
    context = LLMDiagnosticContext(
        device_name=device_name,
        device_type=device_type,
        software_version=software_version,
        failed_command=failed_command,
        error_text=error_text,
        current_view=current_view,
        running_config_snippet=running_config_snippet,
        interface_brief=interface_brief,
        recent_output=recent_output,
        rule_diagnosis=rule_diagnosis,
    )

    # 构建 LLM 推理引导 prompt
    parts: list[str] = []

    parts.append("## 诊断任务")
    parts.append("")
    parts.append(
        "你是一位华为网络设备故障排查专家。"
        "以下设备运行在 **eNSP（Enterprise Network Simulation Platform）仿真平台** 中，"
        "并非真实物理硬件。eNSP 通过软件模拟华为 VRP 操作系统，其行为与真实设备基本一致，"
        "但存在一些仿真环境特有的约束（详见下方）。"
    )
    parts.append("")
    parts.append("请分析该 eNSP 仿真设备的配置失败场景，找出根因并给出修复方案。")

    if experiment_description:
        parts.append("")
        parts.append(f"**用户实验需求**: {experiment_description}")

    parts.append("")
    parts.append("## 设备信息")
    parts.append("")
    parts.append("- 环境: **eNSP 仿真平台**")
    parts.append(f"- 设备名: **{device_name or '未知'}**")
    parts.append(f"- 型号: **{device_type or '未知'}**")
    parts.append(f"- VRP 版本: **{software_version or '未知'}**")
    parts.append(f"- 当前视图: **{current_view or '未知'}**")

    parts.append("")
    parts.append("## 失败命令")
    parts.append("")
    parts.append(f"```\n{failed_command}\n```")

    parts.append("")
    parts.append("## 错误回显")
    parts.append("")
    parts.append(f"```\n{error_text}\n```")

    if running_config_snippet:
        parts.append("")
        parts.append("## 当前运行配置（相关片段）")
        parts.append("")
        parts.append(f"```\n{running_config_snippet}\n```")

    if interface_brief:
        parts.append("")
        parts.append("## 接口状态")
        parts.append("")
        parts.append(f"```\n{interface_brief}\n```")

    if recent_output:
        parts.append("")
        parts.append("## 最近命令输出")
        parts.append("")
        parts.append(f"```\n{recent_output}\n```")

    if rule_diagnosis:
        parts.append("")
        parts.append("## 规则引擎预判（仅供参考）")
        parts.append("")
        cat = rule_diagnosis.get("category", "unknown")
        conf = rule_diagnosis.get("confidence", "low")
        parts.append(f"- 分类: **{cat}**")
        parts.append(f"- 置信度: **{conf}**")
        if rule_diagnosis.get("root_causes"):
            parts.append("- 疑似根因:")
            for rc in rule_diagnosis["root_causes"]:
                parts.append(f"  - {rc}")
        if rule_diagnosis.get("fix_suggestions"):
            parts.append("- 初步建议:")
            for fix in rule_diagnosis["fix_suggestions"]:
                parts.append(f"  - {fix}")

    parts.append("")
    parts.append("## 推理要求")
    parts.append("")
    parts.append("请按以下步骤分析并给出结论：")
    parts.append("")
    parts.append("### 1. 错误类型判断")
    parts.append("这条错误属于哪一类？命令不存在？参数错误？视图错误？还是连接/会话层问题？")
    parts.append("")
    parts.append("### 2. 根因分析")
    parts.append("结合设备信息、当前配置和 eNSP 仿真环境约束，推断最可能的根因。")
    parts.append("")
    parts.append("**eNSP 仿真环境特有约束（优先检查）**：")
    parts.append("- VRP 不认缩写接口名（`GE0/0/0` → 必须 `GigabitEthernet0/0/0`）")
    parts.append("- LoopBack0 是大写 B（不是 `Loopback0`）")
    parts.append("- S5700 系列接口从 1 开始编号（没有 `GE0/0/0`，从 `GE0/0/1` 开始）")
    parts.append("- console 5 分钟无操作自动登出，需重建 Telnet 会话")
    parts.append("- 孤立接口（无对端连线）protocol 永远 down，OSPF 不通告该网段")
    parts.append("- `system-view` 不能重复进入（已在系统视图时再执行会报 Unrecognized）")
    parts.append("- eNSP 设备通过 Telnet 连接（127.0.0.1:端口号），非真实 console 线")
    parts.append("")
    parts.append("**通用排查思路**：")
    parts.append("- 接口名是否使用了全称？")
    parts.append("- 关键字大小写是否正确？")
    parts.append("- 当前视图是否支持该命令？")
    parts.append("- 前置条件是否满足（VLAN 已创建？OSPF 进程已启动？特性已使能？）")
    parts.append("- 是否有配置冲突（IP 地址已被占用？）")
    parts.append("")
    parts.append("### 3. 修复方案")
    parts.append(
        "给出具体的修复命令（可直接下发到 eNSP 设备），按优先级排列。"
        "如果有多条可能的修复方案，标注每种方案的适用条件。"
        "修复命令必须符合 eNSP 仿真环境约束（全称接口名、正确大小写、正确视图）。"
    )
    parts.append("")
    parts.append("### 4. 验证步骤")
    parts.append(
        "修复后应执行哪些验证命令来确认问题已解决？"
        "请列出具体的 eNSP 设备验证命令（如 display ip interface brief、display ospf peer、ping 等）。"
    )

    context.reasoning_prompt = "\n".join(parts)
    return context


def _ospf_state_advice(state: str) -> str:
    """根据 OSPF 邻居状态给出建议。"""
    advice_map = {
        "Down": "邻居未启动或链路不通，检查对端设备与接口状态",
        "Init": "收到 Hello 但未在 Hello 中看到自己的 Router-ID，检查 Hello/Dead 间隔",
        "2-Way": "DR/BDR 选举完成但非全邻接，检查网络类型",
        "ExStart": "正在协商主从关系，若持续此状态检查 MTU",
        "Exchange": "正在交换 DBD，若持续此状态检查 MTU 或接口状态",
        "Loading": "正在请求 LSA，若持续此状态检查链路稳定性",
        "Full": "邻居正常",
    }
    return advice_map.get(state, f"未知状态 {state}，建议检查 OSPF 配置一致性")


def _subnet_contains(network: str, mask: str, target: str) -> bool:
    """判断 target 是否在 network/mask 网段内。

    target 支持 "10.0.0.0" 或 "10.0.0.0/24" 两种格式。
    """
    try:
        if not network or not mask or not target:
            return False
        # 解析 target：支持 "10.0.0.0/24" 格式
        target_ip = target
        if "/" in target:
            target_ip = target.rsplit("/", 1)[0]

        # 简化实现：转换点分十进制为整数做按位与
        def _ip_to_int(ip: str) -> int:
            parts = ip.split(".")
            return (
                (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])
            )

        def _mask_to_int(m: str) -> int:
            if "." in m:
                return _ip_to_int(m)
            bits = int(m)
            return (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF

        net_int = _ip_to_int(network)
        mask_int = _mask_to_int(mask)
        target_int = _ip_to_int(target_ip)
        return (net_int & mask_int) == (target_int & mask_int)
    except (ValueError, IndexError):
        return False


# 模块级单例
_engine: DiagnosticEngine | None = None


def get_diagnostic_engine() -> DiagnosticEngine:
    """获取 DiagnosticEngine 单例。"""
    global _engine
    if _engine is None:
        _engine = DiagnosticEngine()
    return _engine
