"""eNSP ``.topo`` 拓扑文件解析。

eNSP 的 ``.topo`` 文件有两种形态：

1. **纯 XML 格式**（约 85%）：UTF-8 编码的 XML 文档，根元素包含 ``<device>``、``<connection>`` 等。
2. **ZIP 格式**（约 11%）：压缩包内含 ``topo.xml``、``device/*.cfg``、``link/*.lnk``。

本模块：

- 优先用 ZIP 魔数 (``PK\\x03\\x04``) 判断是否为压缩包；
- 内部统一使用 :mod:`defusedxml` 解析 XML，规避 XXE / Billion Laughs 等攻击；
- 将结果规整为 :class:`Topology` 数据类，便于 MCP 资源直接以 JSON 返回。
"""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field

import defusedxml.ElementTree as DET  # noqa: N814  # defusedxml 内部模块名


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #
@dataclass
class Device:
    """拓扑中的一台设备。"""

    id: str
    name: str
    model: str = ""
    console_port: int = 0
    x: int = 0
    y: int = 0
    device_type: str = "unknown"  # router / switch / firewall / cloud / pc
    # 该设备所有可命名的接口，按设备内全局顺序排列（0-based）
    # 例：AR2240 双 GE 板 → ["GE0/0/0", "GE0/0/1", "GE0/0/2"]
    interfaces: list[str] = field(default_factory=list)
    # eNSP 原生 XML 的接口下标是 0-based，但 S5700 的设备接口从 1 开始。
    # 解析器必须显式保留偏移量，避免把 srcIndex=0 错报为 GE0/0/0。
    interface_index_offset: int = 0
    raw: dict[str, str] = field(default_factory=dict)

    def interface_name(self, index: int) -> str:
        """根据 XML 中的全局接口下标返回设备真实接口名。"""
        if 0 <= index < len(self.interfaces):
            return self.interfaces[index]
        return ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "console_port": self.console_port,
            "x": self.x,
            "y": self.y,
            "device_type": self.device_type,
            "interfaces": list(self.interfaces),
            "interface_index_offset": self.interface_index_offset,
        }


@dataclass
class Connection:
    """两个设备之间的连线。"""

    src_device: str
    dst_device: str
    src_interface: str = ""  # 例如 "GE0/0/0"
    dst_interface: str = ""
    src_index: int = 0
    dst_index: int = 0
    link_type: str = ""  # 链路类型，如 Copper / Serial

    def to_dict(self) -> dict[str, object]:
        return {
            "src_device": self.src_device,
            "dst_device": self.dst_device,
            "src_interface": self.src_interface,
            "dst_interface": self.dst_interface,
            "src_index": self.src_index,
            "dst_index": self.dst_index,
            "link_type": self.link_type,
        }


@dataclass
class DeviceConfig:
    """从 ``device/*.cfg`` 中读取的设备配置片段。"""

    device_name: str
    config_text: str
    file_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "device_name": self.device_name,
            "config_text": self.config_text,
            "file_path": self.file_path,
        }


