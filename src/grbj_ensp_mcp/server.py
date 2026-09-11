"""MCP Server 入口：把 eNSP 自动化能力暴露给 LLM 客户端。

运行方式（stdio 传输，本地进程）::

    python -m grbj_ensp_mcp.server

或在 ``pyproject.toml`` 的 ``[project.scripts]`` 提供的 ``grbj-ensp-mcp`` 命令。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import EnspConfig, load_config
from .diagnostics import (
    LLMDiagnosticContext,
    generate_llm_context,
    get_diagnostic_engine,
)
from .lldp_verifier import (
    LLDPNeighbor,
    LLDPScanResult,
    TopologyComparison,
    collect_lldp_neighbors_with_auto_enable,
    compare_topology_with_lldp,
    render_comparison_markdown,
)
from .port_scanner import scan_ensp_devices, scan_single_port
from .session_manager import (
    SessionError,
    SessionManager,
    get_session_manager,
    reset_session_manager,
)
from .telnet_client import TelnetError
from .topo_parser import Device, Topology, parse_topo, parse_topo_bytes

logger = logging.getLogger("grbj_ensp_mcp.server")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------- #
# MCP 应用
# ---------------------------------------------------------------------- #
SERVER_NAME = "grbj-ensp-mcp"
SERVER_INSTRUCTIONS = (
    "通过 MCP 协议操作本地 eNSP 仿真平台。"
    "典型工作流：1) 调用 ensp_scan_devices 发现已启动的设备；"
    "2) 调用 ensp_connect_device 建立 Telnet 会话；"
    "3) 用 ensp_send_commands 批量下发配置；"
    "4) 用 ensp_save_config 保存；"
    "5) 需要查看当前拓扑时用 ensp_parse_topology 解析 .topo 文件。"
)

# 所有配置类 prompt 共享的"先探查再配置"前置说明：配置命令必须来自设备真实状态。
# 提升到模块级以避免 ruff N806（函数内大写常量），同时便于其它模块引用。
PROBE_FIRST = (
    "⚠️ 配置命令必须来自设备真实状态，禁止直接套用记忆中的命令。\n"
    "eNSP 不同镜像 / VRP 版本支持的命令集不一致，凭空写的命令很可能报 "
    "`Error: Unrecognized command`。正确流程：\n"
    "1. 先调 `ensp_get_device_info`（display version）确认设备型号 / VRP 版本；\n"
    "2. 再调 `ensp_get_running_config`（display current-configuration）看清已有配置；\n"
    "3. 基于上述真实信息生成命令，接口名 / 视图层级 / 参数写法以设备为准；\n"
    "4. 用 `ensp_send_command(s)` 下发并检查返回的 `errored`；若设备报 Error，\n"
    "   据 `errors` 回显修正后重试，不要继续下发后续配置；\n"
    "5. 下发后用 `ensp_verify_*` / `ensp_ping` 校验，最后 `ensp_save_config`。\n"
)


def create_mcp_server(
    config: EnspConfig | None = None,
    *,
    manager: SessionManager | None = None,
) -> FastMCP:
    """工厂方法：构造一个已注册好所有工具的 FastMCP 实例。

    把工厂方法暴露出来主要是为了测试（用 ``InMemoryTransport`` 之类的 transport 做端到端测试）。
    """
    cfg = config or load_config()
    if manager is not None:
        # 测试场景：注入自定义 manager
        sm = manager
    else:
        # 重置单例保证配置生效
        reset_session_manager()
        sm = get_session_manager(cfg)

    mcp: Any = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
    )

    # ------------------------------------------------------------------ #
    # 工具：设备发现
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_scan_devices(
        host: str | None = None,
        port_start: int | None = None,
        port_end: int | None = None,
        timeout: float = 0.5,
        max_workers: int | None = None,
    ) -> dict[str, Any]:
        """扫描 eNSP 设备的 Telnet 端口（默认 2000-2100）。

        返回 ``{"host": str, "ports": [int, ...]}``。
        """
        actual_host = host or cfg.host
        start = port_start if port_start is not None else cfg.port_range[0]
        end = port_end if port_end is not None else cfg.port_range[1]
        results = scan_ensp_devices(
            actual_host,
            port_range=(start, end),
            timeout=timeout,
            max_workers=max_workers,
        )
        return {
            "host": actual_host,
            "port_range": [start, end],
            "reachable_ports": [r.port for r in results],
            "count": len(results),
        }

    # ------------------------------------------------------------------ #
    # 工具：会话管理
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_connect_device(
        port: int,
        host: str | None = None,
        name: str | None = None,
        reuse: bool = True,
    ) -> dict[str, Any]:
        """建立到指定 eNSP 设备的 Telnet 会话。

        ``port`` 为 eNSP 分配的 Telnet 端口（通常 2000+）。
        若 ``reuse=True`` 且已存在同 ``(host, port)`` 的活跃会话则直接复用。
        """
        session = sm.create(host=host, port=port, name=name, reuse=reuse, auto_refresh=True)
        return session.snapshot()

    @mcp.tool()
    def ensp_list_sessions() -> list[dict[str, Any]]:
        """列出当前所有活跃会话的快照。"""
        return sm.list_sessions()

    @mcp.tool()
    def ensp_disconnect_device(session_id_or_name: str) -> dict[str, str]:
        """关闭一个会话并释放其 Telnet 连接。"""
        try:
            sm.close(session_id_or_name)
        except SessionError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "closed", "session": session_id_or_name}

    @mcp.tool()
    def ensp_close_all_sessions() -> dict[str, int]:
        """关闭所有会话。"""
        count = len(sm)
        sm.close_all()
        return {"closed": count}

    @mcp.tool()
    def ensp_cleanup_idle_sessions(idle_seconds: float = 300.0) -> dict[str, Any]:
        """清理闲置超过 ``idle_seconds`` 秒的会话。"""
        closed = sm.cleanup_idle(idle_seconds)
        return {"closed_session_ids": closed, "closed_count": len(closed)}

    # ------------------------------------------------------------------ #
    # 工具：命令交互
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_enter_system_view(session_id_or_name: str) -> dict[str, Any]:
        """把指定会话切换到 ``system-view``。"""
        session = sm.get(session_id_or_name)
        result = session.send_command("system-view")
        return {
            "session": session.name,
            "in_system_view": session.in_system_view,
            "elapsed": result.elapsed,
            "output_tail": result.output.splitlines()[-5:],
        }

    @mcp.tool()
    def ensp_send_command(
        session_id_or_name: str,
        command: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """下发单条命令，返回命令输出、耗时与命令层报错标记。

        ``errored=True`` 表示设备回显中包含 ``Error:``（如命令不存在 / 参数错误），
        此时应据 ``errors`` 回显修正命令后重试，而不是继续下发后续配置。
        """
        session = sm.get(session_id_or_name)
        result = session.send_command(command, timeout=timeout)
        return {
            "session": session.name,
            "command": result.command,
            "elapsed": result.elapsed,
            "output": result.output,
            "errored": result.errored,
            "errors": result.errors,
            "in_system_view": session.in_system_view,
        }

    @mcp.tool()
    def ensp_send_commands(
        session_id_or_name: str,
        commands: list[str],
        stop_on_error: bool = False,
        auto_diagnose: bool = False,
    ) -> dict[str, Any]:
        """批量下发命令，每条命令返回独立的 :class:`TelnetResult`。

        ``stop_on_error=True`` 时，遇到连接错误 **或** 设备命令报错（``errored``）会立刻中断。
        ``auto_diagnose=True`` 时，对每条失败命令自动调用诊断引擎，返回 ``diagnosis`` 字段。
        返回中 ``has_error`` 汇总是否存在任意一条命令报错。
        """
        session = sm.get(session_id_or_name)
        results = session.send_commands(commands, stop_on_error=stop_on_error)

        enriched: list[dict[str, Any]] = []
        for r in results:
            entry = {
                "command": r.command,
                "elapsed": r.elapsed,
                "output": r.output,
                "errored": r.errored,
                "errors": r.errors,
            }
            if auto_diagnose and r.errored and r.errors:
                engine = get_diagnostic_engine()
                error_text = "\n".join(r.errors)
                context = {
                    "hostname": session.info.hostname or "",
                    "device_type": session.info.device_type or "",
                    "software_version": session.info.software_version or "",
                    "in_system_view": session.in_system_view,
                }
                diag = engine.diagnose(error_text, command=r.command, device_context=context)
                if diag:
                    entry["diagnosis"] = diag.to_dict()
            enriched.append(entry)

        return {
            "session": session.name,
            "count": len(enriched),
            "has_error": any(r.errored for r in results),
            "results": enriched,
        }

    @mcp.tool()
    def ensp_save_config(session_id_or_name: str) -> dict[str, Any]:
        """触发 ``save`` 流程：先 ``quit`` 退到用户视图，再 ``save`` 并确认 ``y``。"""
        session = sm.get(session_id_or_name)
        results = session.save()
        return {
            "session": session.name,
            "steps": [
                {"command": r.command, "elapsed": r.elapsed, "output": r.output} for r in results
            ],
        }

    @mcp.tool()
    def ensp_get_recent_output(
        session_id_or_name: str,
        lines: int = 200,
    ) -> dict[str, Any]:
        """获取指定会话输出缓冲中最近 ``lines`` 行。"""
        session = sm.get(session_id_or_name)
        return {
            "session": session.name,
            "lines": session.get_recent_output(lines),
        }

    @mcp.tool()
    def ensp_clear_output_buffer(session_id_or_name: str) -> dict[str, str]:
        """清空指定会话的输出缓冲。"""
        session = sm.get(session_id_or_name)
        session.clear_buffer()
        return {"session": session.name, "status": "cleared"}

    @mcp.tool()
    def ensp_get_device_info(
        session_id_or_name: str,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """获取设备元信息（hostname、型号、版本、uptime）。

        ``refresh=True`` 时重新执行 ``display version``；否则只返回已缓存的值。
        """
        session = sm.get(session_id_or_name)
        info = session.refresh_info() if refresh else session.info
        return {
            "session": session.name,
            "hostname": info.hostname,
            "device_type": info.device_type,
            "software_version": info.software_version,
            "uptime": info.uptime,
        }

    @mcp.tool()
    def ensp_get_running_config(session_id_or_name: str) -> dict[str, Any]:
        """获取设备当前运行配置（``display current-configuration``）。

        **这是配置前必须做的探查步骤**。eNSP 不同镜像 / VRP 版本支持的命令集并不一致，
        直接套用记忆中的命令很可能报错（``Error: Unrecognized command``）。正确做法是：

        1. 先用 :func:`ensp_get_device_info` 拿到型号与 VRP 版本；
        2. 再用本工具拿到设备 **已有的** 配置（已存在的接口 IP、VLAN、路由、特性开关等）；
        3. 基于这两份真实信息生成配置命令，而非凭空猜测；
        4. 下发后用 :func:`ensp_send_command` 的 ``errored`` 回显 / :func:`ensp_verify_interfaces`
           等校验，失败则据设备反馈修正。
        """
        session = sm.get(session_id_or_name)
        result = session.send_command("display current-configuration")
        return {
            "session": session.name,
            "command": "display current-configuration",
            "raw_output": result.output,
            "errored": result.errored,
            "errors": result.errors,
        }

    # ------------------------------------------------------------------ #
    # 工具：状态校验（display 命令快捷封装）
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_verify_interfaces(session_id_or_name: str) -> dict[str, Any]:
        """在指定会话上执行 ``display ip interface brief``，解析后返回。"""
        session = sm.get(session_id_or_name)
        result = session.send_command("display ip interface brief")
        interfaces = _parse_ip_interface_brief(result.output)
        return {
            "session": session.name,
            "raw_output": result.output,
            "interfaces": interfaces,
            "up_count": sum(
                1 for i in interfaces if i.get("physical") == "up" and i.get("protocol") == "up"
            ),
        }

    @mcp.tool()
    def ensp_verify_routes(session_id_or_name: str, protocol: str = "") -> dict[str, Any]:
        """执行 ``display ip routing-table``，可按 protocol 过滤（如 ``ospf`` ``static``）。"""
        session = sm.get(session_id_or_name)
        cmd = "display ip routing-table"
        if protocol:
            cmd = f"display ip routing-table protocol {protocol}"
        result = session.send_command(cmd)
        routes = _parse_routing_table(result.output)
        return {
            "session": session.name,
            "command": cmd,
            "raw_output": result.output,
            "routes": routes,
            "count": len(routes),
        }

    @mcp.tool()
    def ensp_verify_ospf(session_id_or_name: str) -> dict[str, Any]:
        """执行 ``display ospf peer brief`` + ``display ospf routing``，合并返回。"""
        session = sm.get(session_id_or_name)
        peer_result = session.send_command("display ospf peer brief")
        routing_result = session.send_command("display ospf routing")
        peers = _parse_ospf_peer_brief(peer_result.output)
        return {
            "session": session.name,
            "peers": peers,
            "peer_count": len(peers),
            "full_neighbors": sum(1 for p in peers if p.get("state") == "Full"),
            "raw_peer": peer_result.output,
            "raw_routing": routing_result.output,
        }

    @mcp.tool()
    def ensp_verify_vlan(session_id_or_name: str, vlan_id: int | None = None) -> dict[str, Any]:
        """执行 ``display vlan``，可按 vlan_id 过滤。"""
        session = sm.get(session_id_or_name)
        cmd = f"display vlan {vlan_id}" if vlan_id else "display vlan"
        result = session.send_command(cmd)
        return {
            "session": session.name,
            "command": cmd,
            "raw_output": result.output,
        }

    # ------------------------------------------------------------------ #
    # 工具：LLDP 邻居采集 + 与 .topo 交叉验证
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_enable_lldp(
        session_id_or_name: str,
        interfaces: list[str] | None = None,
    ) -> dict[str, Any]:
        """在指定设备上开启 LLDP（用于让设备广播 / 接收邻居信息）。

        LLDP（Link Layer Discovery Protocol）是华为 eNSP 设备默认支持的链路层发现协议，
        设备互相通告自己的接口和系统名。开启后即可用 ``ensp_verify_lldp_neighbors``
        看到哪些接口连到了对端设备的哪个接口。

        工作流程：

        1. ``system-view`` 进入系统视图
        2. ``lldp enable`` 全局使能（华为默认全局开启后所有接口都广播）
        3. 若指定了 ``interfaces``，进每个接口视图执行 ``lldp enable``
           （仅在接口视图下需要单独使能时才需要；默认情况下全局开即可）

        参数：

        - ``session_id_or_name``：会话 ID 或设备名
        - ``interfaces``：可选，要单独使能 LLDP 的接口列表（如 ``["GigabitEthernet0/0/2"]``）；
          留空表示仅全局使能

        返回每步的执行结果（命令 / elapsed / output / errored），失败时停止并标记。
        """
        session = sm.get(session_id_or_name)
        results: list[dict[str, Any]] = []

        # 1) 进 system-view
        cmds: list[str] = ["system-view", "lldp enable"]
        # 2) 可选：进指定接口视图
        if interfaces:
            for iface in interfaces:
                cmds.append(f"interface {iface}")
                cmds.append("lldp enable")
                cmds.append("quit")
        cmds.append("quit")  # 退到用户视图，避免影响后续 prompt 判定

        for cmd in cmds:
            try:
                result = session.send_command(cmd)
            except TelnetError as exc:
                results.append(
                    {
                        "command": cmd,
                        "output": f"<<ERROR: {exc}>>",
                        "errored": True,
                        "errors": [str(exc)],
                    }
                )
                return {
                    "session": session.name,
                    "status": "error",
                    "steps": results,
                    "summary": "LLDP 使能失败，连接错误。",
                }
            entry = {
                "command": cmd,
                "output": result.output[-1000:],
                "errored": result.errored,
                "errors": result.errors,
            }
            results.append(entry)
            # 命令报错也直接中断（lldp enable 失败就别往下走了）
            if result.errored:
                return {
                    "session": session.name,
                    "status": "error",
                    "steps": results,
                    "summary": f"LLDP 使能失败（{cmd} 报错），请查看 errors 字段。",
                }

        return {
            "session": session.name,
            "status": "ok",
            "steps": results,
            "summary": "LLDP 已使能（全局 + 可选接口）。",
        }

    @mcp.tool()
    def ensp_verify_lldp_neighbors(
        session_id_or_name: str,
        detail: bool = False,
        auto_enable: bool = True,
        enable_retry_delay: float = 2.0,
    ) -> dict[str, Any]:
        """采集指定设备的 LLDP 邻居信息（真实物理连接）。

        与 ``ensp_verify_interfaces`` 的区别：

        - ``ensp_verify_interfaces`` 只看本设备接口状态，不知道对端是谁
        - 本工具通过 LLDP 看到的是「对端设备 + 对端接口」，是物理事实

        自动使能 LLDP（默认开启）：

        1. 先发 ``display lldp neighbor brief``（默认）或 ``display lldp neighbor``
        2. 若回显表明 LLDP 未使能（命令报错，或含 ``LLDP is not enabled`` /
           ``LLDP功能未使能`` 等关键字），**且** ``auto_enable=True``，自动走：
           ``system-view → lldp enable → quit``，等 ``enable_retry_delay`` 秒
           让邻居收敛，再发一次采集命令
        3. 若第二次仍然失败，返回 ``status="error"`` 并附带 hint

        把 ``auto_enable`` 设为 ``False`` 时，行为退回到「仅采集，未使能则报错」，
        便于在已经手工开启 LLDP 的脚本里复用。

        输出格式：

        - ``neighbors`` 列表：每条含 ``local_interface`` / ``remote_device``
          / ``remote_interface`` / ``remote_chassis_id`` 等字段
        - 返回的接口名已归一化（``GE0/0/0`` → ``GigabitEthernet0/0/0``）
        - ``lldp_was_enabled`` / ``auto_enable_attempts`` 字段告诉调用方
          本次是否自动使能过 LLDP（避免 LLM 重复操作）

        典型用法：

        - 在每台路由器 / 交换机上各调一次，收集到全网 LLDP 邻居后
          传给 ``ensp_cross_validate_topology`` 做交叉验证

        核心实现见 :func:`grbj_ensp_mcp.lldp_verifier.collect_lldp_neighbors_with_auto_enable`。
        """
        session = sm.get(session_id_or_name)
        return collect_lldp_neighbors_with_auto_enable(
            session,
            detail=detail,
            auto_enable=auto_enable,
            enable_retry_delay=enable_retry_delay,
        )

    @mcp.tool()
    def ensp_cross_validate_topology(
        topo_path: str,
        lldp_results: list[dict[str, Any]],
        device_aliases: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """把 ``.topo`` 文件解析结果与设备 LLDP 邻居做交叉验证。

        这是「拓扑纠错」的核心入口——``ensp_parse_topology`` 只看拓扑文件，
        而 ``ensp_verify_lldp_neighbors`` 反映物理事实。两者不一致时就是
        拓扑图错位 / 线缆未接 / 对端设备未启 / 老拓扑需要更新等问题的信号。

        工作流程：

        1. 解析 ``topo_path``（XML 或 ZIP 自动识别）
        2. 接收 ``lldp_results``（每台设备调 ``ensp_verify_lldp_neighbors``
           返回的 ``scan`` 字段或整个返回对象）
        3. 对每条边做双向比对：

           - **matched**：``.topo`` 和 LLDP 都看到的连线（接口端点完全一致）
           - **topo_only**：``.topo`` 记录了但 LLDP 没看到（线没接 / 对端未启 / LLDP 未使能）
           - **lldp_only**：物理上接了但 ``.topo`` 没记（拓扑图过时）
           - **mismatched**：两端点都对得上，但至少一个接口号错位（拓扑图错位）

        4. 输出结构化 JSON + Markdown 报告，便于 LLM 直接理解和用户存档

        参数：

        - ``topo_path``：``.topo`` 文件绝对路径
        - ``lldp_results``：列表，元素为 ``ensp_verify_lldp_neighbors`` 的返回
          （建议传 ``scan`` 字段；为了容错，传整个返回对象也可）
        - ``device_aliases``：可选，把 LLDP 的 ``system-name`` 映射到 ``.topo``
          里的设备名（如 ``{"AR1": "R1"}``），处理 sysname 与拓扑名不一致的情况

        典型使用场景：

        - **生成配置前验证接口号**：调用本工具确认 ``.topo`` 的 ``GE0/0/0``
          是否真的接在物理 GE0/0/0 上，避免下发配置时把 IP 配到错误接口
        - **拓扑图纠错**：发现接口错位后，用 ``ensp_gather_diagnostic_context``
          收集上下文，让 LLM 给出修改建议（更新 .topo 或重新接线）
        - **实验前环境检查**：学生实验前用本工具确认 eNSP GUI 接的线与设计一致
        """
        # 1) 解析 topo
        try:
            topo = parse_topo(topo_path)
        except FileNotFoundError as exc:
            return {"status": "error", "error": f"未找到拓扑文件: {exc}"}
        except ValueError as exc:
            return {"status": "error", "error": f"拓扑解析失败: {exc}"}

        # 2) 规范化 lldp_results 输入（兼容传整个返回对象 / 只传 scan）
        scans: list[LLDPScanResult] = []
        for entry in lldp_results:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("scan") if isinstance(entry.get("scan"), dict) else entry
            try:
                neighbors_raw = inner.get("neighbors", []) or []
                neighbors: list[LLDPNeighbor] = []
                for n in neighbors_raw:
                    if not isinstance(n, dict):
                        continue
                    neighbors.append(
                        LLDPNeighbor(
                            local_device=str(
                                n.get("local_device", "")
                                or entry.get("session", "")
                                or ""
                            ),
                            local_interface=str(n.get("local_interface", "")),
                            remote_chassis_id=str(n.get("remote_chassis_id", "")),
                            remote_system_name=str(n.get("remote_system_name", "")),
                            remote_device=str(
                                n.get("remote_device", "")
                                or n.get("remote_system_name", "")
                            ),
                            remote_interface=str(n.get("remote_interface", "")),
                            ttl=str(n.get("ttl", "")),
                            age=str(n.get("age", "")),
                        )
                    )
                device_name = str(
                    inner.get("device", "")
                    or entry.get("session", "")
                    or ""
                )
                scan_obj = LLDPScanResult(
                    device=device_name,
                    neighbors=neighbors,
                    raw_output=str(inner.get("raw_output", "")),
                    errored=bool(inner.get("errored", False)),
                    error_message=str(inner.get("error_message", "")),
                )
                # 如果整个 entry 报错，但 inner 没有 errored 也保留提示
                if entry.get("status") == "error" and not scan_obj.errored:
                    scan_obj.errored = True
                    scan_obj.error_message = "; ".join(
                        entry.get("errors", []) or [entry.get("error_message", "")]
                    )
                scans.append(scan_obj)
            except (KeyError, TypeError):
                continue

        # 3) 比对
        cmp: TopologyComparison = compare_topology_with_lldp(
            topo, scans, device_aliases=device_aliases
        )

        # 4) 汇总报告
        return {
            "status": "ok",
            "topo_path": os.path.abspath(topo_path),
            "topo_devices": [d.name for d in topo.devices],
            "lldp_devices_scanned": [s.device for s in scans],
            "comparison": cmp.to_dict(),
            "markdown_report": render_comparison_markdown(cmp),
            "guidance": (
                "matched 表示 .topo 与物理事实一致，可放心使用拓扑里的接口号；"
                "topo_only 需要检查线缆 / 对端设备；lldp_only 表示拓扑图过期；"
                "mismatched 表示 .topo 接口号与物理对不上，下发配置前必须用 LLDP "
                "返回的真实接口号。"
            ),
        }

    @mcp.tool()
    def ensp_ping(
        session_id_or_name: str,
        target: str,
        count: int = 4,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        """在指定会话上执行 ``ping -c {count} {target}``，解析丢包率。"""
        session = sm.get(session_id_or_name)
        cmd = f"ping -c {count} {target}"
        result = session.send_command(cmd, timeout=timeout)
        loss = _parse_ping_loss(result.output)
        return {
            "session": session.name,
            "command": cmd,
            "target": target,
            "loss_percent": loss,
            "reachable": loss < 25.0,  # 1/4 丢包以上视为不稳
            "raw_output": result.output,
        }

    # ------------------------------------------------------------------ #
    # 工具：智能诊断（命令错误根因分析 + 修复建议）
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_diagnose_error(
        session_id_or_name: str,
        error_text: str,
        command: str = "",
    ) -> dict[str, Any]:
        """对一条命令错误进行智能诊断，返回根因分析与修复建议。

        参数：
        - ``session_id_or_name``：会话 ID 或设备名
        - ``error_text``：设备回显中的错误文本（如 ``ensp_send_command`` 返回的 ``errors``）
        - ``command``：触发该错误的命令（可选，用于更精准的诊断）

        返回：
        - ``category``：错误分类标签
        - ``severity``：严重级别（critical/error/warning/info）
        - ``root_causes``：可能的根因列表
        - ``probe_commands``：建议先执行的探查命令
        - ``fix_suggestions``：修复建议列表
        - ``confidence``：诊断置信度（high/medium/low）

        使用场景：
        当 ``ensp_send_command`` 返回 ``errored=True`` 时，将 ``errors`` 字段
        传入本工具获取诊断，而不是直接放弃或盲目重试。
        """
        session = sm.get(session_id_or_name)
        engine = get_diagnostic_engine()
        context = {
            "hostname": session.info.hostname or "",
            "device_type": session.info.device_type or "",
            "software_version": session.info.software_version or "",
            "in_system_view": session.in_system_view,
        }
        diag = engine.diagnose(error_text, command=command, device_context=context)
        if diag is None:
            return {
                "session": session.name,
                "status": "no_match",
                "message": "未匹配到已知错误模式，建议人工分析",
            }
        return {
            "session": session.name,
            "status": "diagnosed",
            "diagnosis": diag.to_dict(),
        }

    @mcp.tool()
    def ensp_diagnose_batch(
        session_id_or_name: str,
        command_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """对批量命令结果（``ensp_send_commands`` 的 ``results`` 列表）进行批量诊断。

        返回每个失败命令的诊断结果，并汇总修复建议。
        """
        session = sm.get(session_id_or_name)
        engine = get_diagnostic_engine()
        context = {
            "hostname": session.info.hostname or "",
            "device_type": session.info.device_type or "",
            "software_version": session.info.software_version or "",
            "in_system_view": session.in_system_view,
        }
        batch = engine.diagnose_batch(
            command_results,
            session_name=session.name,
            device_context=context,
        )
        return batch.to_dict()

    # ------------------------------------------------------------------ #
    # 工具：LLM 驱动诊断上下文收集
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_gather_diagnostic_context(
        session_id_or_name: str,
        failed_command: str,
        error_text: str,
        experiment_description: str = "",
    ) -> dict[str, Any]:
        """当命令执行失败时，自动收集设备全貌供 LLM 深度推理。

        与 ``ensp_diagnose_error`` 的区别：
        - ``ensp_diagnose_error`` 返回规则引擎的快速匹配结果
        - 本工具收集设备真实状态（配置、接口、最近输出），生成结构化推理 prompt，
          供 LLM 像网络工程师一样分析根因

        本工具会自动执行以下探查命令（无需手动调用）：
        1. ``display version`` — 设备信息
        2. ``display current-configuration`` — 运行配置
        3. ``display ip interface brief`` — 接口状态

        返回的 ``reasoning_prompt`` 字段可直接用于 LLM 推理，
        引导 LLM 按四步分析：错误类型判断 → 根因分析 → 修复方案 → 验证步骤。

        典型使用场景：
        - ``ensp_smart_config`` 返回 ``has_error=true`` 且规则诊断置信度为 low 时
        - 规则引擎未覆盖的未知错误
        - 需要 LLM 结合配置上下文做深度推理的复杂场景
        """
        session = sm.get(session_id_or_name)
        engine = get_diagnostic_engine()

        # 先做规则引擎预判
        rule_diag = engine.diagnose(
            error_text,
            command=failed_command,
            device_context={
                "hostname": session.info.hostname or "",
                "device_type": session.info.device_type or "",
                "software_version": session.info.software_version or "",
                "in_system_view": session.in_system_view,
            },
        )

        # 收集设备全貌并生成 LLM 推理上下文
        llm_ctx = _gather_llm_context_for_session(
            session,
            failed_command,
            error_text,
            experiment_description,
        )

        return {
            "session": session.name,
            "rule_diagnosis": rule_diag.to_dict() if rule_diag else None,
            "llm_context": llm_ctx.to_dict(),
            "guidance": (
                "请阅读 reasoning_prompt 字段，按照其中的四步推理框架分析根因。"
                "不要仅依赖 rule_diagnosis（规则引擎预判），LLM 应结合设备上下文做独立判断。"
                "规则引擎置信度为 low 或 medium 时，LLM 的深度推理尤其重要。"
                "修复命令必须符合 eNSP 设备约束（全称接口名、正确大小写、正确视图）。"
            ),
        }

    # ------------------------------------------------------------------ #
    # 工具：设备健康检查
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_health_check(
        session_id_or_name: str,
        check_ospf: bool = False,
        check_routes: bool = False,
        expected_up_interfaces: list[str] | None = None,
        expected_ospf_neighbors: int | None = None,
        expected_routes: list[str] | None = None,
    ) -> dict[str, Any]:
        """对设备进行全面健康检查，自动诊断异常。

        检查项：
        1. 会话状态（连接是否正常）
        2. 设备元信息（VRP 版本、型号）
        3. 接口状态（物理/协议状态，自动诊断 down 的接口）
        4. OSPF 邻居状态（可选，需 ``check_ospf=True``）
        5. 路由表完整性（可选，需 ``check_routes=True``）

        返回结构化诊断报告，包含每个检查项的 ``issues`` 列表和修复建议。
        """
        session = sm.get(session_id_or_name)
        engine = get_diagnostic_engine()
        report: dict[str, Any] = {
            "session": session.name,
            "status": "ok",
            "checks": {},
            "issues_count": 0,
            "summary": "",
        }

        # 1) 会话状态
        report["checks"]["session"] = {
            "status": session.status.value,
            "in_system_view": session.in_system_view,
            "idle_seconds": session.idle_seconds,
            "issues": [],
        }
        if session.status.value == "error":
            report["checks"]["session"]["issues"].append(
                {
                    "issue": f"会话状态异常: {session.status.value}",
                    "severity": "critical",
                    "suggestion": "调用 ensp_disconnect_device 后重新 ensp_connect_device",
                }
            )
            report["issues_count"] += 1
        elif session.idle_seconds > 240:
            report["checks"]["session"]["issues"].append(
                {
                    "issue": f"会话闲置 {session.idle_seconds:.0f} 秒，可能即将超时",
                    "severity": "warning",
                    "suggestion": "发送一条 display 命令保持心跳",
                }
            )

        # 2) 设备信息
        try:
            info = session.refresh_info()
            report["checks"]["device_info"] = {
                "hostname": info.hostname,
                "device_type": info.device_type,
                "software_version": info.software_version,
                "uptime": info.uptime,
                "issues": [],
            }
        except TelnetError as exc:
            report["checks"]["device_info"] = {
                "issues": [
                    {
                        "issue": f"无法获取设备信息: {exc}",
                        "severity": "critical",
                        "suggestion": "检查设备连接是否正常",
                    }
                ],
            }
            report["issues_count"] += 1

        # 3) 接口状态
        try:
            if_result = session.send_command("display ip interface brief")
            if not if_result.errored:
                interfaces = _parse_ip_interface_brief(if_result.output)
                iface_issues = engine.analyze_interface_state(interfaces, expected_up_interfaces)
                report["checks"]["interfaces"] = {
                    "raw_output": if_result.output[-2000:],
                    "interface_count": len(interfaces),
                    "up_count": sum(
                        1
                        for i in interfaces
                        if i.get("physical") == "up" and i.get("protocol") == "up"
                    ),
                    "issues": iface_issues,
                }
                report["issues_count"] += len(iface_issues)
            else:
                report["checks"]["interfaces"] = {
                    "issues": [
                        {
                            "issue": f"display ip interface brief 命令失败: {if_result.errors}",
                            "severity": "error",
                        }
                    ],
                }
                report["issues_count"] += 1
        except TelnetError as exc:
            report["checks"]["interfaces"] = {
                "issues": [
                    {
                        "issue": f"接口检查连接失败: {exc}",
                        "severity": "critical",
                    }
                ],
            }
            report["issues_count"] += 1

        # 4) OSPF 检查
        if check_ospf:
            try:
                ospf_result = session.send_command("display ospf peer brief")
                if not ospf_result.errored:
                    peers = _parse_ospf_peer_brief(ospf_result.output)
                    ospf_issues = engine.analyze_ospf_peers(peers, expected_ospf_neighbors)
                    report["checks"]["ospf"] = {
                        "peers": peers,
                        "full_count": sum(1 for p in peers if p.get("state") == "Full"),
                        "issues": ospf_issues,
                    }
                    report["issues_count"] += len(ospf_issues)
                else:
                    report["checks"]["ospf"] = {
                        "issues": [
                            {
                                "issue": f"display ospf peer brief 失败: {ospf_result.errors}",
                                "severity": "error",
                                "suggestion": "确认 OSPF 进程是否已创建",
                            }
                        ],
                    }
                    report["issues_count"] += 1
            except TelnetError as exc:
                report["checks"]["ospf"] = {
                    "issues": [
                        {
                            "issue": f"OSPF 检查连接失败: {exc}",
                            "severity": "error",
                        }
                    ],
                }
                report["issues_count"] += 1

        # 5) 路由表检查
        if check_routes:
            try:
                route_result = session.send_command("display ip routing-table")
                if not route_result.errored:
                    routes = _parse_routing_table(route_result.output)
                    route_issues = engine.analyze_routing_table(routes, expected_routes)
                    report["checks"]["routes"] = {
                        "route_count": len(routes),
                        "issues": route_issues,
                    }
                    report["issues_count"] += len(route_issues)
                else:
                    report["checks"]["routes"] = {
                        "issues": [
                            {
                                "issue": f"display ip routing-table 失败: {route_result.errors}",
                                "severity": "error",
                            }
                        ],
                    }
                    report["issues_count"] += 1
            except TelnetError as exc:
                report["checks"]["routes"] = {
                    "issues": [
                        {
                            "issue": f"路由表检查连接失败: {exc}",
                            "severity": "error",
                        }
                    ],
                }
                report["issues_count"] += 1

        # 汇总
        if report["issues_count"] == 0:
            report["status"] = "healthy"
            report["summary"] = "设备状态正常，未发现异常。"
        else:
            report["status"] = "issues_found"
            report["summary"] = f"发现 {report['issues_count']} 个问题，详见 checks 各子项。"
            # 生成 Markdown 报告
            md_lines = [f"# 健康检查报告：{session.name}", ""]
            md_lines.append(f"状态: **{report['status']}** | 问题数: **{report['issues_count']}**")
            md_lines.append("")
            for check_name, check_data in report["checks"].items():
                issues = check_data.get("issues", [])
                if issues:
                    md_lines.append(f"## {check_name}")
                    md_lines.append("")
                    for issue in issues:
                        sev = issue.get("severity", "info")
                        md_lines.append(f"- [{sev.upper()}] {issue.get('issue', '')}")
                        if issue.get("suggestion"):
                            md_lines.append(f"  > 建议: {issue['suggestion']}")
                    md_lines.append("")
            report["markdown_report"] = "\n".join(md_lines)

        return report

    # ------------------------------------------------------------------ #
    # 工具：智能配置下发（带诊断 + 修复建议）
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_smart_config(
        session_id_or_name: str,
        commands: list[str],
        stop_on_error: bool = True,
        auto_diagnose: bool = True,
        auto_gather_llm_context: bool = False,
        experiment_description: str = "",
    ) -> dict[str, Any]:
        """智能配置下发：逐条下发命令，遇到错误时自动诊断并提供修复建议。

        与 ``ensp_send_commands`` 的区别：
        - 每条命令执行后，若 ``errored=True`` 自动调用诊断引擎
        - 返回中包含每条失败命令的 ``diagnosis`` 字段
        - ``stop_on_error=True`` 时首条失败即停止，避免级联错误
        - ``auto_gather_llm_context=True`` 时，失败后自动收集设备全貌供 LLM 深度推理

        参数：
        - ``session_id_or_name``：会话 ID 或设备名
        - ``commands``：命令列表
        - ``stop_on_error``：遇错即停（推荐 True）
        - ``auto_diagnose``：自动对失败命令执行规则诊断（默认 True）
        - ``auto_gather_llm_context``：失败后自动收集设备全貌生成 LLM 推理上下文（默认 False）
        - ``experiment_description``：用户实验需求描述（用于 LLM 推理上下文）
        """
        session = sm.get(session_id_or_name)
        engine = get_diagnostic_engine()
        context = {
            "hostname": session.info.hostname or "",
            "device_type": session.info.device_type or "",
            "software_version": session.info.software_version or "",
            "in_system_view": session.in_system_view,
        }

        results: list[dict[str, Any]] = []
        has_error = False
        first_error_diagnosis = None
        first_error_command = ""
        first_error_text = ""

        for cmd in commands:
            try:
                result = session.send_command(cmd)
            except TelnetError as exc:
                err_entry = {
                    "command": cmd,
                    "elapsed": 0.0,
                    "output": f"<<ERROR: {exc}>>",
                    "errored": True,
                    "errors": [str(exc)],
                }
                results.append(err_entry)
                has_error = True
                if auto_diagnose:
                    diag = engine.diagnose(str(exc), command=cmd, device_context=context)
                    if diag:
                        err_entry["diagnosis"] = diag.to_dict()
                        if first_error_diagnosis is None:
                            first_error_diagnosis = diag.to_dict()
                            first_error_command = cmd
                            first_error_text = str(exc)
                if stop_on_error:
                    break
                continue

            entry = {
                "command": result.command,
                "elapsed": result.elapsed,
                "output": result.output[-2000:],
                "errored": result.errored,
                "errors": result.errors,
            }

            if result.errored:
                has_error = True
                if auto_diagnose:
                    error_text = "\n".join(result.errors)
                    diag = engine.diagnose(
                        error_text, command=result.command, device_context=context
                    )
                    if diag:
                        entry["diagnosis"] = diag.to_dict()
                        if first_error_diagnosis is None:
                            first_error_diagnosis = diag.to_dict()
                            first_error_command = result.command
                            first_error_text = error_text
                if stop_on_error:
                    results.append(entry)
                    break

            results.append(entry)

        response: dict[str, Any] = {
            "session": session.name,
            "count": len(results),
            "has_error": has_error,
            "results": results,
            "first_error_diagnosis": first_error_diagnosis,
            "summary": (
                "所有命令执行成功。"
                if not has_error
                else f"{len(results)} 条命令中有错误，已自动诊断。查看 first_error_diagnosis 获取根因与修复建议。"
            ),
        }

        # 如果开启了 LLM 上下文收集且有错误，自动收集设备全貌
        if auto_gather_llm_context and has_error and first_error_command:
            try:
                llm_ctx = _gather_llm_context_for_session(
                    session,
                    first_error_command,
                    first_error_text,
                    experiment_description,
                )
                response["llm_context"] = llm_ctx.to_dict()
                response["llm_guidance"] = (
                    "请阅读 llm_context.reasoning_prompt 字段，"
                    "按照其中的四步推理框架分析根因。"
                    "不要仅依赖 first_error_diagnosis（规则引擎预判），"
                    "LLM 应结合设备上下文做独立判断。"
                )
            except TelnetError:
                response["llm_context"] = {
                    "error": "收集 LLM 上下文时设备连接失败，请先检查设备状态"
                }

        return response

    # ------------------------------------------------------------------ #
    # 工具：一站式实验配置（编排 + 校验 + 报告）
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_apply_experiment(
        plan: dict[str, list[str]],
        verify_targets: list[dict[str, Any]] | None = None,
        auto_save: bool = True,
    ) -> dict[str, Any]:
        """一站式下发实验配置：按设备规划的命令序列逐台下发 + 校验 + 报告。

        参数：
        - ``plan``：``{ "设备名": ["system-view", "interface GE0/0/0", "ip address ...", ...], ... }``
        - ``verify_targets``：可选校验项，元素为 ``{"device": str, "kind": "ospf"|"vlan"|"routes"|"interfaces"|"ping", "args": {...}}``
        - ``auto_save``：下发完成后自动 save

        工作流：

        1. 对 plan 中每台设备调用 ``ensp_send_commands``（自动进 system-view 后再下发）
        2. 跑 verify_targets
        3. auto_save=True 时全部 save
        4. 返回结构化报告 + Markdown
        """
        apply_results: list[dict[str, Any]] = []
        verify_results: list[dict[str, Any]] = []
        save_results: list[dict[str, Any]] = []
        engine = get_diagnostic_engine()

        # 1) 下发
        for device_name, cmds in plan.items():
            if not cmds:
                continue
            try:
                session = sm.get(device_name)
            except SessionError as exc:
                apply_results.append(
                    {"device": device_name, "status": "error", "error": f"会话不存在: {exc}"}
                )
                continue
            # 确保首条是 system-view（避免用户已经在 system-view 时重复）
            commands = list(cmds)
            if commands and commands[0].strip() != "system-view":
                commands.insert(0, "system-view")
            # 末尾加 quit 退到用户视图（save 需要在用户视图）
            if commands and commands[-1].strip() != "quit":
                commands.append("quit")
            try:
                results = session.send_commands(commands)
                device_has_error = any(r.errored for r in results)
                diag = None
                if device_has_error:
                    diag = engine.diagnose_batch(
                        [
                            {
                                "command": r.command,
                                "errored": r.errored,
                                "errors": r.errors,
                            }
                            for r in results
                        ],
                        session_name=device_name,
                        device_context={
                            "hostname": session.info.hostname or "",
                            "device_type": session.info.device_type or "",
                            "software_version": session.info.software_version or "",
                        },
                    )
                apply_results.append(
                    {
                        "device": device_name,
                        "status": "ok",
                        "has_error": device_has_error,
                        "commands_count": len(results),
                        "elapsed_total": sum(r.elapsed for r in results),
                        "outputs_tail": [r.output.splitlines()[-3:] for r in results],
                        "diagnosis": diag.to_dict() if diag and diag.failed_commands > 0 else None,
                    }
                )
            except TelnetError as exc:
                apply_results.append({"device": device_name, "status": "error", "error": str(exc)})

        # 2) 校验
        for vt in verify_targets or []:
            device = vt.get("device")
            kind = vt.get("kind", "interfaces")
            args = vt.get("args", {}) or {}
            if not device:
                continue
            try:
                session = sm.get(device)
            except SessionError as exc:
                verify_results.append(
                    {"device": device, "kind": kind, "status": "error", "error": str(exc)}
                )
                continue
            try:
                if kind == "ospf":
                    cmd_result = session.send_command("display ospf peer brief")
                    peers = _parse_ospf_peer_brief(cmd_result.output)
                    verify_results.append(
                        {
                            "device": device,
                            "kind": kind,
                            "status": "ok",
                            "peers": peers,
                            "full_count": sum(1 for p in peers if p.get("state") == "Full"),
                        }
                    )
                elif kind == "vlan":
                    cmd_result = session.send_command(
                        f"display vlan {args.get('vlan_id', '')}".strip()
                    )
                    verify_results.append(
                        {"device": device, "kind": kind, "status": "ok", "raw": cmd_result.output}
                    )
                elif kind == "routes":
                    cmd_result = session.send_command(
                        f"display ip routing-table protocol {args.get('protocol', '')}".strip()
                    )
                    routes = _parse_routing_table(cmd_result.output)
                    verify_results.append(
                        {
                            "device": device,
                            "kind": kind,
                            "status": "ok",
                            "route_count": len(routes),
                            "routes": routes,
                        }
                    )
                elif kind == "interfaces":
                    cmd_result = session.send_command("display ip interface brief")
                    ifs = _parse_ip_interface_brief(cmd_result.output)
                    verify_results.append(
                        {
                            "device": device,
                            "kind": kind,
                            "status": "ok",
                            "interface_count": len(ifs),
                            "up_count": sum(
                                1
                                for i in ifs
                                if i.get("physical") == "up" and i.get("protocol") == "up"
                            ),
                            "interfaces": ifs,
                        }
                    )
                elif kind == "ping":
                    target = args.get("target", "")
                    count = int(args.get("count", 4))
                    cmd_result = session.send_command(f"ping -c {count} {target}")
                    verify_results.append(
                        {
                            "device": device,
                            "kind": kind,
                            "status": "ok",
                            "target": target,
                            "loss": _parse_ping_loss(cmd_result.output),
                        }
                    )
                else:
                    verify_results.append(
                        {
                            "device": device,
                            "kind": kind,
                            "status": "skipped",
                            "reason": f"未知校验类型: {kind}",
                        }
                    )
            except TelnetError as exc:
                verify_results.append(
                    {"device": device, "kind": kind, "status": "error", "error": str(exc)}
                )

        # 3) save
        if auto_save:
            for device_name in plan:
                try:
                    session = sm.get(device_name)
                    session.save()
                    save_results.append({"device": device_name, "status": "saved"})
                except (SessionError, TelnetError) as exc:
                    save_results.append(
                        {"device": device_name, "status": "error", "error": str(exc)}
                    )

        # 4) 报告
        apply_ok = sum(1 for r in apply_results if r["status"] == "ok")
        verify_ok = sum(1 for r in verify_results if r["status"] == "ok")
        save_ok = sum(1 for r in save_results if r.get("status") == "saved")
        overall_ok = (
            apply_ok == len(plan)
            and (not verify_targets or verify_ok == len(verify_targets))
            and (not auto_save or save_ok == len(plan))
        )
        return {
            "status": "success" if overall_ok else "partial" if apply_ok > 0 else "failed",
            "apply_results": apply_results,
            "verify_results": verify_results,
            "save_results": save_results,
            "summary": {
                "devices_planned": len(plan),
                "apply_ok": apply_ok,
                "verify_ok": verify_ok,
                "save_ok": save_ok,
            },
            "markdown_report": _render_apply_experiment_report(
                plan, apply_results, verify_results, save_results
            ),
        }

    # 注：原 ensp_apply_experiment_template（内置写死命令模板）已移除。
    # 配置命令必须由 LLM 基于设备真实探查（ensp_get_device_info +
    # ensp_get_running_config）生成，再调用 ensp_apply_experiment(plan=...) 下发，
    # 避免向 eNSP 设备下发其不支持的命令。

    # ------------------------------------------------------------------ #
    # 工具：拓扑解析
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def ensp_parse_topology(path: str) -> dict[str, Any]:
        """解析本地 ``.topo`` 文件（XML 或 ZIP 格式）。"""
        try:
            topo = parse_topo(path)
        except (FileNotFoundError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}
        return topo.to_dict() | {"summary": topo.render_summary()}

    @mcp.tool()
    def ensp_parse_topology_b64(content_b64: str, name: str = "uploaded") -> dict[str, Any]:
        """解析 base64 编码的 ``.topo`` 字节（用于 MCP 客户端直接上传）。

        输入是标准 base64 字符串。返回与 ``ensp_parse_topology`` 一致的结构。
        """
        try:
            data = base64.b64decode(content_b64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            return {"status": "error", "error": f"base64 解码失败: {exc}"}
        try:
            topo = parse_topo_bytes(data, name=name)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        return topo.to_dict() | {"summary": topo.render_summary()}

    @mcp.tool()
    def ensp_load_topology(
        path: str,
        auto_connect: bool = True,
        scan_timeout: float = 0.5,
    ) -> dict[str, Any]:
        """加载 ``.topo`` 拓扑：解析 → 探测每个设备的 com_port → 自动建连 → 输出启动指引。

        典型工作流：

        1. 调用本工具解析 ``.topo``，获得设备清单与连线；
        2. 工具对每个 ``com_port`` 做 TCP 探测（无需 telnet 登录）；
        3. 对可达端口自动调用 ``ensp_connect_device`` 建连；
        4. 对不可达端口，把设备列入 ``pending_devices`` 并附带 eNSP GUI 启动步骤；
        5. 用户在 eNSP GUI 中启动设备后，再次调用本工具（或 ``ensp_scan_devices`` +
           ``ensp_connect_device`` 组合）补齐剩余会话。

        返回 JSON + Markdown 报告（``markdown_report`` 字段）。
        """
        try:
            topo = parse_topo(path)
        except FileNotFoundError as exc:
            return {"status": "error", "error": f"未找到拓扑文件: {exc}"}
        except ValueError as exc:
            return {"status": "error", "error": f"拓扑解析失败: {exc}"}

        reachable_map: dict[int, bool] = {}
        reachable_errors: dict[int, str] = {}
        for dev in topo.devices:
            port = dev.console_port
            if port <= 1:
                continue  # PC / Cloud 占位，不需要探测
            result = scan_single_port(cfg.host, port, timeout=scan_timeout)
            reachable_map[port] = result.reachable
            if not result.reachable and result.error:
                reachable_errors[port] = result.error

        connected: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        if auto_connect:
            for dev in topo.devices:
                entry = _connect_or_record(
                    dev=dev,
                    sm=sm,
                    host=cfg.host,
                    reachable_map=reachable_map,
                )
                if isinstance(entry, dict) and entry.get("status") == "connected":
                    connected.append(entry)
                elif isinstance(entry, dict):
                    pending.append(entry)

        instructions = _render_ensp_gui_instructions(path) if pending else None
        subnet_plan = _plan_subnets(topo)
        md = _render_load_topology_report(
            topo=topo,
            path=path,
            connected=connected,
            pending=pending,
            reachable_map=reachable_map,
            reachable_errors=reachable_errors,
            subnet_plan=subnet_plan,
        )

        return {
            "status": "ok",
            "source_path": os.path.abspath(path),
            "topology": topo.to_dict(),
            "summary": topo.render_summary(),
            "subnet_plan": subnet_plan,
            "reachable_ports": sorted(p for p, r in reachable_map.items() if r),
            "unreachable_ports": sorted(p for p, r in reachable_map.items() if not r),
            "connected_sessions": connected,
            "pending_devices": pending,
            "ensp_gui_instructions": instructions,
            "markdown_report": md,
        }

    # ------------------------------------------------------------------ #
    # 资源
    # ------------------------------------------------------------------ #
    @mcp.resource("ensp://config")
    def ensp_config_resource() -> str:
        """返回当前 eNSP 配置的 JSON 字符串。"""
        import json

        return json.dumps(
            {
                "host": cfg.host,
                "port_range": list(cfg.port_range),
                "encoding": cfg.encoding,
                "connect_timeout": cfg.connect_timeout,
                "read_timeout": cfg.read_timeout,
                "default_command_delay": cfg.default_command_delay,
                "default_long_command_delay": cfg.default_long_command_delay,
                "session_buffer_size": cfg.session_buffer_size,
                "scanner_max_workers": cfg.scanner_max_workers,
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.resource("ensp://sessions")
    def ensp_sessions_resource() -> str:
        """返回当前所有活跃会话的快照（JSON 字符串）。"""
        import json

        return json.dumps(sm.list_sessions(), ensure_ascii=False, indent=2)

    @mcp.resource("ensp://devices")
    def ensp_devices_resource() -> str:
        """重新扫描并返回当前可达的 eNSP 设备端口列表（JSON 字符串）。"""
        import json

        # 注意：scan_ensp_devices 的第一个位置参数是 host，必须显式传 config= 才不会把
        # EnspConfig 实例当 host 传入（运行时会因 socket 期待 str 而崩溃）。
        results = scan_ensp_devices(config=cfg)
        payload = {
            "host": cfg.host,
            "reachable_ports": [r.port for r in results],
            "count": len(results),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    # 提示模板
    # ------------------------------------------------------------------ #
    @mcp.prompt()
    def configure_vlan(vlan_id: int, name: str = "", ports: str = "") -> str:
        """VLAN 配置提示：在交换机上创建 VLAN 并配置接入端口。

        参数：
        - ``vlan_id``：VLAN 号
        - ``name``：VLAN 名称（可选）
        - ``ports``：要加入该 VLAN 的接入端口，逗号分隔，例如 ``"GE0/0/1,GE0/0/2"``
        """
        return (
            PROBE_FIRST + f"\n请按以下思路在目标交换机上配置 VLAN {vlan_id}：\n\n"
            f"- 进入 system-view；\n"
            f"- `vlan {vlan_id}` 进入 VLAN 视图（名称可选，设备支持时加 description）；\n"
            f"- 接入端口：进接口视图配 access 类型 + 默认 VLAN；\n"
            f"- 若对端是交换机，端口需配 trunk 并放行该 VLAN；\n"
            f"- 接口命名（如 GE0/0/x 或 GigabitEthernet0/0/x）、trunk 关键字以设备实际为准，\n"
            f"  先用 `ensp_get_running_config` 看清同类端口已用什么写法，再据此生成命令；\n"
            f"- 需配置的端口：{ports or '（待在探查后确定）'}；\n"
            f"- 下发后用 `ensp_verify_vlan` 校验，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_ospf(
        process_id: int,
        router_id: str,
        networks: str,
        area: int = 0,
    ) -> str:
        """OSPF 配置提示：在指定路由器上启用 OSPF 进程并宣告网段。

        参数：
        - ``process_id``：OSPF 进程号
        - ``router_id``：Router-ID，例如 ``1.1.1.1``
        - ``networks``：要宣告的网段列表（CIDR），例如 ``"10.0.12.0/30,192.168.1.0/24"``
        - ``area``：区域号，默认 0
        """
        return (
            PROBE_FIRST
            + f"\n请按以下思路在目标路由器上配置 OSPF（进程 {process_id}，区域 {area}）：\n\n"
            f"- Router-ID 建议设为 {router_id}（loopback 或手工指定，写法以设备支持的为准）；\n"
            f"- 待宣告网段：{networks or '（待定）'}，掩码写法以设备为准；\n"
            f"- eNSP 路由器常见两种宣告写法：全局 `ospf` 视图下 `network` + 反掩码，\n"
            f"  或进接口视图 `ospf enable area`。先用 `ensp_get_device_info` 确认 VRP 版本，\n"
            f"  再选设备支持的写法，不要两种混用；\n"
            f"- 下发后用 `ensp_verify_ospf` 看邻居是否到 Full、`ensp_verify_routes` 看路由；\n"
            f"- 连通性用 `ensp_ping` 验证，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_static_route(
        destination: str,
        mask: str,
        next_hop: str,
    ) -> str:
        """静态路由配置提示：在指定设备上配置一条 IPv4 静态路由。

        也可用于默认路由：``destination="0.0.0.0"``、``mask="0.0.0.0"``。
        """
        return (
            PROBE_FIRST + f"\n请按以下思路在目标设备上配置静态路由：\n\n"
            f"- 目的网段：{destination}  掩码：{mask}  下一跳：{next_hop}\n"
            f"  （{destination} 为 0.0.0.0 且 {mask} 为 0.0.0.0 即默认路由）\n"
            f"- 关键字 `ip route-static` 各版本一致，但下一跳 / 出接口的写法、\n"
            f"  以及是否需先为出接口配 IP 以设备为准；若用出接口方式，\n"
            f"  先 `ensp_get_running_config` 确认接口已配置地址；\n"
            f"- 下发后用 `ensp_verify_routes` 校验是否进入路由表，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_acl(
        acl_number: int,
        rule_lines: str,
        interface_apply: str = "",
        traffic_filter: str = "inbound",
    ) -> str:
        """ACL 配置提示：基本 / 高级 ACL，绑定到接口或全局。

        参数：
        - ``acl_number``：2000=基本 3000=高级 4000=二层
        - ``rule_lines``：rule 子句，多条用 ``\\n`` 分隔，例如 ``"rule 5 permit source 192.168.1.0 0.0.0.255"``
        - ``interface_apply``：要绑定的接口名（如 ``GE0/0/0``），留空表示不绑定
        - ``traffic_filter``：``inbound`` 或 ``outbound``
        """
        return (
            PROBE_FIRST + f"\n请按以下思路配置 ACL {acl_number}（基本/高级类型以设备为准）：\n\n"
            f"- 规则子句：{rule_lines or '（待定，如 rule 5 permit source 192.168.1.0 0.0.0.255）'}，\n"
            f"  rule 关键字、匹配语法以设备支持的写法为准；\n"
            f"- 绑定接口 {interface_apply or '（可不绑定，仅做匹配）'} 时用 "
            f"traffic-filter {traffic_filter}，接口名与 traffic-filter 关键字\n"
            f"  先 `ensp_get_running_config` 确认设备已支持的写法；\n"
            f"- 下发后检查 `errored`；用 `display acl {acl_number}` / "
            f"`display traffic-filter` 校验，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_nat(
        nat_mode: str = "easy-ip",
        inside_interfaces: str = "",
        outside_interface: str = "",
        static_mappings: str = "",
        acl_number: int = 2000,
    ) -> str:
        """NAT 配置提示：Easy IP / 动态 NAT / 静态 NAT Server。

        参数：
        - ``nat_mode``：``"easy-ip"`` / ``"dynamic"`` / ``"static"``
        - ``inside_interfaces``：内网接口列表，逗号分隔（动态 / Easy IP 适用）
        - ``outside_interface``：公网出口接口
        - ``static_mappings``：静态映射，格式 ``"公网IP|私网IP,公网IP|私网IP"``
        - ``acl_number``：用于匹配内网流量的 ACL
        """
        return (
            PROBE_FIRST + f"\n请按以下思路配置 NAT（模式 {nat_mode}）：\n\n"
            f"- Easy IP：先 `acl` 定义匹配内网流量的规则，再在出接口 {outside_interface} 下配 "
            f"`nat outbound` + ACL 号（无地址池）；\n"
            f"- 动态 NAT：定义地址池（nat address-group）+ ACL + 出接口 `nat outbound` address-group；\n"
            f"- 静态 NAT Server：用 `nat static global <公网> inside <私网>`，并在出接口启用；\n"
            f"- nat / acl / address-group 的关键字与参数写法以设备为准，\n"
            f"  先 `ensp_get_running_config` 看设备是否已有 nat 相关配置与接口命名；\n"
            f"- 下发后检查 `errored`；用 `display nat outbound` / `display nat static` 校验，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_stp(
        mode: str = "rstp",
        root_primary: str = "",
        root_secondary: str = "",
        bridge_priority: str = "",
    ) -> str:
        """STP / RSTP / MSTP 配置提示。

        参数：
        - ``mode``：``"stp"`` / ``"rstp"`` / ``"mstp"``
        - ``root_primary``：主根桥设备名（设 priority 0）
        - ``root_secondary``：备根桥设备名（设 priority 4096）
        - ``bridge_priority``：其余设备的 priority，如 ``"32768"`` 留空保持默认
        """
        return (
            PROBE_FIRST + f"\n请按以下思路配置 {mode.upper()}：\n\n"
            f"- 全网设备先 `stp mode {mode}`（命令关键字各版本一致）；\n"
            f"- 根桥选举：主根设 priority 0、备根 4096（{root_primary or '主根'} / {root_secondary or '备根'}），\n"
            f"  其余设备 priority 用 {bridge_priority or '32768'}；\n"
            f"- MSTP 还需配 region-name + instance + vlan 映射并 `active region-configuration`，\n"
            f"  具体关键字以设备为准，先用 `ensp_get_running_config` 看现有 stp 配置；\n"
            f"- 下发后检查 `errored`，用 `display stp brief` 看根桥角色。\n"
        )

    @mcp.prompt()
    def configure_dhcp(
        role: str = "server",
        pool_name: str = "pool1",
        network: str = "192.168.10.0",
        mask: str = "24",
        gateway: str = "192.168.10.1",
        dns: str = "8.8.8.8",
        lease_day: int = 3,
        relay_server: str = "",
        relay_vlanif: str = "",
    ) -> str:
        """DHCP Server / Relay 配置提示。

        参数：
        - ``role``：``"server"`` 或 ``"relay"``
        - ``pool_name``：地址池名（仅 server）
        - ``network`` / ``mask``：网段（CIDR 中的网络号 + 掩码位）
        - ``gateway``：默认网关 IP
        - ``dns``：DNS 服务器 IP
        - ``lease_day``：租期天数
        - ``relay_server``：relay 模式下指定 DHCP Server 的 IP
        - ``relay_vlanif``：relay 模式下的中继接口（如 Vlanif10）
        """
        return (
            PROBE_FIRST + f"\n请按以下思路配置 DHCP（角色 {role}）：\n\n"
            f"- 先 `dhcp enable` 全局使能；\n"
            f"- Server：建地址池（ip pool），配 network/mask、gateway-list、dns-list、lease，\n"
            f"  并在对应三层接口（如 Vlanif）下 `dhcp select global`；\n"
            f"- Relay：在连接客户端的接口（如 {relay_vlanif or 'VlanifX'}）下 "
            f"`dhcp select relay` + `dhcp relay server-ip {relay_server or '<server-ip>'}`；\n"
            f"- 网段 {network}/{mask}、网关 {gateway}、DNS {dns}、租期 {lease_day} 天按实验规划填写，\n"
            f"  但接口名 / vlanif 写法以设备为准，先用 `ensp_get_running_config` 确认；\n"
            f"- 下发后检查 `errored`；用 `display ip pool` / `display dhcp relay` 校验。\n"
        )

    @mcp.prompt()
    def configure_vrrp(
        vrid: int = 1,
        vrrp_ip: str = "",
        interface: str = "Vlanif10",
        priority: int = 100,
        preempt: bool = True,
    ) -> str:
        """VRRP 主备配置提示。

        参数：
        - ``vrid``：VRID
        - ``vrrp_ip``：虚拟 IP 地址
        - ``interface``：参与 VRRP 的接口（一般 VlanifX）
        - ``priority``：本设备优先级（默认 100，备机建议 50）
        - ``preempt``：是否开启抢占
        """
        return (
            PROBE_FIRST + f"\n请按以下思路配置 VRRP（VRID={vrid}）：\n\n"
            f"- 在参与 VRRP 的接口 {interface}（一般为 VlanifX）下配：\n"
            f"  `vrrp vrid {vrid} virtual-ip {vrrp_ip}`、`vrrp vrid {vrid} priority {priority}`；\n"
            f"- 抢占：{'需要（preempt 命令以设备实际支持为准）' if preempt else '不需要抢占'}；\n"
            f"- 接口名 / vrrp 关键字先 `ensp_get_running_config` 确认；\n"
            f"- 下发后检查 `errored`；用 `display vrrp brief` 校验主备状态，最后 `ensp_save_config`。\n"
        )

    @mcp.prompt()
    def configure_eth_trunk(
        trunk_id: int,
        members: str,
        mode: str = "lacp",
        lacp_priority: int = 32768,
    ) -> str:
        """Eth-Trunk 链路聚合配置提示。

        参数：
        - ``trunk_id``：Eth-Trunk 编号
        - ``members``：成员接口，逗号分隔（最少 2 个）
        - ``mode``：``"lacp"`` / ``"manual"``
        - ``lacp_priority``：LACP 系统优先级（lacp 模式）
        """
        member_list = [m.strip() for m in members.split(",") if m.strip()]
        return (
            PROBE_FIRST
            + f"\n请按以下思路配置 Eth-Trunk {trunk_id}（模式 {mode}，成员：{', '.join(member_list) or '待定'}）：\n\n"
            f"- 创建聚合口 `interface Eth-Trunk {trunk_id}`；\n"
            f"- LACP 模式配 `mode lacp-static`（可选 lacp priority），手工模式配负载分担算法；\n"
            f"- 把成员接口 {', '.join(member_list) or '<members>'} 加入聚合：进成员接口视图 `eth-trunk {trunk_id}`；\n"
            f"- 成员接口名、mode 关键字以设备为准，先用 `ensp_get_running_config` 看现有 trunk 配置；\n"
            f"- 下发后检查 `errored`；用 `display eth-trunk {trunk_id}` 校验成员与状态。\n"
        )

    @mcp.prompt()
    def troubleshoot_connectivity(source: str, target: str) -> str:
        """端到端连通性故障排查提示：从源设备 ping 目标 IP。

        用 ``ensp_ping`` 下发 ping 命令，再用 ``ensp_verify_routes`` 查路由。
        """
        return (
            PROBE_FIRST + f"\n请对 {source} 到 {target} 的连通性进行排查：\n\n"
            f"1. 在 {source} 上调用 `ensp_ping` 测试 {target} 的可达性；\n"
            f"2. 如不通，先 `ensp_get_device_info` + `ensp_get_running_config` 看清设备型号与已有配置；\n"
            f"3. 再依次调用：\n"
            f"   - `ensp_verify_interfaces` 看接口 IP 与物理 / 协议状态；\n"
            f"   - `ensp_verify_routes` 看路由表是否有目的网段；\n"
            f"   - `ensp_verify_vlan` / `ensp_verify_ospf` 看 VLAN / OSPF；\n"
            f"4. 排查结束后用 Markdown 报告问题点与修复建议。\n"
        )

    return mcp  # type: ignore[return-value]


# ---------------------------------------------------------------------- #
# 实验配置报告渲染（对已下发的 plan 生成 Markdown，不负责生成命令）
# ---------------------------------------------------------------------- #
def _render_apply_experiment_report(
    plan: dict[str, list[str]],
    apply_results: list[dict[str, Any]],
    verify_results: list[dict[str, Any]],
    save_results: list[dict[str, Any]],
) -> str:
    """生成 ensp_apply_experiment 的 Markdown 报告。"""
    lines: list[str] = ["# 实验配置报告", ""]
    lines.append(f"- 计划设备数：**{len(plan)}**")
    apply_ok = sum(1 for r in apply_results if r.get("status") == "ok")
    lines.append(f"- 下发成功：**{apply_ok}/{len(plan)}**")
    if verify_results:
        verify_ok = sum(1 for r in verify_results if r.get("status") == "ok")
        lines.append(f"- 校验成功：**{verify_ok}/{len(verify_results)}**")
    if save_results:
        save_ok = sum(1 for r in save_results if r.get("status") == "saved")
        lines.append(f"- 保存成功：**{save_ok}/{len(save_results)}**")
    lines.append("")
    lines.append("## 下发详情")
    lines.append("")
    for r in apply_results:
        if r.get("status") == "ok":
            error_mark = " (有命令报错)" if r.get("has_error") else ""
            lines.append(
                f"- {'⚠' if r.get('has_error') else '✓'} **{r['device']}**  "
                f"{r['commands_count']} 条命令，"
                f"总耗时 {r['elapsed_total']:.2f}s{error_mark}"
            )
            if r.get("diagnosis"):
                diag = r["diagnosis"]
                lines.append(f"  > 诊断: {diag.get('summary', '')}")
        else:
            lines.append(f"- ✗ **{r['device']}**  {r.get('error', '失败')}")
    lines.append("")
    if verify_results:
        lines.append("## 校验结果")
        lines.append("")
        for v in verify_results:
            kind = v.get("kind", "?")
            device = v.get("device", "?")
            if v.get("status") == "ok":
                detail = ""
                if kind == "ospf":
                    detail = f"邻居数 {len(v.get('peers', []))}，Full {v.get('full_count', 0)}"
                elif kind == "routes":
                    detail = f"路由 {v.get('route_count', 0)} 条"
                elif kind == "interfaces":
                    detail = f"接口 {v.get('interface_count', 0)} 个，up {v.get('up_count', 0)}"
                elif kind == "ping":
                    detail = f"到 {v.get('target')} 丢包 {v.get('loss')}%"
                else:
                    detail = "(详见 raw)"
                lines.append(f"- ✓ **{device}** ({kind}) {detail}")
            else:
                lines.append(f"- ✗ **{device}** ({kind}) {v.get('error', v.get('reason', '失败'))}")
        lines.append("")
    if save_results:
        lines.append("## 保存结果")
        lines.append("")
        for s in save_results:
            if s.get("status") == "saved":
                lines.append(f"- ✓ **{s['device']}** 已保存")
            else:
                lines.append(f"- ✗ **{s['device']}** {s.get('error', '保存失败')}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# display 命令输出解析
# ---------------------------------------------------------------------- #
def _gather_llm_context_for_session(
    session: object,
    failed_command: str,
    error_text: str,
    experiment_description: str = "",
) -> LLMDiagnosticContext:
    """从设备会话中收集 LLM 推理所需的全部上下文。

    内部函数，供 ``ensp_smart_config`` 和 ``ensp_gather_diagnostic_context`` 复用。
    """
    engine = get_diagnostic_engine()

    # 规则引擎预判
    rule_diag = engine.diagnose(
        error_text,
        command=failed_command,
        device_context={
            "hostname": session.info.hostname or "",
            "device_type": session.info.device_type or "",
            "software_version": session.info.software_version or "",
            "in_system_view": session.in_system_view,
        },
    )

    # 收集设备状态
    device_type = session.info.device_type or ""
    software_version = session.info.software_version or ""
    running_config = ""
    interface_brief = ""
    recent_output = session.get_recent_output(lines=100)
    current_view = "system-view" if session.in_system_view else "user-view"

    try:
        session.refresh_info()
        device_type = session.info.device_type or device_type
        software_version = session.info.software_version or software_version
    except TelnetError:
        pass

    try:
        config_keywords = _extract_config_keywords(failed_command)
        config_result = session.send_command("display current-configuration")
        if not config_result.errored:
            if config_keywords:
                all_lines = config_result.output.splitlines()
                relevant = [
                    line
                    for line in all_lines
                    if any(kw.lower() in line.lower() for kw in config_keywords)
                ]
                running_config = "\n".join(relevant[-80:])
            else:
                running_config = config_result.output[-3000:]
    except TelnetError:
        pass

    try:
        iface_result = session.send_command("display ip interface brief")
        if not iface_result.errored:
            interface_brief = iface_result.output[-2000:]
    except TelnetError:
        pass

    return generate_llm_context(
        device_name=session.name,
        device_type=device_type,
        software_version=software_version,
        failed_command=failed_command,
        error_text=error_text,
        current_view=current_view,
        running_config_snippet=running_config,
        interface_brief=interface_brief,
        recent_output=recent_output,
        rule_diagnosis=rule_diag.to_dict() if rule_diag else None,
        experiment_description=experiment_description,
    )


def _extract_config_keywords(command: str) -> list[str]:
    """从失败命令中提取配置关键字，用于过滤 running-config 相关行。

    例如：
    - "ospf 1 area 0.0.0.0" → ["ospf", "area"]
    - "interface GigabitEthernet0/0/0" → ["interface", "GigabitEthernet0/0/0"]
    - "vlan 10" → ["vlan"]
    - "ip address 10.0.0.1 255.255.255.0" → ["ip address"]
    """
    # 复合关键字（两个词一起）
    compound_keywords = [
        "ip address",
        "ip route",
        "ospf enable",
        "dhcp enable",
        "stp enable",
        "port link-type",
        "port default",
        "port trunk",
        "port access",
        "undo ",
        "display ",
        "reset ",
    ]
    keywords: list[str] = []
    cmd_lower = command.lower()

    for ck in compound_keywords:
        if ck in cmd_lower:
            keywords.append(ck.split()[0])

    # 单关键字
    single_keywords = [
        "ospf",
        "area",
        "vlan",
        "interface",
        "router-id",
        "network",
        "bgp",
        "rip",
        "static",
        "acl",
        "nat",
        "dhcp",
        "stp",
        "lacp",
        "trunk",
        "access",
        "hybrid",
        "GigabitEthernet",
        "LoopBack",
        "Vlanif",
        "Ethernet",
        "Serial",
        "route-policy",
        "prefix",
        "peer",
    ]
    for sk in single_keywords:
        if sk.lower() in cmd_lower:
            keywords.append(sk)

    # 去重
    seen: set[str] = set()
    result = []
    for kw in keywords:
        if kw.lower() not in seen:
            result.append(kw)
            seen.add(kw.lower())
    return result


def _parse_ip_interface_brief(output: str) -> list[dict[str, str]]:
    """解析 ``display ip interface brief`` 的输出。

    输出示例::

        Interface                         IP Address/Mask    Physical   Protocol
        GigabitEthernet0/0/0              10.0.12.1/30       up         up
        GigabitEthernet0/0/1              unassigned         up         up
        NULL0                             unassigned         up         up(s)
    """
    interfaces: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.rstrip()
        if not line or line.lower().startswith("interface") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        ifname, ip_mask, physical, protocol = parts[0], parts[1], parts[2], parts[3]
        interfaces.append(
            {
                "interface": ifname,
                "ip": ip_mask if ip_mask != "unassigned" else "",
                "mask": ip_mask.split("/")[1] if "/" in ip_mask else "",
                "physical": physical,
                "protocol": protocol,
            }
        )
    return interfaces


def _parse_routing_table(output: str) -> list[dict[str, str]]:
    """解析 ``display ip routing-table`` 输出。"""
    routes: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.rstrip()
        if not line or line.lower().startswith("destination") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) < 5 or not _looks_like_ip(parts[0]):
            continue
        routes.append(
            {
                "destination": parts[0],
                "mask": parts[1],
                "protocol": parts[2] if len(parts) > 2 else "",
                "preference": parts[3] if len(parts) > 3 else "",
                "cost": parts[4] if len(parts) > 4 else "",
                "next_hop": parts[5] if len(parts) > 5 else "",
                "interface": parts[6] if len(parts) > 6 else "",
            }
        )
    return routes


def _parse_ospf_peer_brief(output: str) -> list[dict[str, str]]:
    """解析 ``display ospf peer brief`` 输出。"""
    peers: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.rstrip()
        if not line or line.lower().startswith("neighbor id") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        peers.append(
            {
                "neighbor_id": parts[0],
                "priority": parts[1] if len(parts) > 1 else "",
                "state": parts[2] if len(parts) > 2 else "",
                "interface": parts[-1] if parts else "",
            }
        )
    return peers


def _parse_ping_loss(output: str) -> float:
    """从 ``ping`` 输出中提取丢包率。返回 0-100，未匹配返回 -1。"""
    import re

    m = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
    if m:
        return float(m.group(1))
    # 华为设备常用中文格式："丢包率 0%" 之类
    m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", output, re.IGNORECASE)
    return float(m.group(1)) if m else -1.0


def _looks_like_ip(s: str) -> bool:
    return s.count(".") == 3 and all(p.isdigit() for p in s.split("."))


# ---------------------------------------------------------------------- #
# 拓扑加载辅助函数
# ---------------------------------------------------------------------- #
def _plan_subnets(topo: Topology) -> dict[str, Any]:
    """根据拓扑结构给出 IP 规划建议。

    规则：

    - 路由器之间的 P2P 链路用 ``10.1.<x>.<y>.0/30``（x, y 取两台设备在拓扑中的顺序号）
    - 路由器 ↔ PC 的 LAN 段用 ``192.168.<seg>.0/24``（seg 按发现顺序递增）
    - 路由器 Loopback 用 ``1.1.1.<idx>/32``，``2.2.2.<idx>/32``（按设备名排序）
    """
    devices = list(topo.devices)
    routers = [d for d in devices if d.device_type == "router"]
    # Router ID 按"路由器在拓扑中的出现顺序"递增
    name_to_router_idx = {r.name: i + 1 for i, r in enumerate(routers)}
    # P2P 互联子网用"设备在 topo.devices 中的全局顺序"
    name_to_idx = {d.name: i + 1 for i, d in enumerate(devices)}

    p2p_links: list[dict[str, Any]] = []
    p2p_seen: set[tuple[str, str]] = set()
    lan_segments: list[dict[str, Any]] = []
    loopback_ips: dict[str, str] = {}
    used_lan_segs: set[int] = set()

    for r in routers:
        idx = name_to_router_idx[r.name]
        loopback_ips[r.name] = f"{idx}.{idx}.{idx}.{idx}/32"

    for conn in topo.connections:
        src_dev = topo.device_by_name(conn.src_device)
        dst_dev = topo.device_by_name(conn.dst_device)
        if not src_dev or not dst_dev:
            continue
        key = tuple(sorted([conn.src_device, conn.dst_device]))
        if key in p2p_seen:
            continue
        p2p_seen.add(key)
        # 路由器 ↔ 路由器 → P2P /30
        if src_dev.device_type == "router" and dst_dev.device_type == "router":
            i, j = sorted([name_to_idx[conn.src_device], name_to_idx[conn.dst_device]])
            subnet = f"10.1.{i}.{j * 4}/30"
            p2p_links.append(
                {
                    "devices": [conn.src_device, conn.dst_device],
                    "interfaces": [conn.src_interface, conn.dst_interface],
                    "subnet": subnet,
                    "addresses": {
                        conn.src_device: f"10.1.{i}.{j * 4 + 1}/30",
                        conn.dst_device: f"10.1.{i}.{j * 4 + 2}/30",
                    },
                }
            )
        # 路由器 ↔ PC → LAN /24
        elif (src_dev.device_type == "router" and dst_dev.device_type == "pc") or (
            dst_dev.device_type == "router" and src_dev.device_type == "pc"
        ):
            seg = 1
            while seg in used_lan_segs:
                seg += 1
            used_lan_segs.add(seg)
            router = conn.src_device if src_dev.device_type == "router" else conn.dst_device
            pc = conn.dst_device if src_dev.device_type == "router" else conn.src_device
            router_if = (
                conn.src_interface if src_dev.device_type == "router" else conn.dst_interface
            )
            lan_segments.append(
                {
                    "router": router,
                    "router_interface": router_if,
                    "pc": pc,
                    "subnet": f"192.168.{seg}.0/24",
                    "addresses": {
                        router: f"192.168.{seg}.254/24",
                        pc: f"192.168.{seg}.1/24",
                    },
                }
            )

    return {
        "p2p_links": p2p_links,
        "lan_segments": lan_segments,
        "loopback_ips": loopback_ips,
    }


# ---------------------------------------------------------------------- #
# 拓扑加载辅助函数（连接 + 报告）
# ---------------------------------------------------------------------- #
def _connect_or_record(
    *,
    dev: Device,
    sm: SessionManager,
    host: str,
    reachable_map: dict[int, bool],
) -> dict[str, Any]:
    """根据设备探测结果，自动建连或记录到 pending 列表。"""
    port = dev.console_port
    # PC / Cloud 等无 console 端口
    if port <= 1:
        reason = "无 console 端口" if port == 0 else "Cloud 设备（占位端口 1）"
        return {
            "name": dev.name,
            "port": port,
            "device_type": dev.device_type,
            "reason": reason,
        }
    # 设备未启动
    if not reachable_map.get(port, False):
        return {
            "name": dev.name,
            "port": port,
            "device_type": dev.device_type,
            "reason": "eNSP 设备未启动（TCP 不可达）",
        }
    # 已启动 → 尝试建连
    try:
        session = sm.create(host=host, port=port, name=dev.name, reuse=True, auto_refresh=True)
    except TelnetError as exc:
        return {
            "name": dev.name,
            "port": port,
            "device_type": dev.device_type,
            "reason": f"连接失败: {exc}",
        }
    return {
        "status": "connected",
        "name": dev.name,
        "port": port,
        "session_id": session.session_id,
        "session_status": session.status.value,
    }


def _render_ensp_gui_instructions(path: str) -> str:
    """生成 eNSP GUI 启动 .topo 的步骤说明。"""
    abs_path = os.path.abspath(path)
    return (
        "检测到部分设备未启动，请在 eNSP GUI 中按以下步骤操作：\n"
        "\n"
        f"  1. 双击桌面 eNSP 快捷方式启动主程序；\n"
        f"  2. 菜单 → 文件 → 打开（或 Ctrl+O）选择拓扑：\n"
        f"     {abs_path}\n"
        "  3. 工具 → 启动所有设备（或 Ctrl+Alt+A）\n"
        "  4. 等待指示灯变绿后，回到 MCP 客户端再次调用 ensp_load_topology 补齐会话。\n"
        "\n"
        "快捷键：\n"
        "  - Ctrl+O          打开 .topo 文件\n"
        "  - Ctrl+Alt+A      启动所有未启动设备\n"
        "  - Ctrl+Alt+C      关闭所有已启动设备\n"
        "\n"
        "故障排查：\n"
        "  - 启动报 41 错误 → 工具 → 注册设备，全勾选并确认；\n"
        "  - VirtualBox 报 40 错误 → 关闭 eNSP，删除安装目录下 VBoxServer 下的\n"
        "    AR_Base / WLAN_*_Base 目录后重启 eNSP；\n"
        "  - 启动后无 telnet 端口 → 检查 WinPcap / Wireshark / VirtualBox 是否完整安装。\n"
    )


def _render_load_topology_report(
    *,
    topo: Topology,
    path: str,
    connected: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    reachable_map: dict[int, bool],
    reachable_errors: dict[int, str],
    subnet_plan: dict[str, Any],
) -> str:
    """生成 ensp_load_topology 的人类可读 Markdown 报告。"""
    lines: list[str] = []
    lines.append(f"# 拓扑加载报告：{topo.name}")
    lines.append("")
    lines.append(f"- 源文件：`{os.path.abspath(path)}`")
    lines.append(f"- 格式：`{topo.source_format}`")
    lines.append(f"- 设备数：**{len(topo.devices)}**")
    lines.append(f"- 连线数：**{len(topo.connections)}**")
    lines.append("")
    lines.append("## 设备清单")
    lines.append("")
    lines.append("| 名称 | 型号 | 类型 | console 端口 | 状态 |")
    lines.append("|---|---|---|---|---|")
    for dev in topo.devices:
        port = dev.console_port
        if port <= 1:
            status = "非路由器（无需 console）"
        elif any(c["name"] == dev.name for c in connected):
            status = "已建连（session 已就绪）"
        else:
            err = reachable_errors.get(port, "")
            status = "未启动" if not reachable_map.get(port) else f"未建连（{err}）"
        lines.append(f"| {dev.name} | {dev.model or '-'} | {dev.device_type} | {port} | {status} |")
    lines.append("")
    lines.append("## 连线清单")
    lines.append("")
    if topo.connections:
        lines.append("| 源 | 源接口 | 目标 | 目标接口 | 链路 |")
        lines.append("|---|---|---|---|---|")
        for c in topo.connections:
            lines.append(
                f"| {c.src_device} | {c.src_interface or '-'} | "
                f"{c.dst_device} | {c.dst_interface or '-'} | {c.link_type or '-'} |"
            )
    else:
        lines.append("（无连线）")
    lines.append("")
    if subnet_plan.get("p2p_links"):
        lines.append("## P2P 互联子网建议")
        lines.append("")
        lines.append("| 设备 A | 设备 B | 子网 | A 地址 | B 地址 |")
        lines.append("|---|---|---|---|---|")
        for p in subnet_plan["p2p_links"]:
            devs = p["devices"]
            addrs = p["addresses"]
            lines.append(
                f"| {devs[0]} | {devs[1]} | {p['subnet']} | {addrs[devs[0]]} | {addrs[devs[1]]} |"
            )
        lines.append("")
    if subnet_plan.get("lan_segments"):
        lines.append("## LAN 段建议（路由器 ↔ PC）")
        lines.append("")
        lines.append("| 路由器 | 接口 | PC | 子网 | 路由器地址 | PC 地址 |")
        lines.append("|---|---|---|---|---|---|")
        for seg in subnet_plan["lan_segments"]:
            addrs = seg["addresses"]
            lines.append(
                f"| {seg['router']} | {seg['router_interface']} | {seg['pc']} | {seg['subnet']} | "
                f"{addrs[seg['router']]} | {addrs[seg['pc']]} |"
            )
        lines.append("")
    if subnet_plan.get("loopback_ips"):
        lines.append("## Loopback IP 建议")
        lines.append("")
        for name, ip in subnet_plan["loopback_ips"].items():
            lines.append(f"- {name}: `{ip}`")
        lines.append("")
    if connected:
        lines.append("## 已建连会话")
        lines.append("")
        for c in connected:
            lines.append(
                f"- **{c['name']}** → port `{c['port']}` session_id=`{c.get('session_id', '?')}`"
            )
        lines.append("")
    if pending:
        lines.append("## 待启动设备")
        lines.append("")
        for p in pending:
            lines.append(
                f"- **{p['name']}**（{p.get('device_type', '-')}）— {p.get('reason', '原因未知')}"
            )
        lines.append("")
        lines.append(_render_ensp_gui_instructions(path))
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def main() -> None:
    """CLI 入口：使用 stdio 传输运行 MCP Server。"""
    logging.basicConfig(
        level=os.environ.get("GRBJ_ENSP_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = create_mcp_server()
    logger.info("启动 %s （stdio 传输）", SERVER_NAME)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
