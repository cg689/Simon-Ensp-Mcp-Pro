"""原生 eNSP ``.topo`` 拓扑生成器。

解决「简化变体不被 eNSP GUI 渲染」类问题：
``NativeTopoBuilder`` 强制按真实 eNSP 原生格式输出 XML，
自动补齐所有必需字段（poe / settings / bootmode / edit_left / edit_top / UUID /
``srcBoundRect_*`` / ``tarBoundRect_*`` 锚点），让调用者只关心业务数据
（设备清单 + 连线关系），不再手写易错的 XML。

使用方法：
    >>> from grbj_ensp_mcp.topo_builder import NativeTopoBuilder
    >>> b = NativeTopoBuilder()
    >>> b.add_device("AR1", model="AR2240", com_port=2000, cx=400, cy=100)
    >>> b.add_device("SW1", model="S5700", com_port=2001, cx=400, cy=300)
    >>> b.add_device("PC1", model="PC", com_port=0, cx=200, cy=500)
    >>> b.add_line("AR1", "SW1", src_index=0, tar_index=1)
    >>> b.add_line("SW1", "PC1", src_index=2, tar_index=0)
    >>> xml = b.render()  # 完整 eNSP 原生格式 XML 字符串
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

# ---------------------------------------------------------------------- #
# 默认接口配置
# ---------------------------------------------------------------------- #
# 设备型号 → slot 内接口规格列表 [(interfacename, count), ...]
DEFAULT_SLOT_SPECS: dict[str, list[tuple[str, int]]] = {
    "AR1220": [("GE", 2), ("Ethernet", 8), ("Serial", 2)],
    "AR201": [("GE", 2)],
    "AR2220": [("GE", 1), ("GE", 2)],
    "AR2240": [("GE", 2), ("GE", 2), ("GE", 2)],  # 3 个 LPU 板共 6 个 GE
    "S2700": [("FE", 24)],
    "S3700": [("Ethernet", 22), ("GE", 2)],  # 实测对齐 eNSP GUI 默认形态（实验4真实.topo样本）
    "S5700": [("GE", 24)],
    "S6700": [("GE", 24)],
    "AC6005": [("GE", 8)],
    "AP6050": [("GE", 2)],
    "USG5500": [("GE", 4), ("Serial", 2)],
    # USG6000V 默认形态（与 eNSP GUI 拖出一致，逆向自真实 .topo 样本）：
    # 第一组 = slotIndex 0 的管理口 GE0/0/0（不用于业务连线）；
    # 第二组 = slotIndex 1 的 7 个业务口 GE1/0/0 ~ GE1/0/6。
    # 渲染走 _render_dev 的枚举式分支（<slot id="1"> + 逐口 <interface>），
    # 与 AR/S 的 count 式 <slot number="slot17"> 结构不同，勿合并。
    "USG6000V": [("GE", 1), ("GE", 7)],
    "PC": [("Ethernet", 1)],
    "STA": [],
    "Laptop": [("Ethernet", 1)],
    "MCS": [("Ethernet", 1)],
}


# ---------------------------------------------------------------------- #
# 设备 / 连线声明
# ---------------------------------------------------------------------- #
@dataclass
class _DeviceSpec:
    name: str
    model: str
    com_port: int
    cx: float
    cy: float
    slot_specs: list[tuple[str, int]]
    system_mac: str = ""
    settings: str = ""
    device_uuid: str = ""
    # slot 内接口全局数量（srcIndex/tarIndex 上限）
    interface_total: int = 0


@dataclass
class _LineSpec:
    src: str
    dst: str
    src_index: int
    tar_index: int
    link_type: str = "Copper"


# ---------------------------------------------------------------------- #
# 主类
# ---------------------------------------------------------------------- #
class NativeTopoBuilder:
    """强制按真实 eNSP 原生格式生成 ``.topo`` 的生成器。"""

    def __init__(
        self,
        *,
        topo_version: str = "1.3.00.100",
        encoding: str = "UTF-8",
    ) -> None:
        self.topo_version = topo_version
        self.encoding = encoding
        self._devices: list[_DeviceSpec] = []
        self._lines: list[_LineSpec] = []
        self._uuid_by_name: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 设备
    # ------------------------------------------------------------------ #
    def add_device(
        self,
        name: str,
        *,
        model: str = "AR2240",
        com_port: int = 0,
        cx: float = 0.0,
        cy: float = 0.0,
        slot_specs: list[tuple[str, int]] | None = None,
        system_mac: str | None = None,
        settings: str = "",
    ) -> NativeTopoBuilder:
        """添加一台设备。

        参数:
            name: 设备显示名（唯一）
            model: 设备型号，决定默认接口表
            com_port: Telnet 端口；PC/STA 等填 0
            cx, cy: 画布中心坐标（像素）
            slot_specs: 自定义 slot 接口规格 ``[(interfacename, count), ...]``；
                缺省按 model 取 :data:`DEFAULT_SLOT_SPECS`
            system_mac: 设备 MAC；缺省按 `00-E0-FC-XX-XX-XX` 随机生成
            settings: 设备属性串（如 PC 的 ``-simpc_ip ... -simpc_mask ...``），
                会原样写入 ``<dev settings="...">`` 字段；缺省空串。
                注意：settings 内含空格，调用方需自行把头尾空格拼好。
        """
        if name in self._uuid_by_name:
            raise ValueError(f"设备名重复: {name!r}")

        specs = (
            list(slot_specs)
            if slot_specs is not None
            else list(DEFAULT_SLOT_SPECS.get(model, [("GE", 1)]))
        )
        interface_total = sum(count for _, count in specs)

        # UUID 在 builder 生命周期内稳定
        device_uuid = str(uuid.uuid4()).upper()
        mac = system_mac or _generate_mac(name)

        self._devices.append(
            _DeviceSpec(
                name=name,
                model=model,
                com_port=com_port,
                cx=float(cx),
                cy=float(cy),
                slot_specs=specs,
                system_mac=mac,
                settings=settings,
                device_uuid=device_uuid,
                interface_total=interface_total,
            )
        )
        self._uuid_by_name[name] = device_uuid
        return self

    # ------------------------------------------------------------------ #
    # 连线
    # ------------------------------------------------------------------ #
    def add_line(
        self,
        src: str,
        dst: str,
        *,
        src_index: int = 0,
        tar_index: int = 0,
        link_type: str = "Copper",
    ) -> NativeTopoBuilder:
        """添加一条连线。

        src_index / tar_index 是设备在 slot 树中的**全局接口序号**
        （与 :mod:`topo_parser` 一致：从上到下累加 0-based）。
        """
        if src not in self._uuid_by_name:
            raise ValueError(f"未声明源设备: {src!r}")
        if dst not in self._uuid_by_name:
            raise ValueError(f"未声明目标设备: {dst!r}")
        self._lines.append(
            _LineSpec(
                src=src,
                dst=dst,
                src_index=src_index,
                tar_index=tar_index,
                link_type=link_type,
            )
        )
        return self

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def validate(self) -> list[str]:
        """校验已添加的设备与连线；返回错误列表（空列表表示通过）。"""
        errors: list[str] = []
        names = {d.name for d in self._devices}
        if len(names) != len(self._devices):
            seen: set[str] = set()
            for d in self._devices:
                if d.name in seen:
                    errors.append(f"设备名重复: {d.name!r}")
                seen.add(d.name)

        # com_port 不重复（PC/STA=0 可多个）
        used_ports: dict[int, str] = {}
        for d in self._devices:
            if d.com_port <= 1:
                continue
            if d.com_port in used_ports:
                errors.append(
                    f"com_port {d.com_port} 被 {used_ports[d.com_port]} 与 {d.name} 重复占用"
                )
            used_ports[d.com_port] = d.name

        # 连线 src_index / tar_index 越界
        iface_map = {d.name: d.interface_total for d in self._devices}
        for ln in self._lines:
            if ln.src_index >= iface_map[ln.src]:
                errors.append(
                    f"连线 {ln.src}->{ln.dst} 的 src_index={ln.src_index} 超出 "
                    f"{ln.src} 接口数 {iface_map[ln.src]}"
                )
            if ln.tar_index >= iface_map[ln.dst]:
                errors.append(
                    f"连线 {ln.src}->{ln.dst} 的 tar_index={ln.tar_index} 超出 "
                    f"{ln.dst} 接口数 {iface_map[ln.dst]}"
                )
        return errors

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def render(self, *, validate: bool = True) -> str:
        """渲染为完整 eNSP 原生格式 XML 字符串。"""
        if validate:
            errors = self.validate()
            if errors:
                raise ValueError("生成器校验失败:\n  - " + "\n  - ".join(errors))

        dev_by_name = {d.name: d for d in self._devices}
        parts: list[str] = []

        # 1) XML 头
        parts.append(f'<?xml version="1.0" encoding="{self.encoding}"?>')
        parts.append(f'<topo version="{self.topo_version}">')
        parts.append("    <devices>")

        # 2) <devices>
        for d in self._devices:
            parts.append(_render_dev(d))

        parts.append("    </devices>")

        # 3) <lines>
        parts.append("    <lines>")
        for ln in self._lines:
            src_dev = dev_by_name[ln.src]
            dst_dev = dev_by_name[ln.dst]
            parts.append(_render_line(src_dev, dst_dev, ln))
        parts.append("    </lines>")

        # 4) 收尾
        parts.append("    <shapes />")
        parts.append("    <txttips />")
        parts.append("</topo>")

        return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------- #
# 渲染辅助
# ---------------------------------------------------------------------- #
def _generate_mac(seed: str) -> str:
    """按设备名生成稳定的伪随机 MAC（格式 00-E0-FC-XX-XX-XX）。"""
    import hashlib

    digest = hashlib.md5(seed.encode("utf-8")).digest()
    return f"00-E0-FC-{digest[0:1].hex().upper()}-{digest[1:2].hex().upper().zfill(2)}-{digest[2:3].hex().upper().zfill(2)}"


def _render_dev(d: _DeviceSpec) -> str:
    edit_left = int(d.cx) + 27
    edit_top = int(d.cy) + 54
    head = [
        f'        <dev id="{d.device_uuid}" name="{d.name}" poe="0" model="{d.model}"',
        f'             settings="{d.settings}" system_mac="{d.system_mac}" com_port="{d.com_port}" bootmode="0"',
        f'             cx="{d.cx:.6f}" cy="{d.cy:.6f}" edit_left="{edit_left}" edit_top="{edit_top}">',
    ]
    tail = ["        </dev>"]

    if d.model == "USG6000V":
        # USG6000V 是虚拟机形态设备，.topo 里用枚举式接口表（逆向自真实样本）：
        # <slot id="1"> 下每个 <interface> 一个口，slotIndex=0 是管理口 GE0/0/0，
        # slotIndex=1 是业务口 GE1/0/0 ~ GE1/0/6。每组 slot_specs 对应一个 slotIndex。
        head.append('            <slot id="1">')
        for slot_idx, (iface_name, count) in enumerate(d.slot_specs):
            for i in range(count):
                head.append(
                    f'                <interface category="Ethernet" type="{iface_name}"'
                    f' slotIndex="{slot_idx}" cardIndex="0" interfaceIndex="{i}" />'
                )
        head.append("            </slot>")
        return "\n".join(head + tail)

    head.append('            <slot number="slot17" isMainBoard="1">')
    for iface_name, count in d.slot_specs:
        head.append(
            f'                <interface sztype="Ethernet" interfacename="{iface_name}" count="{count}" />'
        )
    head.append("            </slot>")
    return "\n".join(head + tail)


def _render_line(src_dev: _DeviceSpec, dst_dev: _DeviceSpec, ln: _LineSpec) -> str:
    src_uuid = src_dev.device_uuid
    dst_uuid = dst_dev.device_uuid
    sb_x, sb_y, so_x, so_y = _compute_anchor(src_dev.cx, src_dev.cy, dst_dev.cx, dst_dev.cy)
    tb_x, tb_y, to_x, to_y = _compute_anchor(dst_dev.cx, dst_dev.cy, src_dev.cx, src_dev.cy)
    return (
        f'        <line srcDeviceID="{src_uuid}" destDeviceID="{dst_uuid}">'
        f'<interfacePair lineName="{ln.link_type}"'
        f' srcIndex="{ln.src_index}" srcBoundRectIsMoved="1"'
        f' srcBoundRect_X="{sb_x:.6f}" srcBoundRect_Y="{sb_y:.6f}"'
        f' srcOffset_X="{so_x:.6f}" srcOffset_Y="{so_y:.6f}"'
        f' tarIndex="{ln.tar_index}" tarBoundRectIsMoved="1"'
        f' tarBoundRect_X="{tb_x:.6f}" tarBoundRect_Y="{tb_y:.6f}"'
        f' tarOffset_X="{to_x:.6f}" tarOffset_Y="{to_y:.6f}" />'
        f"</line>"
    )


def _compute_anchor(
    sx: float, sy: float, tx: float, ty: float
) -> tuple[float, float, float, float]:
    """计算接口锚点：bound = 源中心 + 50px 朝目标方向；offset = bound - 中心。"""
    dx, dy = tx - sx, ty - sy
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        # 同位置兜底，避免除零
        return sx, sy + 50.0, 0.0, 50.0
    bx = sx + 50.0 * dx / dist
    by = sy + 50.0 * dy / dist
    return bx, by, bx - sx, by - sy
