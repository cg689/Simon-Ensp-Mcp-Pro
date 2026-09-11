"""端口扫描器单元测试。"""

from __future__ import annotations

import socket
from contextlib import contextmanager

import pytest

from grbj_ensp_mcp.port_scanner import (
    ScannedPort,
    scan_ensp_devices,
    scan_ports,
    scan_single_port,
)


@contextmanager
def _open_local_port() -> tuple[str, int]:
    """起一个临时 TCP server，返回 (host, port) 后自动清理。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        host, port = server.getsockname()
        yield host, port
    finally:
        server.close()


class TestScanSinglePort:
    def test_reachable_port(self) -> None:
        with _open_local_port() as (host, port):
            r = scan_single_port(host, port, timeout=1.0)
            assert r.reachable is True
            assert r.port == port
            assert r.error is None

    def test_unreachable_port(self) -> None:
        # 找一个未使用的端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            unused_port = s.getsockname()[1]
        # 立即关闭，几乎不会立刻被占用
        r = scan_single_port("127.0.0.1", unused_port, timeout=0.3)
        assert r.reachable is False
        assert r.error is not None


class TestScanPorts:
    def test_scan_returns_sorted(self) -> None:
        results = scan_ports("127.0.0.1", (2000, 2010), timeout=0.2, max_workers=4)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, ScannedPort)
        ports = [r.port for r in results]
        assert ports == sorted(ports)

    def test_invalid_range(self) -> None:
        with pytest.raises(ValueError):
            scan_ports("127.0.0.1", (2010, 2000), timeout=0.2)

    def test_stop_at_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 不依赖真实网络：mock 掉 scan_single_port，偶数端口视为可达。
        # 原实现依赖本机 loopback 起 socket server，在沙箱网络波动下偶发 flaky。
        def fake_scan(host: str, port: int, *, timeout: float = 0.5) -> ScannedPort:
            return ScannedPort(host=host, port=port, reachable=(port % 2 == 0))

        monkeypatch.setattr("grbj_ensp_mcp.port_scanner.scan_single_port", fake_scan)
        results = scan_ports(
            "127.0.0.1",
            (1, 6),
            timeout=0.5,
            max_workers=32,
            stop_at_first=True,
        )
        assert any(r.reachable for r in results), "未发现可达端口"
        assert {r.port for r in results} <= {1, 2, 3, 4, 5, 6}


class TestScanEnspDevices:
    def test_uses_default_config(self) -> None:
        # 仅验证函数可正常调用 + 返回类型正确，不依赖 eNSP 是否启动。
        # （早期版本断言 results == []，但测试环境 eNSP 一旦开着就不稳。）
        results = scan_ensp_devices("127.0.0.1", port_range=(2000, 2005), timeout=0.2)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, ScannedPort)
