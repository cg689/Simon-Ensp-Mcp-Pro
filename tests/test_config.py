"""EnspConfig 单元测试。"""

from __future__ import annotations

import pytest

from grbj_ensp_mcp.config import EnspConfig, load_config


class TestEnspConfig:
    def test_default_values(self) -> None:
        cfg = EnspConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port_range == (2000, 2100)
        assert cfg.encoding == "gbk"
        assert cfg.connect_timeout > 0
        assert cfg.read_timeout > 0

    def test_frozen(self) -> None:
        cfg = EnspConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.host = "0.0.0.0"  # type: ignore[misc]

    def test_port_in_range_inside(self) -> None:
        cfg = EnspConfig(port_range=(2000, 2010))
        assert cfg.port_in_range(2000)
        assert cfg.port_in_range(2005)
        assert cfg.port_in_range(2010)

    def test_port_in_range_outside(self) -> None:
        cfg = EnspConfig(port_range=(2000, 2010))
        assert not cfg.port_in_range(1999)
        assert not cfg.port_in_range(2011)


class TestLoadConfig:
    def test_default_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENSP_HOST", raising=False)
        monkeypatch.delenv("ENSP_PORT_START", raising=False)
        monkeypatch.delenv("ENSP_PORT_END", raising=False)
        cfg = load_config()
        assert cfg.host == "127.0.0.1"
        assert cfg.port_range == (2000, 2100)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENSP_HOST", "10.0.0.1")
        monkeypatch.setenv("ENSP_PORT_START", "3000")
        monkeypatch.setenv("ENSP_PORT_END", "3010")
        cfg = load_config()
        assert cfg.host == "10.0.0.1"
        assert cfg.port_range == (3000, 3010)

    def test_invalid_port_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENSP_PORT_START", "3010")
        monkeypatch.setenv("ENSP_PORT_END", "3000")
        with pytest.raises(ValueError):
            load_config()

    def test_non_integer_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENSP_PORT_START", "abc")
        with pytest.raises(ValueError):
            load_config()