@dataclass
class Topology:
    """拓扑解析结果。"""

    name: str
    devices: list[Device] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    configs: list[DeviceConfig] = field(default_factory=list)
    source_path: str = ""
    source_format: str = "xml"  # "xml" | "zip"
    extra: dict[str, str] = field(default_factory=dict)
    root_tag: str = ""
    ensp_gui_renderable: bool = False
    ensp_gui_render_issues: list[str] = field(default_factory=list)

    def device_by_name(self, name: str) -> Device | None:
        for d in self.devices:
            if d.name == name:
                return d
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "source_format": self.source_format,
            "devices": [d.to_dict() for d in self.devices],
            "connections": [c.to_dict() for c in self.connections],
            "configs": [c.to_dict() for c in self.configs],
            "extra": self.extra,
            "root_tag": self.root_tag,
            "ensp_gui_renderable": self.ensp_gui_renderable,
            "ensp_gui_render_issues": list(self.ensp_gui_render_issues),
        }

    def render_summary(self) -> str:
        """生成一个面向 LLM 的简洁文本摘要。"""
        lines = [f"拓扑名称: {self.name}", f"来源: {self.source_path} ({self.source_format})"]
        lines.append(
            f"根元素: <{self.root_tag or '?'}>  eNSP GUI 可渲染: {self.ensp_gui_renderable}"
        )
        if not self.ensp_gui_renderable:
            lines.append("  ⚠ 警告：当前 .topo 不会被 eNSP GUI 渲染（打开后画布空白）")
            for issue in self.ensp_gui_render_issues:
                lines.append(f"    - {issue}")
            lines.append(
                "  建议：用 grbj_ensp_mcp.topo_builder.NativeTopoBuilder 重新生成，"
                "或参考 docs/topo-drawing-rules.md 的真实原生格式"
            )
        lines.append(f"设备数: {len(self.devices)}")
        for dev in self.devices:
            port_info = f"console={dev.console_port}" if dev.console_port > 1 else "无 console"
            lines.append(
                f"  - {dev.name} 型号={dev.model or '?'} 类型={dev.device_type} {port_info} 位置=({dev.x},{dev.y})"
            )
        lines.append(f"连线数: {len(self.connections)}")
        for conn in self.connections:
            lines.append(
                f"  - {conn.src_device}({conn.src_interface}) <-> "
                f"{conn.dst_device}({conn.dst_interface})"
            )
        if self.configs:
            lines.append(f"附带配置片段: {len(self.configs)} 个")
        return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 设备类型启发
# ---------------------------------------------------------------------- #
def _is_s5700_model(model: str) -> bool:
    """判断型号是否属于 S5700 系列，兼容 S5700 / S5700-LI 等写法。"""
    normalized = model.upper().replace("-", "")
    return normalized.startswith("S5700") or "S5700" in normalized


def _guess_device_type(model: str) -> str:
    m = model.upper()
    if "USG" in m or "FIREWALL" in m or "NGFW" in m:
        return "firewall"
    if "AR" in m or "NE" in m or "ROUTER" in m:
        return "router"
    if "S5" in m or "S3" in m or "S6" in m or "S2" in m or "SWITCH" in m or "LSW" in m:
        return "switch"
    if "AC" in m and "AP" not in m:
        return "wireless_controller"
    if "AP" in m:
        return "ap"
    if "CLOUD" in m:
        return "cloud"
    if "PC" in m:
        return "pc"
    return "unknown"


# ---------------------------------------------------------------------- #
# 解析入口
# ---------------------------------------------------------------------- #
_ZIP_MAGIC = b"PK\x03\x04"


