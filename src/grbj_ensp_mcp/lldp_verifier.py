"""基于 LLDP 的 eNSP 真实拓扑发现与 ``.topo`` 文件交叉验证。

**核心目标**：解决「``ensp_parse_topology`` 解析出来的连线 vs eNSP GUI 实际接的线
可能不一致」这类问题——例如学生保存 .topo 后又在 GUI 里手动接了几根线、
或老拓扑里把 AR1 的 GE0/0/0 错标成 GE0/0/1。

**双源真相**：

- ``.topo`` 是「设计图」，由 eNSP GUI 拖拽时生成，受人工错误影响
- LLDP 是「物理事实」，通过设备间互相通告接口信息得到，谁对谁错一目了然

本模块提供：

- :class:`LLDPNeighbor`：单条 LLDP 邻居通告
- :func:`parse_lldp_neighbor_brief`：解析 ``display lldp neighbor brief`` 输出
- :func:`parse_lldp_neighbor`：解析 ``display lldp neighbor`` 详细输出
- :class:`LLDPScanResult`：单设备的邻居列表 + 原始输出
- :func:`normalize_interface`：接口名归一化（GE0/0/0 ↔ GigabitEthernet0/0/0）
- :class:`TopologyComparison`：拓扑与 LLDP 的差异比对结果
- :func:`compare_topology_with_lldp`：执行双向比对并生成 Markdown 报告

**典型工作流**（由 MCP 工具层编排）::

    ensp_enable_lldp(session)              # 在所有目标设备上开 LLDP
    ensp_verify_lldp_neighbors(session)    # 采集每个设备的邻居
    ensp_cross_validate_topology(           # 与 .topo 解析结果对比
        topo_path="xxx.topo",
        lldp_results=[...],
    )
    # → matched / topo_only / lldp_only / mismatched

华为 eNSP 设备 LLDP 关键事实：

- 全局开启：``system-view`` → ``lldp enable``
- 设备类型（Router / Switch）默认就会在所有接口发 LLDP
- 邻居显示命令：``display lldp neighbor``（详细） / ``display lldp neighbor brief``（简表）
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .topo_parser import Topology

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #
@dataclass
class LLDPNeighbor:
    """单条 LLDP 邻居通告。

    字段含义：

    - ``local_device``：本地设备名（调用方填入，因为设备回显里通常不报自己的名字）
    - ``local_interface``：本地出接口（设备回显原文，已归一化）
    - ``remote_chassis_id``：对端设备 MAC（LLDP 协议层 ID）
    - ``remote_system_name``：对端 sysname（设备回显里可能为空）
    - ``remote_device``：对端设备名（来自 system-name；MCP 工具层负责匹配到拓扑）
    - ``remote_interface``：对端接口（设备回显原文，已归一化）
    - ``ttl`` / ``age``：TTL 与老化时间（秒），用于判断链路新鲜度
    """

    local_device: str = ""
    local_interface: str = ""
    remote_chassis_id: str = ""
    remote_system_name: str = ""
    remote_device: str = ""
    remote_interface: str = ""
    ttl: str = ""
    age: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "local_device": self.local_device,
            "local_interface": self.local_interface,
            "remote_chassis_id": self.remote_chassis_id,
            "remote_system_name": self.remote_system_name,
            "remote_device": self.remote_device,
            "remote_interface": self.remote_interface,
            "ttl": self.ttl,
            "age": self.age,
        }


@dataclass
class LLDPScanResult:
    """单设备的 LLDP 扫描结果。"""

    device: str
    neighbors: list[LLDPNeighbor] = field(default_factory=list)
    raw_output: str = ""
    errored: bool = False
    error_message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "neighbors": [n.to_dict() for n in self.neighbors],
            "count": len(self.neighbors),
            "raw_output": self.raw_output[-2000:],  # 截断避免返回过大
            "errored": self.errored,
            "error_message": self.error_message,
        }


@dataclass
class TopologyComparison:
    """``.topo`` 与 LLDP 双向比对结果。

    每条记录都是一条「无向边」，用两个端点 ``(device, interface)`` 表示。
    归一化规则：把接口名统一到 ``GigabitEthernet0/0/0`` / ``Ethernet0/0/0`` 等全称形式，
    然后做端点匹配（顺序无关）。
    """

    matched: list[dict[str, str]] = field(default_factory=list)
    """``matched``：``.topo`` 和 LLDP 都报告了这条边。"""

    topo_only: list[dict[str, str]] = field(default_factory=list)
    """``topo_only``：``.topo`` 说有线但 LLDP 看不到——线可能没接 / 对端设备未启。"""

    lldp_only: list[dict[str, str]] = field(default_factory=list)
    """``lldp_only``：物理上接了但 ``.topo`` 没记——拓扑图过时。"""

    mismatched: list[dict[str, str]] = field(default_factory=list)
    """``mismatched``：两端都报告了连线但接口号对不上——拓扑图错位。"""

    notes: list[str] = field(default_factory=list)
    """比对过程中遇到的提示信息（如设备名匹配歧义、LLDP 未使能警告等）。"""

    def to_dict(self) -> dict[str, object]:
        return {
            "matched": self.matched,
            "topo_only": self.topo_only,
            "lldp_only": self.lldp_only,
            "mismatched": self.mismatched,
            "summary": {
                "matched_count": len(self.matched),
                "topo_only_count": len(self.topo_only),
                "lldp_only_count": len(self.lldp_only),
                "mismatched_count": len(self.mismatched),
            },
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------- #
# 接口名归一化
# ---------------------------------------------------------------------- #
_INTERFACE_PREFIX_MAP: dict[str, str] = {
    # 简写 → 全称（按"最长前缀优先"匹配）
    "GigabitEthernet": "GigabitEthernet",
    "GigabitEth": "GigabitEthernet",
    "Gig": "GigabitEthernet",
    "GE": "GigabitEthernet",
    "TenGigabitEthernet": "TenGigabitEthernet",
    "XGE": "TenGigabitEthernet",
    "XGigabitEthernet": "TenGigabitEthernet",
    "Ethernet": "Ethernet",
    "Eth": "Ethernet",
    "Eth-Trunk": "Eth-Trunk",
    "LoopBack": "LoopBack",
    "Loopback": "LoopBack",
    "Loop": "LoopBack",
    "Vlanif": "Vlanif",
    "MEth": "MEth",
    "Serial": "Serial",
    "Serial1/0/0": "Serial",
}

# 接口前缀归一化后的"接口族"识别（用于把 GE0/0/0 和 GigabitEthernet0/0/0 视为同类）
_INTERFACE_FAMILY_MAP: dict[str, str] = {
    "GigabitEthernet": "GigabitEthernet",
    "TenGigabitEthernet": "TenGigabitEthernet",
    "Ethernet": "Ethernet",
    "Eth-Trunk": "Eth-Trunk",
    "LoopBack": "LoopBack",
    "Vlanif": "Vlanif",
    "MEth": "MEth",
    "Serial": "Serial",
}


def normalize_interface(name: str) -> str:
    """把接口名归一化为「全称 + 数字段」形式，便于跨设备比对。

    输入兼容形式（举例）::

        "GE0/0/0"          → "GigabitEthernet0/0/0"
        "GigabitEthernet0/0/0" → "GigabitEthernet0/0/0"
        "GigabitEthernet 0/0/0" → "GigabitEthernet0/0/0"
        "GE 0/0/0"         → "GigabitEthernet0/0/0"
        "LoopBack0"        → "LoopBack0"
        "Loopback 0"       → "LoopBack0"
        "Eth-Trunk1"       → "Eth-Trunk1"
        "Vlanif 10"        → "Vlanif10"

    归一化策略：

    1. 把简写前缀（``GE``/``Eth``/``Loop``/``Gigabit``）映射为全称前缀
    2. 去掉前缀与数字段之间的所有空白
    3. 对 LoopBack 类，按已知经验「B 大写」统一为 ``LoopBack``（不是 ``Loopback``）
    4. 数字段的格式保持原样（GE0/0/0 ↔ 0/0/0/0 等特殊情况不在本函数内归一，
       留给上层按设备规则处理）

    无法识别的接口名原样返回（避免错误归一化）。
    """
    if not name:
        return ""
    raw = name.strip()
    if not raw:
        return ""

    # 分离前缀与数字段（首个数字字符起）
    m = re.match(r"^([A-Za-z\-]+)\s*(.*)$", raw)
    if not m:
        return raw
    prefix_raw, rest = m.group(1), m.group(2).strip()
    # 去掉 rest 中的所有空白（"0/0/0" 内部不该有空白）
    rest_compact = re.sub(r"\s+", "", rest)
    if not rest_compact:
        return raw

    # 找最长的已知前缀（避免 ``Gig`` 被 ``G`` 截断）
    resolved_prefix = ""
    for short, full in sorted(
        _INTERFACE_PREFIX_MAP.items(), key=lambda kv: -len(kv[0])
    ):
        if prefix_raw == short or prefix_raw.lower() == short.lower():
            resolved_prefix = full
            break
    if not resolved_prefix:
        # 没匹配到任何已知前缀 → 原样返回（保留大小写信息）
        return f"{prefix_raw}{rest_compact}"

    return f"{resolved_prefix}{rest_compact}"


def interfaces_match(a: str, b: str) -> bool:
    """判断两个接口名是否指向同一接口（接口名归一化后比较）。"""
    return normalize_interface(a) == normalize_interface(b)


def _interface_family(iface: str) -> str:
    """按接口前缀识别"接口族"，用于按"接口族 + 数字"做端点比对。

    例::

        GE0/0/0       → "GigabitEthernet"
        GigabitEthernet0/0/0 → "GigabitEthernet"
        Eth0/0/0      → "Ethernet"

    无法识别时返回空串，调用方应退回"原始字符串比较"。
    """
    norm = normalize_interface(iface)
    for prefix, family in sorted(
        _INTERFACE_FAMILY_MAP.items(), key=lambda kv: -len(kv[0])
    ):
        if norm.startswith(prefix):
            return family
    return ""


# ---------------------------------------------------------------------- #
# LLDP 输出解析
# ---------------------------------------------------------------------- #
# ``display lldp neighbor brief`` 输出格式（华为 VRP）::
#
#     Local Intf     | Neighbor Dev        | Neighbor Intf              | Aging
#     GE0/0/0        | AR2                 | GE0/0/1                    | 120
#     GE0/0/1        | SW1                 | GigabitEthernet0/0/24      | 100
#
# 注意：
# - 字段分隔靠多空格对齐（不是 | 字符）
# - 部分版本带表头，部分不带；遇到 "---- More ----" 也属正常

_BRIEF_HEADER_PATTERNS = (
    r"Local\s*Intf",  # 表头
    r"Neighbor\s*Dev",
    r"Neighbor\s*Intf",
    r"LLDP\s+neighbor-information",
    r"^\s*-{3,}\s*$",  # 横线分隔
)


def parse_lldp_neighbor_brief(
    output: str,
    *,
    local_device: str = "",
) -> list[LLDPNeighbor]:
    """解析 ``display lldp neighbor brief`` 的输出。

    参数：
        output：设备回显原文
        local_device：本地设备名（用于回填 ``local_device`` 字段；不回显中取）

    返回：
        LLDPNeighbor 列表。

    解析策略：

    1. 跳过表头、空行、``---- More ----``、``<...>`` / ``[...]`` 提示符行
    2. 用多空格 ``re.split(r"\\s{2,}", line)`` 拆字段
    3. 字段数 ≥ 3 视为数据行（第 4 列 Aging 可能缺）
    4. 设备名取第 1 列，接口名取第 2 列
    """
    neighbors: list[LLDPNeighbor] = []
    if not output:
        return neighbors

    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # 跳过提示符
        s = line.strip()
        if not s:
            continue
        # 跳过 "<Huawei>" / "[Huawei]" 提示符行
        if s.startswith(("<", "[", "-")) and (s.endswith((">", "]")) or not s):
            continue
        if "More" in s and "----" in s:
            continue
        # 跳过明显的表头行（包含 "Local Intf" / "Neighbor Dev" 等）
        if any(re.search(pat, s, re.IGNORECASE) for pat in _BRIEF_HEADER_PATTERNS):
            continue
        # 跳过纯提示性文字（如 "Total neighbors displayed: 1" / "LLDP is not enabled"）
        if ":" in s and "LLDP" in s and not re.search(r"\s{2,}", s):
            continue
        # 跳过结尾不带多空格分隔的杂项
        if not re.search(r"\s{2,}", s):
            continue

        parts = re.split(r"\s{2,}", s)
        if len(parts) < 3:
            continue
        # 期望列：local_interface | remote_device | remote_interface [| age]
        local_iface = normalize_interface(parts[0])
        remote_dev = parts[1].strip()
        remote_iface = normalize_interface(parts[2])
        age = parts[3].strip() if len(parts) >= 4 else ""

        if not local_iface or not remote_iface:
            continue

        neighbors.append(
            LLDPNeighbor(
                local_device=local_device,
                local_interface=local_iface,
                remote_chassis_id="",  # brief 格式不含
                remote_system_name=remote_dev,
                remote_device=remote_dev,
                remote_interface=remote_iface,
                ttl="",
                age=age,
            )
        )

    return neighbors


# ``display lldp neighbor`` 详细输出（每接口一段）::
#
#     LLDP neighbor-information of port GigabitEthernet0/0/0:
#     Neighbor index : 1
#     Update time    : 0 days, 0 hours, 1 minutes, 30 seconds
#     Device type    : AR2240
#     Device ID      : 0030-1122-3344
#     Port ID        : GigabitEthernet0/0/1
#     System name    : AR2
#     ...
#
# 段间用空行 / "LLDP neighbor-information of port" / 提示符分隔。

_DETAIL_PORT_HEADER_RE = re.compile(r"LLDP\s+neighbor-information\s+of\s+port\s+(\S+):", re.I)
_DETAIL_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z\s]*?)\s*:\s*(.+?)\s*$")


def parse_lldp_neighbor(
    output: str,
    *,
    local_device: str = "",
) -> list[LLDPNeighbor]:
    """解析 ``display lldp neighbor`` 的详细输出。

    每段以 ``LLDP neighbor-information of port <iface>:`` 开头，直到下一个
    同类段或提示符结束。每段含若干 ``Key: Value`` 字段。

    至少需要 ``Port ID`` 和 ``System name`` 才会构造一条 LLDPNeighbor，
    否则视为无效段（跳过）。
    """
    neighbors: list[LLDPNeighbor] = []
    if not output:
        return neighbors

    state = {"current_port": "", "current_fields": {}}

    def _flush() -> None:
        if not state["current_port"]:
            return
        current_fields: dict[str, str] = state["current_fields"]
        port_id = current_fields.get("Port ID", "") or current_fields.get("Port id", "")
        system_name = (
            current_fields.get("System name", "")
            or current_fields.get("System Name", "")
            or current_fields.get("Systemname", "")
        )
        if not port_id:
            state["current_port"] = ""
            state["current_fields"] = {}
            return
        neighbors.append(
            LLDPNeighbor(
                local_device=local_device,
                local_interface=normalize_interface(state["current_port"]),
                remote_chassis_id=current_fields.get("Device ID", "")
                or current_fields.get("Chassis ID", ""),
                remote_system_name=system_name,
                remote_device=system_name,
                remote_interface=normalize_interface(port_id),
                ttl=current_fields.get("TTL", ""),
                age=current_fields.get(
                    "Update time", ""
                )  # 老化用 Update time 表示
                or current_fields.get("Aging", ""),
            )
        )
        state["current_port"] = ""
        state["current_fields"] = {}

    for line in output.splitlines():
        line_stripped = line.strip()
        # 段头
        m = _DETAIL_PORT_HEADER_RE.search(line_stripped)
        if m:
            _flush()
            state["current_port"] = m.group(1).strip()
            continue
        # 空行：段间分隔；可能正好是一段结束
        if not line_stripped:
            _flush()
            continue
        # 提示符行：可能是整段命令的回显结尾
        if line_stripped.startswith(("<", "[", "-")) and line_stripped.endswith((">", "]")):
            _flush()
            continue
        if "More" in line_stripped and "----" in line_stripped:
            continue
        # 字段行
        m = _DETAIL_FIELD_RE.match(line_stripped)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            state["current_fields"][key] = value
            continue
        # 未知行：忽略（但不清空 current，保留宽容性）

    _flush()
    return neighbors


def parse_lldp_output(
    output: str,
    *,
    local_device: str = "",
    brief: bool = True,
    force: bool = False,
) -> list[LLDPNeighbor]:
    """通用入口：brief 与 detail 自动识别。

    参数：
        brief：默认 ``True`` 时按 brief 解析；``False`` 时按 detail 解析
        force：``True`` 时跳过自动识别，严格按 ``brief`` 参数走（默认 ``False``）

    自动识别策略：

    - 输出含 ``LLDP neighbor-information of port`` → detail
    - 否则 → brief

    注意：默认 ``brief=True`` 时也会先做自动识别——只有当 ``force=True`` 时
    才严格按 brief 解析。这样调用方既能用 ``brief=True`` 拿到 brief 结果
    （前提是 force=True），也能用 ``brief=True`` 让函数自动判断输出格式。
    """
    if not output:
        return []
    if force:
        return parse_lldp_neighbor_brief(output, local_device=local_device) if brief else parse_lldp_neighbor(output, local_device=local_device)
    # 自动识别
    if "LLDP neighbor-information of port" in output:
        return parse_lldp_neighbor(output, local_device=local_device)
    return parse_lldp_neighbor_brief(output, local_device=local_device)


# ---------------------------------------------------------------------- #
# 未使能 LLDP 检测
# ---------------------------------------------------------------------- #
# 华为 eNSP / VRP 在 LLDP 未使能时 display 命令的典型回显：
# - 英文: "LLDP is not enabled"
# - 英文: "Error: LLDP is not enabled globally"
# - 中文: "LLDP功能未使能" / "LLDP 未使能" / "LLDP全局未使能"
#
# 匹配策略：行内任意位置匹配任一关键字，且大小写不敏感（英文回显里
# 偶有 "Lldp is not enabled" 这种小写形式）。
_NOT_ENABLED_PATTERNS = (
    re.compile(r"LLDP\s*功能?\s*未\s*使能", re.IGNORECASE),
    re.compile(r"LLDP\s*未\s*使能", re.IGNORECASE),
    re.compile(r"LLDP\s+is\s+not\s+enabled", re.IGNORECASE),
    re.compile(r"LLDP\s+is\s+disabled", re.IGNORECASE),
    re.compile(r"LLDP\s*全局未使能", re.IGNORECASE),
)


def looks_like_lldp_not_enabled(raw_output: str) -> bool:
    """检测设备回显是否表明 ``LLDP 未使能``。

    典型场景：

    - eNSP 部分镜像默认 LLDP 是开的，但 AR201 / 旧版 S5700 可能需要手动开
    - 用户从没调过 ``lldp enable`` → ``display lldp neighbor`` 返回错误或空表
    - ``display lldp neighbor brief`` 输出 ``LLDP is not enabled``

    与错误字段（``errored=True``）的区别：
    ``errored`` 是命令层直接报错（``Error:`` 行首）；本函数专门检测
    LLDP 协议的「未使能」状态。即使 ``errored=False``（命令没报错但
    输出含 "LLDP is not enabled"），本函数也会返回 ``True``。

    参数：
        raw_output：``display lldp neighbor`` / ``display lldp neighbor brief``
        命令的设备回显原文

    返回：
        ``True`` 表示回显含未使能特征，应触发自动使能逻辑
    """
    if not raw_output:
        return False
    return any(pat.search(raw_output) for pat in _NOT_ENABLED_PATTERNS)


# ---------------------------------------------------------------------- #
# 比对逻辑
# ---------------------------------------------------------------------- #
def _edge_key(device: str, iface: str) -> str:
    """构造端点键（用于边匹配前的端点归一化）。"""
    return f"{device}::{normalize_interface(iface)}"


def _link_key(endpoints: Iterable[tuple[str, str]]) -> frozenset[str]:
    """把"两个端点"组成无向边键：内部用 frozenset 包裹两端的端点键。"""
    return frozenset(_edge_key(d, i) for d, i in endpoints)


def _topo_edges(topo: Topology) -> list[tuple[frozenset[str], dict[str, str]]]:
    """把 ``.topo`` 的所有连线转换为 ``(无向边键, 详情)`` 列表。

    详情包含：
    - topo_src_device / topo_src_interface
    - topo_dst_device / topo_dst_interface
    - link_type
    """
    edges: list[tuple[frozenset[str], dict[str, str]]] = []
    for c in topo.connections:
        src_if = c.src_interface
        dst_if = c.dst_interface
        if not src_if or not dst_if:
            # 拓扑里 src_index/tarIndex 没解析出接口名 → 跳过（无法比对）
            continue
        endpoints = (
            (c.src_device, normalize_interface(src_if)),
            (c.dst_device, normalize_interface(dst_if)),
        )
        # 过滤掉"self-loop"（端点相同）的脏数据
        if endpoints[0][1] == endpoints[1][1] and endpoints[0][0] == endpoints[1][0]:
            continue
        key = _link_key(endpoints)
        detail = {
            "src_device": c.src_device,
            "src_interface": normalize_interface(src_if),
            "dst_device": c.dst_device,
            "dst_interface": normalize_interface(dst_if),
            "link_type": c.link_type,
        }
        edges.append((key, detail))
    return edges


def _lldp_edges(
    lldp_results: Iterable[LLDPScanResult],
    *,
    device_aliases: dict[str, str] | None = None,
) -> list[tuple[frozenset[str], dict[str, str]]]:
    """把所有设备的 LLDP 扫描结果汇总为边列表。

    ``device_aliases``：把 LLDP 通告里的 ``system-name`` 映射到 ``.topo`` 里的设备名。
    例如 AR2 → R2。若不提供，则直接用 ``local_device`` 和 ``remote_system_name``。
    """
    aliases = device_aliases or {}

    def _resolve(name: str) -> str:
        return aliases.get(name, name)

    edges: list[tuple[frozenset[str], dict[str, str]]] = []
    seen_keys: set[frozenset[str]] = set()
    for scan in lldp_results:
        local_dev = _resolve(scan.device)
        for n in scan.neighbors:
            remote_dev = _resolve(n.remote_device or n.remote_system_name)
            if not local_dev or not remote_dev:
                continue
            endpoints = (
                (local_dev, n.local_interface),
                (remote_dev, n.remote_interface),
            )
            if endpoints[0][0] == endpoints[1][0] and endpoints[0][1] == endpoints[1][1]:
                continue
            key = _link_key(endpoints)
            if key in seen_keys:
                # 同一物理边被双向通告（A→B 和 B→A）只记一次
                continue
            seen_keys.add(key)
            detail = {
                "src_device": local_dev,
                "src_interface": n.local_interface,
                "dst_device": remote_dev,
                "dst_interface": n.remote_interface,
                "remote_system_name": n.remote_system_name,
                "remote_chassis_id": n.remote_chassis_id,
            }
            edges.append((key, detail))
    return edges


def compare_topology_with_lldp(
    topo: Topology,
    lldp_results: Iterable[LLDPScanResult],
    *,
    device_aliases: dict[str, str] | None = None,
) -> TopologyComparison:
    """把 ``.topo`` 与 LLDP 扫描结果双向比对，输出差异报告。

    参数：
        topo：``ensp_parse_topology`` 解析出来的 :class:`Topology`
        lldp_results：每台设备调 ``ensp_verify_lldp_neighbors`` 收集的扫描结果列表
        device_aliases：可选，LLDP system-name → 拓扑设备名的别名映射

    返回：
        :class:`TopologyComparison`：

        - ``matched``：双方都认可的边（接口端点完全一致）
        - ``topo_only``：``.topo`` 有但 LLDP 没看到（线可能没接 / 对端设备未启）
        - ``lldp_only``：LLDP 看到但 ``.topo`` 没记（拓扑图过时）
        - ``mismatched``：端点有重合但接口号不同（拓扑图错位）
        - ``notes``：附加提示（设备名映射歧义 / LLDP 未使能警告等）
    """
    cmp = TopologyComparison()

    topo_edges = _topo_edges(topo)
    lldp_edges = _lldp_edges(lldp_results, device_aliases=device_aliases)
    topo_keys = {k for k, _ in topo_edges}
    lldp_keys = {k for k, _ in lldp_edges}
    topo_detail = dict(topo_edges)
    lldp_detail = dict(lldp_edges)

    # 1) 完全匹配（两端点完全一致）
    matched_keys = topo_keys & lldp_keys
    for k in sorted(matched_keys, key=lambda x: sorted(x)):
        cmp.matched.append(
            {
                **topo_detail[k],
                "match_status": "matched",
            }
        )

    # 2) 端点部分匹配（拓扑说 (A,if_a)-(B,if_b)，LLDP 说 (A,if_a')-(B,if_b')，
    #    两端设备对得上但至少一个接口号错位 → mismatched）
    topo_by_devices: dict[tuple[str, str], list[tuple[frozenset[str], dict[str, str]]] ] = {}
    for k, d in topo_edges:
        pair = tuple(sorted([d["src_device"], d["dst_device"]]))
        topo_by_devices.setdefault(pair, []).append((k, d))

    lldp_by_devices: dict[tuple[str, str], list[tuple[frozenset[str], dict[str, str]]] ] = {}
    for k, d in lldp_edges:
        pair = tuple(sorted([d["src_device"], d["dst_device"]]))
        lldp_by_devices.setdefault(pair, []).append((k, d))

    used_topo: set[frozenset[str]] = set(matched_keys)
    used_lldp: set[frozenset[str]] = set(matched_keys)

    for pair, topo_list in topo_by_devices.items():
        if pair not in lldp_by_devices:
            continue
        lldp_list = lldp_by_devices[pair]
        # 尝试把拓扑边与 LLDP 边配对（按端点键做最佳匹配）
        for topo_key, topo_d in topo_list:
            if topo_key in used_topo:
                continue
            for lldp_key, lldp_d in lldp_list:
                if lldp_key in used_lldp:
                    continue
                # 至少一个端点的接口号能匹配（设备对相同 + 至少一段接口相同），
                # 才视为"端点部分匹配"——否则交给 lldp_only / topo_only 处理
                if topo_d["src_interface"] == lldp_d["src_interface"] and (
                    topo_d["dst_device"] == lldp_d["src_device"]
                ):
                    # topo src ↔ lldp src 一致，但方向反过来（无所谓），且另一端也对
                    used_topo.add(topo_key)
                    used_lldp.add(lldp_key)
                    cmp.mismatched.append(
                        {
                            **topo_d,
                            "lldp_src_device": lldp_d["src_device"],
                            "lldp_src_interface": lldp_d["src_interface"],
                            "lldp_dst_device": lldp_d["dst_device"],
                            "lldp_dst_interface": lldp_d["dst_interface"],
                            "match_status": "interface_mismatch",
                        }
                    )
                    break
                # 双向端点接口各自匹配（不强求方向）
                topo_endpoints = {
                    (topo_d["src_device"], topo_d["src_interface"]),
                    (topo_d["dst_device"], topo_d["dst_interface"]),
                }
                lldp_endpoints = {
                    (lldp_d["src_device"], lldp_d["src_interface"]),
                    (lldp_d["dst_device"], lldp_d["dst_interface"]),
                }
                common = topo_endpoints & lldp_endpoints
                if common and len(topo_endpoints) == 2 and len(lldp_endpoints) == 2:
                    # 至少一个端点匹配 + 端点数都是 2 + 设备对相同 → 另一端错位
                    used_topo.add(topo_key)
                    used_lldp.add(lldp_key)
                    diff_count = len(topo_endpoints - lldp_endpoints)
                    if diff_count > 0:
                        cmp.mismatched.append(
                            {
                                **topo_d,
                                "lldp_src_device": lldp_d["src_device"],
                                "lldp_src_interface": lldp_d["src_interface"],
                                "lldp_dst_device": lldp_d["dst_device"],
                                "lldp_dst_interface": lldp_d["dst_interface"],
                                "match_status": "interface_mismatch",
                            }
                        )
                        break

    # 3) 仅 topo（拓扑里有，LLDP 没看到）
    topo_only_keys = topo_keys - used_topo
    for k in sorted(topo_only_keys, key=lambda x: sorted(x)):
        cmp.topo_only.append(
            {
                **topo_detail[k],
                "match_status": "topo_only",
            }
        )

    # 4) 仅 LLDP（拓扑里没有的物理连线）
    lldp_only_keys = lldp_keys - used_lldp
    for k in sorted(lldp_only_keys, key=lambda x: sorted(x)):
        cmp.lldp_only.append(
            {
                **lldp_detail[k],
                "match_status": "lldp_only",
            }
        )

    # 5) 备注：LLDP 扫描为空 / 失败
    for scan in lldp_results:
        if scan.errored:
            cmp.notes.append(
                f"设备 {scan.device} 的 LLDP 采集失败：{scan.error_message}（可能未使能 LLDP）"
            )
        elif not scan.neighbors:
            cmp.notes.append(
                f"设备 {scan.device} 没看到任何 LLDP 邻居（可能被叫错名字 / 对端未启 / LLDP 未使能）"
            )

    # 6) 备注：拓扑里的设备名与 LLDP system-name 完全对不上
    topo_dev_names = {d.name for d in topo.devices}
    lldp_dev_names: set[str] = set()
    for scan in lldp_results:
        lldp_dev_names.add(scan.device)
        for n in scan.neighbors:
            if n.remote_device:
                lldp_dev_names.add(n.remote_device)
    missing_in_topo = lldp_dev_names - topo_dev_names
    if missing_in_topo and not device_aliases:
        cmp.notes.append(
            "LLDP 看到的下列设备名在 .topo 中找不到："
            + ", ".join(sorted(missing_in_topo))
            + "。可通过 device_aliases 参数做 system-name → 拓扑设备名映射。"
        )

    return cmp


# ---------------------------------------------------------------------- #
# Markdown 报告
# ---------------------------------------------------------------------- #
def render_comparison_markdown(cmp: TopologyComparison) -> str:
    """把 :class:`TopologyComparison` 渲染为人类可读的 Markdown 报告。"""
    lines: list[str] = []
    total = len(cmp.matched) + len(cmp.topo_only) + len(cmp.lldp_only) + len(cmp.mismatched)
    lines.append("# LLDP × .topo 拓扑交叉验证报告")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 总边数：**{total}**")
    lines.append(f"- 拓扑与 LLDP 一致：**{len(cmp.matched)}**")
    lines.append(f"- 仅 .topo 有：**{len(cmp.topo_only)}**")
    lines.append(f"- 仅 LLDP 有：**{len(cmp.lldp_only)}**")
    lines.append(f"- 端点错位（接口号不一致）：**{len(cmp.mismatched)}**")
    lines.append("")

    if cmp.matched:
        lines.append("## 一致（matched）")
        lines.append("")
        lines.append("| 设备 A | 接口 A | 设备 B | 接口 B | 链路类型 |")
        lines.append("|--------|--------|--------|--------|----------|")
        for m in cmp.matched:
            lines.append(
                f"| {m.get('src_device', '?')} | {m.get('src_interface', '?')} | "
                f"{m.get('dst_device', '?')} | {m.get('dst_interface', '?')} | "
                f"{m.get('link_type', '')} |"
            )
        lines.append("")

    if cmp.mismatched:
        lines.append("## 端点错位（mismatched）")
        lines.append("")
        lines.append(
            "拓扑文件与物理 LLDP 都看到了这条连线，但至少一个接口号对不上。"
            "通常是 .topo 在 eNSP GUI 里拖线时把接口画错了。"
        )
        lines.append("")
        lines.append(
            "| 拓扑 设备A | 拓扑 接口A | 拓扑 设备B | 拓扑 接口B | LLDP 设备A | LLDP 接口A | LLDP 设备B | LLDP 接口B |"
        )
        lines.append(
            "|-----------|-----------|-----------|-----------|-----------|-----------|-----------|-----------|"
        )
        for m in cmp.mismatched:
            lines.append(
                f"| {m.get('src_device', '?')} | {m.get('src_interface', '?')} | "
                f"{m.get('dst_device', '?')} | {m.get('dst_interface', '?')} | "
                f"{m.get('lldp_src_device', '?')} | {m.get('lldp_src_interface', '?')} | "
                f"{m.get('lldp_dst_device', '?')} | {m.get('lldp_dst_interface', '?')} |"
            )
        lines.append("")

    if cmp.topo_only:
        lines.append("## 仅 .topo 有（topo_only）")
        lines.append("")
        lines.append(
            "拓扑文件记录了这条边，但 LLDP 没看到。可能原因："
            "线缆没接 / 对端设备未启动 / 接口 shutdown / LLDP 未使能。"
        )
        lines.append("")
        lines.append("| 设备 A | 接口 A | 设备 B | 接口 B |")
        lines.append("|--------|--------|--------|--------|")
        for m in cmp.topo_only:
            lines.append(
                f"| {m.get('src_device', '?')} | {m.get('src_interface', '?')} | "
                f"{m.get('dst_device', '?')} | {m.get('dst_interface', '?')} |"
            )
        lines.append("")

    if cmp.lldp_only:
        lines.append("## 仅 LLDP 有（lldp_only）")
        lines.append("")
        lines.append(
            "物理上接了但拓扑文件没记录。建议在 eNSP GUI 中检查并保存 .topo，"
            "或在 cross_validate_topology 调用时通过 device_aliases 关联 system-name。"
        )
        lines.append("")
        lines.append("| 设备 A | 接口 A | 设备 B | 接口 B | 对端 sysname | 对端 Chassis ID |")
        lines.append("|--------|--------|--------|--------|-------------|-----------------|")
        for m in cmp.lldp_only:
            lines.append(
                f"| {m.get('src_device', '?')} | {m.get('src_interface', '?')} | "
                f"{m.get('dst_device', '?')} | {m.get('dst_interface', '?')} | "
                f"{m.get('remote_system_name', '')} | {m.get('remote_chassis_id', '')} |"
            )
        lines.append("")

    if cmp.notes:
        lines.append("## 备注")
        lines.append("")
        for n in cmp.notes:
            lines.append(f"- {n}")
        lines.append("")

    if total == 0:
        lines.append(
            "_未采集到任何连线数据——请确认 .topo 已解析、且各设备已成功执行 "
            "`ensp_verify_lldp_neighbors` 并启用 LLDP。_"
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 带自动使能的 LLDP 采集（核心编排函数）
# ---------------------------------------------------------------------- #
class _SessionLike(Protocol):
    """采集逻辑对 session 的最小接口契约。

    真实会话由 ``session_manager.Session`` 提供；测试时可用
    ``unittest.mock.MagicMock`` 构造兼容对象。
    """

    name: str

    def send_command(self, command: str, timeout: float = 10.0) -> Any: ...

    def send_commands(self, commands: list[str], stop_on_error: bool = True) -> list[Any]: ...


# 触发「自动使能 + 重试」的条件：命令层报错 OR 回显含未使能关键字
def _needs_auto_enable(result: Any, raw_output: str) -> bool:
    """判断一次采集结果是否需要自动使能 LLDP 后重试。"""
    if getattr(result, "errored", False):
        return True
    return looks_like_lldp_not_enabled(raw_output)


def _do_auto_enable(session: _SessionLike) -> dict[str, Any]:
    """执行 ``system-view → lldp enable → quit``，把每步结果塞进 dict 返回。

    失败也不抛——调用方根据 ``success`` 字段决定是否重试采集。
    """
    cmds = ["system-view", "lldp enable", "quit"]
    steps: list[dict[str, Any]] = []
    try:
        results = session.send_commands(cmds)
    except Exception as exc:  # noqa: BLE001 - 容错：任何异常都收集下来
        return {
            "attempted": True,
            "success": False,
            "steps": [{"command": " | ".join(cmds), "error": str(exc)}],
            "error_message": f"LLDP 自动使能时连接失败：{exc}",
        }
    for cmd_name, res in zip(cmds, results, strict=True):
        steps.append(
            {
                "command": cmd_name,
                "output": getattr(res, "output", "")[-500:],
                "errored": getattr(res, "errored", True),
            }
        )
    ok = all(not getattr(r, "errored", True) for r in results)
    return {
        "attempted": True,
        "success": ok,
        "steps": steps,
        "error_message": "" if ok else "LLDP 自动使能失败，请查看 steps 字段。",
    }


def collect_lldp_neighbors_with_auto_enable(
    session: _SessionLike,
    *,
    detail: bool = False,
    auto_enable: bool = True,
    enable_retry_delay: float = 2.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """采集 LLDP 邻居，必要时自动使能 LLDP 后重试。

    核心流程：

    1. 发 ``display lldp neighbor brief``（默认）或 ``display lldp neighbor``
    2. 若结果 ``errored=True`` 或回显含未使能关键字
       （:func:`looks_like_lldp_not_enabled`）→ 走自动使能：

       - ``system-view → lldp enable → quit``
       - sleep ``enable_retry_delay`` 秒（默认 2.0）让 LLDP 邻居收敛
       - 重发采集命令

    3. 若第二次仍失败 → 返回 ``status="error"`` + 详细诊断 hint
    4. 成功 → 解析 + 返回，含 ``lldp_was_enabled`` /
       ``auto_enable_attempts`` 字段，便于 LLM 知道是否做过自动使能

    参数：

    - ``session``：duck-typed 接口对象，需有 ``name`` / ``send_command`` /
      ``send_commands`` 三个属性
    - ``detail``：发 ``display lldp neighbor``（详细）而非 brief
    - ``auto_enable``：默认 ``True``；设 ``False`` 时退回到
      「仅采集，未使能则报错」的旧行为
    - ``enable_retry_delay``：自动使能后等多久再重采，默认 2.0 秒
    - ``sleep_fn``：可注入，便于测试时跳过真实 sleep

    返回：dict 结构同 MCP 工具 ``ensp_verify_lldp_neighbors``。
    """
    cmd = "display lldp neighbor" if detail else "display lldp neighbor brief"

    def _send_display() -> Any:
        return session.send_command(cmd, timeout=15.0)

    # ---- 第 1 次采集 ----
    try:
        result = _send_display()
    except Exception as exc:  # noqa: BLE001
        return {
            "session": session.name,
            "status": "error",
            "errored": True,
            "error_message": str(exc),
            "neighbors": [],
            "count": 0,
            "raw_output": "",
            "auto_enable_attempts": 0,
            "lldp_was_enabled": False,
            "auto_enable": {"attempted": False, "success": False},
        }

    auto_enable_info: dict[str, Any] = {"attempted": False, "success": False}
    needs_retry = _needs_auto_enable(result, getattr(result, "output", ""))

    # ---- 自动使能 + 重试 ----
    if needs_retry and auto_enable:
        auto_enable_info = _do_auto_enable(session)
        if auto_enable_info["success"]:
            sleep_fn(max(0.0, enable_retry_delay))
            try:
                result = _send_display()
                needs_retry = _needs_auto_enable(result, getattr(result, "output", ""))
            except Exception as exc:  # noqa: BLE001
                return {
                    "session": session.name,
                    "status": "error",
                    "errored": True,
                    "error_message": f"自动使能后采集失败：{exc}",
                    "neighbors": [],
                    "count": 0,
                    "raw_output": "",
                    "auto_enable_attempts": 1,
                    "lldp_was_enabled": True,
                    "auto_enable": auto_enable_info,
                }

    # ---- 仍然失败 → 返回错误 ----
    if needs_retry:
        return {
            "session": session.name,
            "status": "error",
            "errored": True,
            "errors": getattr(result, "errors", []),
            "neighbors": [],
            "count": 0,
            "raw_output": getattr(result, "output", ""),
            "auto_enable_attempts": int(auto_enable_info["attempted"]),
            "lldp_was_enabled": bool(auto_enable_info.get("success", False)),
            "auto_enable": auto_enable_info,
            "hint": (
                "可能原因：1) LLDP 自动使能失败；2) 设备型号不支持 LLDP；"
                "3) 接口尚未 up；4) ``auto_enable=False`` 时 LLDP 未手工使能。"
            ),
        }

    # ---- 成功 → 解析并返回 ----
    raw = getattr(result, "output", "")
    neighbors: list[LLDPNeighbor] = parse_lldp_output(
        raw, local_device=session.name, brief=not detail
    )
    scan = LLDPScanResult(
        device=session.name,
        neighbors=neighbors,
        raw_output=raw,
        errored=False,
    )
    return {
        "session": session.name,
        "status": "ok",
        "command": cmd,
        "neighbors": [n.to_dict() for n in neighbors],
        "count": len(neighbors),
        "raw_output": raw[-2000:],
        "scan": scan.to_dict(),
        "auto_enable_attempts": int(auto_enable_info["attempted"]),
        "lldp_was_enabled": bool(auto_enable_info.get("success", False)),
        "auto_enable": auto_enable_info,
    }
