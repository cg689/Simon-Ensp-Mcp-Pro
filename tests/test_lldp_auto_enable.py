"""``collect_lldp_neighbors_with_auto_enable`` 的单元测试。

用 MagicMock 模拟 session，避免依赖真实 eNSP / Telnet 连接。
覆盖：

- LLDP 已开启：只发 1 条采集命令
- LLDP 未开启 + auto_enable=True（默认）：先 enable 再重采，返回成功
- LLDP 未开启 + auto_enable=False：只发 1 条，返回错误
- 自动使能后重采仍失败：返回 errored=True + lldp_was_enabled=True
- sleep_fn 可注入（不会真 sleep 2 秒）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from grbj_ensp_mcp.lldp_verifier import collect_lldp_neighbors_with_auto_enable


# ---------------------------------------------------------------------- #
# 测试用假 session 与 TelnetResult
# ---------------------------------------------------------------------- #
def _make_result(output: str, *, errored: bool = False, errors: list[str] | None = None) -> Any:
    """构造一个 TelnetResult 兼容对象。"""
    obj = MagicMock()
    obj.output = output
    obj.errored = errored
    obj.errors = errors or []
    return obj


def _make_session(
    *,
    display_output: str = "",
    display_errored: bool = False,
    enable_results: list[Any] | None = None,
    second_display_output: str = "",
    second_display_errored: bool = False,
) -> MagicMock:
    """构造一个 session-like 对象，按调用顺序返回预设结果。

    - 第 1 次 send_command 返回 ``display_output``
    - send_commands 返回 ``enable_results``（默认 3 条全成功）
    - 第 2 次 send_command 返回 ``second_display_output``
    """
    session = MagicMock()
    session.name = "AR1"

    # 用 side_effect 精确控制每次调用的返回值
    display_call_count = 0

    def _send_command(cmd: str, timeout: float = 10.0) -> Any:
        nonlocal display_call_count
        display_call_count += 1
        if display_call_count == 1:
            return _make_result(display_output, errored=display_errored)
        # 第 2 次及之后
        return _make_result(second_display_output, errored=second_display_errored)

    session.send_command.side_effect = _send_command

    if enable_results is None:
        enable_results = [
            _make_result("Enter system view, return user view with Ctrl+Z."),
            _make_result("Info: LLDP is enabled globally."),
            _make_result("<AR1>"),
        ]
    session.send_commands.return_value = enable_results

    return session


# ---------------------------------------------------------------------- #
# 场景 1：LLDP 已开启
# ---------------------------------------------------------------------- #
class TestAlreadyEnabled:
    """LLDP 已使能时，只发 1 条采集命令，不触发 enable。"""

    def test_only_display_called(self) -> None:
        session = _make_session(
            display_output="GE0/0/0  AR2  GE0/0/1  120\n<Huawei>",
        )
        sleeps: list[float] = []
        result = collect_lldp_neighbors_with_auto_enable(
            session, sleep_fn=sleeps.append
        )

        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["auto_enable_attempts"] == 0
        assert result["lldp_was_enabled"] is False
        # 只发了一次 display
        assert session.send_command.call_count == 1
        assert session.send_command.call_args.args[0] == "display lldp neighbor brief"
        # enable 命令序列不该被调用
        session.send_commands.assert_not_called()
        assert sleeps == []  # 没等任何东西


# ---------------------------------------------------------------------- #
# 场景 2：LLDP 未开启 + auto_enable=True（默认）
# ---------------------------------------------------------------------- #
NOT_ENABLED_OUTPUT = "Error: LLDP is not enabled globally.\n<Huawei>"


class TestAutoEnableSuccess:
    """LLDP 未使能，auto_enable=True → 自动使能 + 等 retry_delay + 重采。"""

    def test_full_auto_enable_flow(self) -> None:
        session = _make_session(
            display_output=NOT_ENABLED_OUTPUT,
            second_display_output="GE0/0/0  AR2  GE0/0/1  120\n<Huawei>",
        )
        sleeps: list[float] = []

        result = collect_lldp_neighbors_with_auto_enable(
            session, auto_enable=True, enable_retry_delay=2.5, sleep_fn=sleeps.append
        )

        # 结果：第二次采集成功，返回 1 个邻居
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["auto_enable_attempts"] == 1
        assert result["lldp_was_enabled"] is True
        # 调用顺序：display → send_commands（3 条）→ sleep → display
        assert session.send_command.call_count == 2
        session.send_commands.assert_called_once()
        assert session.send_commands.call_args.args[0] == [
            "system-view",
            "lldp enable",
            "quit",
        ]
        assert sleeps == [2.5]

    def test_detect_chinese_not_enabled(self) -> None:
        """中文回显「LLDP功能未使能」也能触发自动使能。"""
        session = _make_session(
            display_output="LLDP功能未使能\n<Huawei>",
            second_display_output="GE0/0/0  AR2  GE0/0/1  120\n<Huawei>",
        )
        result = collect_lldp_neighbors_with_auto_enable(
            session, sleep_fn=lambda _: None
        )
        assert result["status"] == "ok"
        assert result["auto_enable_attempts"] == 1
        assert session.send_commands.called

    def test_sleep_clamped_to_zero(self) -> None:
        """retry_delay ≤ 0 时不发 sleep（避免无意义等待）。"""
        session = _make_session(
            display_output=NOT_ENABLED_OUTPUT,
            second_display_output="GE0/0/0  AR2  GE0/0/1  120\n<Huawei>",
        )
        sleeps: list[float] = []
        result = collect_lldp_neighbors_with_auto_enable(
            session, enable_retry_delay=-1.0, sleep_fn=sleeps.append
        )
        assert result["status"] == "ok"
        assert sleeps == [0.0]  # clamped to 0


# ---------------------------------------------------------------------- #
# 场景 3：LLDP 未开启 + auto_enable=False
# ---------------------------------------------------------------------- #
class TestAutoEnableDisabled:
    """auto_enable=False 时跳过使能步骤，直接报失败。"""

    def test_no_enable_attempted(self) -> None:
        session = _make_session(display_output=NOT_ENABLED_OUTPUT)
        result = collect_lldp_neighbors_with_auto_enable(
            session, auto_enable=False, sleep_fn=lambda _: None
        )

        assert result["status"] == "error"
        assert result["errored"] is True
        assert result["auto_enable_attempts"] == 0
        assert result["lldp_was_enabled"] is False
        # 只发了一次 display
        assert session.send_command.call_count == 1
        session.send_commands.assert_not_called()
        # hint 应提示用户 LLDP 未使能
        assert "LLDP" in result["hint"]


# ---------------------------------------------------------------------- #
# 场景 4：自动使能后重采仍失败
# ---------------------------------------------------------------------- #
class TestAutoEnableStillFails:
    """自动使能成功，但第二次采集仍然报错 → 返回 errored + lldp_was_enabled=True。"""

    def test_returns_error(self) -> None:
        session = _make_session(
            display_output=NOT_ENABLED_OUTPUT,
            second_display_output=NOT_ENABLED_OUTPUT,  # 重采仍失败
        )
        result = collect_lldp_neighbors_with_auto_enable(
            session, sleep_fn=lambda _: None
        )

        assert result["status"] == "error"
        assert result["errored"] is True
        assert result["auto_enable_attempts"] == 1
        assert result["lldp_was_enabled"] is True
        # 仍提示可能原因
        assert "hint" in result


# ---------------------------------------------------------------------- #
# 场景 5：第一次采集就 Telnet 抛异常
# ---------------------------------------------------------------------- #
class TestTelnetExceptionOnFirstSend:
    def test_returns_error(self) -> None:
        session = MagicMock()
        session.name = "AR1"
        session.send_command.side_effect = ConnectionError("telnet closed")

        result = collect_lldp_neighbors_with_auto_enable(
            session, sleep_fn=lambda _: None
        )

        assert result["status"] == "error"
        assert result["errored"] is True
        assert "telnet closed" in result["error_message"]
        assert result["auto_enable_attempts"] == 0
        assert result["lldp_was_enabled"] is False


# ---------------------------------------------------------------------- #
# 场景 6：第一次 send_command 报 errored=True（VRP 错误）
# ---------------------------------------------------------------------- #
class TestVRPErroredOnFirstSend:
    def test_triggers_auto_enable(self) -> None:
        """命令层报 errored=True 也是「需要自动使能」的信号。"""
        session = _make_session(
            display_output="Error: Unrecognized command\n<Huawei>",
            display_errored=True,
            second_display_output="GE0/0/0  AR2  GE0/0/1  120\n<Huawei>",
        )
        result = collect_lldp_neighbors_with_auto_enable(
            session, sleep_fn=lambda _: None
        )

        # errored=True 也应触发自动使能
        assert session.send_commands.called
        assert result["status"] == "ok"


# ---------------------------------------------------------------------- #
# 场景 7：detail=True
# ---------------------------------------------------------------------- #
class TestDetailCommand:
    def test_detail_uses_full_command(self) -> None:
        session = _make_session(
            display_output=(
                "LLDP neighbor-information of port GigabitEthernet0/0/0:\n"
                "Neighbor index : 1\n"
                "Device ID      : 0030-1122-3344\n"
                "Port ID        : GigabitEthernet0/0/1\n"
                "System name    : AR2\n"
                "TTL            : 120\n"
                "<Huawei>\n"
            ),
        )
        result = collect_lldp_neighbors_with_auto_enable(
            session, detail=True, sleep_fn=lambda _: None
        )
        assert result["status"] == "ok"
        assert result["command"] == "display lldp neighbor"
        assert session.send_command.call_args.args[0] == "display lldp neighbor"
