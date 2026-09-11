"""全局配置：端口范围、编码、超时等可调参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnspConfig:
    """eNSP 自动化操作的全局配置。

    字段说明：
    - host：eNSP 设备 Telnet 服务监听地址，默认本机回环。
    - port_range：eNSP 设备默认占用的 Telnet 端口范围。
    - encoding：eNSP 设备控制台使用 GBK 编码。
    - connect_timeout / read_timeout：socket 连接 / 读取超时（秒）。
    - default_command_delay：连续命令之间的等待秒数，避免设备处理未完成。
    - session_buffer_size：每个会话在内存中保留的最近输出行数。
    - scanner_max_workers：端口扫描并发线程数。
    """

    host: str = "127.0.0.1"
    port_range: tuple[int, int] = (2000, 2100)
    encoding: str = "gbk"
    connect_timeout: float = 5.0
    read_timeout: float = 3.0
    default_command_delay: float = 0.6
    default_long_command_delay: float = 1.5
    session_buffer_size: int = 2000
    scanner_max_workers: int = 32

    def port_in_range(self, port: int) -> bool:
        """判断端口是否落在 eNSP 默认端口范围内。"""
        return self.port_range[0] <= port <= self.port_range[1]


def load_config() -> EnspConfig:
    """从环境变量加载配置，允许运行时覆盖默认值。

    支持的环境变量：
    - ``ENSP_HOST``：默认 127.0.0.1。
    - ``ENSP_PORT_START`` / ``ENSP_PORT_END``：默认 2000 / 2100。
    - ``ENSP_ENCODING``：默认 gbk。
    - ``ENSP_CONNECT_TIMEOUT`` / ``ENSP_READ_TIMEOUT``：默认 5.0 / 3.0 秒。
    """

    host = os.environ.get("ENSP_HOST", "127.0.0.1")
    encoding = os.environ.get("ENSP_ENCODING", "gbk")

    try:
        port_start = int(os.environ.get("ENSP_PORT_START", "2000"))
        port_end = int(os.environ.get("ENSP_PORT_END", "2100"))
    except ValueError as exc:
        raise ValueError("ENSP_PORT_START / ENSP_PORT_END 必须为整数") from exc

    if port_start <= 0 or port_end <= 0 or port_start > port_end:
        raise ValueError("端口范围非法：起点必须为正且不大于终点")

    try:
        connect_timeout = float(os.environ.get("ENSP_CONNECT_TIMEOUT", "5.0"))
        read_timeout = float(os.environ.get("ENSP_READ_TIMEOUT", "3.0"))
    except ValueError as exc:
        raise ValueError("超时配置必须为浮点数") from exc

    return EnspConfig(
        host=host,
        port_range=(port_start, port_end),
        encoding=encoding,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )


# 模块级默认单例，便于直接复用
DEFAULT_CONFIG: EnspConfig = load_config()
