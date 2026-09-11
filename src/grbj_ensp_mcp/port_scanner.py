"""eNSP 设备端口扫描。

eNSP 设备启动后会在本机回环地址上监听一个 Telnet 端口（默认从 2000 开始顺序分配），
本模块负责在指定端口范围内并发探测哪些端口是开放的、可被 ``TelnetClient`` 连接。
"""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import EnspConfig, load_config


@dataclass(frozen=True)
class ScannedPort:
    """扫描结果。"""

    host: str
    port: int
    reachable: bool
    error: str | None = None


def scan_single_port(
    host: str,
    port: int,
    *,
    timeout: float = 0.5,
) -> ScannedPort:
    """探测单个端口是否可连接。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ScannedPort(host=host, port=port, reachable=True)
    except (TimeoutError, OSError) as exc:
        return ScannedPort(host=host, port=port, reachable=False, error=str(exc))


def scan_ports(
    host: str,
    port_range: tuple[int, int],
    *,
    timeout: float = 0.5,
    max_workers: int = 32,
    stop_at_first: bool = False,
) -> list[ScannedPort]:
    """并发扫描一段端口范围，返回每个端口的探测结果。

    - ``stop_at_first=True`` 时只要找到一个开放端口就取消剩余探测。
    - 返回列表按端口号升序排列。

    实现注意：
    _scan 不在 event_set 后立即 return None —— 所有 future 都至少跑完一次
    scan_single_port。原因：若部分 worker 先看到 event_set 提前 return None
    （future 立即完成），as_completed 可能先 yield 这些 None future 而忽略
    后完成的 reachable=True future，导致结果丢失。统一让所有 worker 完成
    实际扫描，再由主线程根据 event 决定是否 break。
    """
    if port_range[0] > port_range[1] or port_range[0] <= 0:
        raise ValueError("port_range 非法")
    ports = list(range(port_range[0], port_range[1] + 1))
    results: list[ScannedPort] = []
    if not ports:
        return results

    found_lock = threading.Lock()
    found_event = threading.Event()

    def _scan(port: int) -> ScannedPort:
        # 始终跑完一次扫描：保证 reachable 结果不因 race 被吞
        result = scan_single_port(host, port, timeout=timeout)
        if stop_at_first and result.reachable:
            with found_lock:
                if not found_event.is_set():
                    found_event.set()
        return result

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan, p): p for p in ports}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                port = futures[future]
                result = ScannedPort(host=host, port=port, reachable=False, error=str(exc))
            results.append(result)
            # 注意：不能用 found_event.is_set() 作为 break 条件，因为 worker 在
            # 跑完 scan_single_port 后才 set event，期间主线程可能 yield 多个
            # 仍无 reachable 的 future。改为"results 至少含一个 reachable"再停。
            if stop_at_first and any(r.reachable for r in results):
                for f in futures:
                    f.cancel()
                break

    results.sort(key=lambda r: r.port)
    return results


def scan_ensp_devices(
    host: str | None = None,
    *,
    port_range: tuple[int, int] | None = None,
    timeout: float = 0.5,
    max_workers: int | None = None,
    config: EnspConfig | None = None,
) -> list[ScannedPort]:
    """基于 :class:`EnspConfig` 的便捷扫描入口。

    - 默认扫描 2000-2100 范围。
    - 只返回 ``reachable=True`` 的端口。
    """
    cfg = config or load_config()
    actual_host = host or cfg.host
    actual_range = port_range or cfg.port_range
    actual_workers = max_workers or cfg.scanner_max_workers
    results = scan_ports(
        actual_host,
        actual_range,
        timeout=timeout,
        max_workers=actual_workers,
    )
    return [r for r in results if r.reachable]