def parse_topo(path: str) -> Topology:
    """解析 ``.topo`` 文件，自动识别 ZIP 或纯 XML。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到拓扑文件: {path}")
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(_ZIP_MAGIC):
        return _parse_zip(path)
    return _parse_xml(path)


def parse_topo_bytes(data: bytes, *, name: str = "unnamed") -> Topology:
    """直接解析内存中的字节（用于 MCP 资源上传等场景）。"""
    if data.startswith(_ZIP_MAGIC):
        return _parse_zip_bytes(data, name=name)
    return _parse_xml_bytes(data, source_name=name, source_format="xml", source_path="")


# ---------------------------------------------------------------------- #
# XML 解析
# ---------------------------------------------------------------------- #
# eNSP GUI 渲染必需字段：<dev> 上不可缺的属性
_ENSP_REQUIRED_DEV_ATTRS = (
    "poe",
    "settings",
    "system_mac",
    "com_port",
    "bootmode",
    "edit_left",
    "edit_top",
)

# eNSP GUI 渲染必需字段：<interfacePair> 上不可缺的锚点属性
_ENSP_REQUIRED_PAIR_ATTRS = (
    "srcBoundRectIsMoved",
    "srcBoundRect_X",
    "srcBoundRect_Y",
    "srcOffset_X",
    "srcOffset_Y",
    "tarBoundRectIsMoved",
    "tarBoundRect_X",
    "tarBoundRect_Y",
    "tarOffset_X",
    "tarOffset_Y",
)

# 设备 id 是否为 UUID 格式（eNSP 期望真实 UUID，不接受 D1/D2 简写）
_UUID_RE = __import__("re").compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$",
    __import__("re").I,
)


def _check_ensp_gui_renderable(
    root: object,
    devices: list[Device],
) -> tuple[bool, list[str]]:
    """检查当前 .topo 能否被 eNSP GUI 渲染。

    真实 eNSP 严格只识别：

    1. 根元素为 ``<topo>``（不区分大小写），不接受 ``<topology>``
    2. 每个 ``<dev>`` 的 id 是 UUID 格式
    3. 每个 ``<dev>`` 具备完整的渲染属性（poe/settings/system_mac/com_port/bootmode/edit_left/edit_top）
    4. 每个 ``<line>`` 的 ``<interfacePair>`` 具备完整锚点属性
    """
    issues: list[str] = []

    # 1) 根元素
    root_tag = (getattr(root, "tag", "") or "").lower()
    if root_tag != "topo":
        issues.append(f"根元素是 <{root_tag}> 而非 <topo>（eNSP GUI 不识别 <topology>）")

    # 2) 设备 id 必须是 UUID
    for d in devices:
        if not _UUID_RE.match(d.id):
            issues.append(f"设备 {d.name} id={d.id!r} 不是 UUID 格式（应为 36 位十六进制）")

    # 3) <dev> 完整属性
    for dev_el in _iter_elements(root, {"dev", "Dev", "DEV"}):
        attribs = getattr(dev_el, "attrib", {}) or {}
        missing = [k for k in _ENSP_REQUIRED_DEV_ATTRS if k not in attribs]
        if missing:
            name = attribs.get("name", "?")
            issues.append(f"设备 {name} 缺字段: {', '.join(missing)}")

    # 4) <line>/<interfacePair> 锚点字段
    for line_el in _iter_elements(root, {"line", "Line", "LINE"}):
        for child in _direct_children(line_el):
            if (getattr(child, "tag", "") or "").lower() != "interfacepair":
                continue
            pair_attribs = getattr(child, "attrib", {}) or {}
            missing = [k for k in _ENSP_REQUIRED_PAIR_ATTRS if k not in pair_attribs]
            if missing:
                src_id = (getattr(line_el, "attrib", {}) or {}).get("srcDeviceID", "?")
                issues.append(
                    f"<line src={src_id}> 的 <interfacePair> 缺字段: {', '.join(missing)}"
                )

    return (len(issues) == 0), issues


def _parse_xml(path: str) -> Topology:
    with open(path, "rb") as f:
        data = f.read()
    return _parse_xml_bytes(
        data,
        source_name=os.path.splitext(os.path.basename(path))[0],
        source_format="xml",
        source_path=os.path.abspath(path),
    )


def _parse_xml_bytes(
    data: bytes,
    *,
    source_name: str,
    source_format: str,
    source_path: str,
) -> Topology:
    # eNSP 偶尔声明 ``encoding="UNICODE"`` 这种非法值，先按 UTF-8 解码再交给 defusedxml
    text = data.decode("utf-8", errors="replace")
    try:
        root = DET.fromstring(text)
    except DET.ParseError as exc:
        raise ValueError(f"XML 解析失败: {exc}") from exc

    topology = Topology(
        name=source_name,
        source_path=source_path,
        source_format=source_format,
    )
    topology.root_tag = root.tag
    topology.extra["root_tag"] = root.tag

    # 1) 设备
    for device_el in _iter_elements(
        root,
        {"device", "Device", "DEVICE", "dev", "Dev", "DEV"},
    ):
        dev = _parse_device(device_el)
        if dev is not None:
            topology.devices.append(dev)

    # 2) 连线
    for conn_el in _iter_elements(
        root,
        {"connection", "Connection", "link", "Link", "line", "Line", "LINE"},
    ):
        conn = _parse_connection(conn_el, topology)
        if conn is not None:
            topology.connections.append(conn)

    # 3) 部分 .topo 还会带 startup 配置或备注
    for cfg_el in _iter_elements(root, {"config", "Config", "startup"}):
        device = cfg_el.attrib.get("device") or cfg_el.attrib.get("name") or ""
        text_content = (cfg_el.text or "").strip()
        if device and text_content:
            topology.configs.append(
                DeviceConfig(
                    device_name=device,
                    config_text=text_content,
                    file_path=source_path,
                )
            )

    # 4) eNSP GUI 可渲染性检测（关键：提前发现「简化变体不会被 eNSP 渲染」类问题）
    renderable, issues = _check_ensp_gui_renderable(root, topology.devices)
    topology.ensp_gui_renderable = renderable
    topology.ensp_gui_render_issues = issues

    return topology


def _parse_device(element: object) -> Device | None:
    """从 ``<dev>`` / ``<device>`` 元素构造 :class:`Device`。

    兼容多种 eNSP 版本：

    - 属性写法（id="..." name="..." model="..." com_port="..."）
    - 子元素写法（<name>、<type>、<id>、<location><x/><y/></location>）
    - 带 ``<slot><interface sztype="..." interfacename="..." count="N"/></slot>``
      的接口表，会被展平成全局接口名列表
    """
    attribs = getattr(element, "attrib", {}) or {}
    children = {child.tag: child for child in _direct_children(element)}
    get = lambda *keys: _first_attr(attribs, children, keys)  # noqa: E731

    dev_id = get("id", "deviceId", "devId", "ID")
    name = get("name", "Name", "NAME", "deviceName")
    if not name:
        return None
    model = get("model", "type", "Type", "Model")
    com_port_raw = get("com_port", "comPort", "console_port", "ComPort")
    try:
        com_port = int(com_port_raw) if com_port_raw else 0
    except (TypeError, ValueError):
        com_port = 0
    if com_port == 0 and model.upper() == "CLOUD":
        com_port = 1  # Cloud 设备占位端口
    cx_raw = get("cx", "x", "X")
    cy_raw = get("cy", "y", "Y")
    try:
        x = int(float(cx_raw or 0))
    except (TypeError, ValueError):
        x = 0
    try:
        y = int(float(cy_raw or 0))
    except (TypeError, ValueError):
        y = 0

    # 解析 <slot> 内的接口列表，展平成全局接口名
    interfaces = _collect_interfaces(element, model=model or "")

    # S5700 在 eNSP 设备上从 GE0/0/1 开始；原生 XML 下标 0 对应第一个接口。
    # 其他型号（如 AR）保持 0-based，避免破坏已有真实拓扑的解析结果。
    interface_index_offset = 1 if _is_s5700_model(model) else 0

    return Device(
        id=str(dev_id or name),
        name=name,
        model=model or "",
        console_port=com_port,
        x=x,
        y=y,
        device_type=_guess_device_type(model or ""),
        interfaces=interfaces,
        interface_index_offset=interface_index_offset,
        raw={k: str(v) for k, v in attribs.items()},
    )


def _collect_interfaces(element: object, *, model: str = "") -> list[str]:
    """从设备的 ``<slot>`` 树中收集所有可命名的接口。

    规则：

    - 每个 ``<slot>`` 内的接口在 slot 内从 0 连续编号（不受不同 interface 元素影响）
    - 跨 slot 累加 slot 索引
    - 实际命名形如 ``GE0/0/0``、``GE0/0/1``、``GE0/0/2``，``Ethernet0/0/0``。
    - S5700 的设备真实接口号从 1 开始；XML 的 0-based 索引由
      :class:`Device` 的 ``interface_index_offset`` 单独记录。
    """
    index_offset = 1 if _is_s5700_model(model) else 0
    interfaces: list[str] = []
    for slot_index, slot in enumerate(_iter_elements(element, {"slot", "Slot", "SLOT"})):
        slot_offset = 0
        for iface in _iter_elements(slot, {"interface", "Interface", "INTERFACE"}):
            attribs = getattr(iface, "attrib", {}) or {}
            name = (
                attribs.get("interfacename")
                or attribs.get("name")
                or attribs.get("InterfaceName")
                or ""
            )
            try:
                count = int(attribs.get("count", 1) or 1)
            except (TypeError, ValueError):
                count = 1
            for _ in range(count):
                interfaces.append(f"{name}0/{slot_index}/{slot_offset + index_offset}")
                slot_offset += 1
        slot_index += 1
    return interfaces


def _direct_children(element: object) -> list[object]:
    """返回元素的直接子节点列表，兼容 defusedxml 与标准库。"""
    children = getattr(element, "__children__", None)
    if children is not None:
        return list(children)
    try:
        return list(element)  # type: ignore[arg-type]
    except TypeError:
        return []


def _first_attr(
    attribs: dict[str, str],
    children: dict[str, object],
    keys: Iterable[str],
) -> str:
    for k in keys:
        if k in attribs and attribs[k] != "":
            return attribs[k]
        if k in children:
            child = children[k]
            text = getattr(child, "text", None)
            if text:
                return text
            if "value" in getattr(child, "attrib", {}):
                return child.attrib["value"]
    return ""


def _parse_connection(element: object, topology: Topology) -> Connection | None:
    """从 ``<connection>`` / ``<line>`` 元素构造 :class:`Connection`。

    eNSP 常见字段：

    - ``srcDeviceID`` / ``destDeviceID``：源/目标设备 ID
    - ``InterfacePair`` 子元素：含 ``lineName``（链路类型，如 Copper）/
      ``srcIndex`` / ``tarIndex``（在设备 slot 内的全局索引）

    srcIndex / tarIndex 是设备在 ``<slot>`` 树中的全局接口序号，需要配合
    :class:`Device` 的 ``interfaces`` 列表才能还原出真实接口名（GE0/0/0 等）。
    """
    attribs = getattr(element, "attrib", {}) or {}
    children = {child.tag: child for child in _direct_children(element)}

    src_id = attribs.get("srcDeviceID") or attribs.get("src") or attribs.get("srcDevice")
    dst_id = attribs.get("destDeviceID") or attribs.get("dst") or attribs.get("destDevice")
    if not src_id or not dst_id:
        # 兼容 eNSP 早期格式：<connection src="R1" dst="R2" srcIf="GE0/0/0" dstIf="GE0/0/1" />
        src_id = src_id or attribs.get("srcDeviceName")
        dst_id = dst_id or attribs.get("destDeviceName")

    if not src_id or not dst_id:
        return None

    src_name = _resolve_device_name(src_id, topology)
    dst_name = _resolve_device_name(dst_id, topology)
    if not src_name or not dst_name:
        return None

    pair = children.get("InterfacePair")
    if pair is None:
        pair = children.get("interfacePair")
    if pair is not None:
        pair_attribs = getattr(pair, "attrib", {}) or {}
        link_type = pair_attribs.get("lineName") or pair_attribs.get("linkType") or ""
        try:
            src_index = int(pair_attribs.get("srcIndex", 0))
        except (TypeError, ValueError):
            src_index = 0
        try:
            dst_index = int(pair_attribs.get("tarIndex", 0))
        except (TypeError, ValueError):
            dst_index = 0
        # 旧 eNSP 格式：lineName 直接写接口名（"GE0/0/0"）而非链路类型
        # 启发式判断：包含 "/" 视为接口名，否则视为链路类型
        line_name = pair_attribs.get("lineName") or ""
        if "/" in line_name:
            legacy_src_if = line_name
            legacy_dst_if = line_name
        else:
            legacy_src_if = ""
            legacy_dst_if = ""
    else:
        link_type = ""
        src_index = 0
        dst_index = 0
        legacy_src_if = ""
        legacy_dst_if = ""

    # 把 srcIndex / dstIndex 还原成真实接口名（GE0/0/0 等）
    src_dev = topology.device_by_name(src_name)
    dst_dev = topology.device_by_name(dst_name)
    src_if = src_dev.interface_name(src_index) if src_dev else ""
    dst_if = dst_dev.interface_name(dst_index) if dst_dev else ""
    # 如果旧格式直接给了 srcInterface/dstInterface 或 lineName 形如 GE0/0/0，优先使用
    attr_src_if = attribs.get("srcIf") or attribs.get("srcInterface") or ""
    attr_dst_if = attribs.get("dstIf") or attribs.get("dstInterface") or ""
    if attr_src_if:
        src_if = attr_src_if
    if attr_dst_if:
        dst_if = attr_dst_if
    if legacy_src_if and not src_if:
        src_if = legacy_src_if
    if legacy_dst_if and not dst_if:
        dst_if = legacy_dst_if

    return Connection(
        src_device=src_name,
        dst_device=dst_name,
        src_interface=src_if,
        dst_interface=dst_if,
        src_index=src_index,
        dst_index=dst_index,
        link_type=link_type if "/" not in (link_type or "") else "",
    )


def _resolve_device_name(device_ref: str, topology: Topology) -> str | None:
    """把 ``<id>`` 引用解析为设备名（找不到时直接返回原值）。"""
    if topology.device_by_name(device_ref):
        return device_ref
    for d in topology.devices:
        if d.id == device_ref:
            return d.name
    return device_ref or None


def _iter_elements(root: object, tags: set[str]) -> Iterable[object]:
    """遍历所有匹配标签的元素（不区分大小写的子集匹配）。"""
    lowered = {t.lower() for t in tags}
    for el in getattr(root, "iter", lambda: [])():
        if el.tag.lower() in lowered:
            yield el


# ---------------------------------------------------------------------- #
# ZIP 解析
# ---------------------------------------------------------------------- #
def _parse_zip(path: str) -> Topology:
    with open(path, "rb") as f:
        data = f.read()
    return _parse_zip_bytes(data, name=os.path.splitext(os.path.basename(path))[0])


def _parse_zip_bytes(data: bytes, *, name: str) -> Topology:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"ZIP 解析失败: {exc}") from exc

    topology = Topology(
        name=name,
        source_path="",
        source_format="zip",
    )

    # 1) 先尝试从内嵌 topo.xml 拿结构
    xml_candidates = [
        n
        for n in archive.namelist()
        if n.lower().endswith("topo.xml") or n.lower().endswith("topology.xml")
    ]
    for xml_name in xml_candidates:
        xml_data = archive.read(xml_name)
        try:
            inner = _parse_xml_bytes(
                xml_data,
                source_name=name,
                source_format="zip",
                source_path=xml_name,
            )
        except ValueError:
            continue
        topology.devices = inner.devices
        topology.connections = inner.connections
        topology.root_tag = inner.root_tag
        topology.ensp_gui_renderable = inner.ensp_gui_renderable
        topology.ensp_gui_render_issues = inner.ensp_gui_render_issues
        topology.extra["inner_xml"] = xml_name
        break

    # 2) 读取 device/*.cfg 文本作为配置片段
    for entry in archive.namelist():
        if not entry.lower().endswith(".cfg"):
            continue
        base = os.path.basename(entry)
        device_name = os.path.splitext(base)[0]
        try:
            config_text = archive.read(entry).decode("utf-8", errors="ignore")
        except (KeyError, RuntimeError):
            continue
        topology.configs.append(
            DeviceConfig(
                device_name=device_name,
                config_text=config_text,
                file_path=entry,
            )
        )

    return topology
