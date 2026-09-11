"""多设备并发会话管理。

``DeviceSession`` 包装 ``TelnetClient`` 并维护一个设备的状态（提示符位置、输出缓冲、元信息）。
``SessionManager`` 负责创建、查询、关闭所有活跃会话。
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .config import EnspConfig, load_config
from .telnet_client import (
    TelnetClient,
    TelnetError,
    TelnetResult,
)


class SessionStatus(str, Enum):
    """会话生命周期状态。"""

    NEW = "new"
    CONNECTING = "connecting"
    ACTIVE = "active"
    IDLE = "idle"
    CLOSED = "closed"
    ERROR = "error"


class SessionError(Exception):
    """会话级错误。"""


@dataclass
class DeviceInfo:
    """从 eNSP 设备上提取到的元信息。

    字段均为可选：在未调用 ``refresh_info`` 前是空的。
    """

    hostname: str | None = None
    device_type: str | None = None  # e.g. "AR2220"
    software_version: str | None = None
    uptime: str | None = None


@dataclass
class DeviceSession:
    """代表一个已建立的 eNSP 设备会话。

    所有公开方法都是线程安全的（内部使用 ``RLock`` 保护状态变更）。
    """

    name: str
    host: str
    port: int
    config: EnspConfig
    client: TelnetClient = field(init=False)
    status: SessionStatus = SessionStatus.NEW
    info: DeviceInfo = field(default_factory=DeviceInfo)
    in_system_view: bool = False
    last_error: str | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    output_buffer: deque[str] = field(init=False)

    _lock: threading.RLock = field(init=False, repr=False)
    _max_buffer_lines: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._max_buffer_lines = self.config.session_buffer_size
        self.output_buffer = deque(maxlen=self._max_buffer_lines)
        self.client = TelnetClient(
            host=self.host,
            port=self.port,
            encoding=self.config.encoding,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
        )

    # ------------------------------------------------------------------ #
    # 状态辅助
    # ------------------------------------------------------------------ #
    def _touch(self) -> None:
        self.last_active = time.time()

    def _append_output(self, text: str) -> None:
        if not text:
            return
        for line in text.splitlines():
            if line:
                self.output_buffer.append(line)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active

    def snapshot(self) -> dict[str, object]:
        """返回会话的 JSON 可序列化快照，便于 MCP 资源返回。"""
        with self._lock:
            return {
                "session_id": self.session_id,
                "name": self.name,
                "host": self.host,
                "port": self.port,
                "status": self.status.value,
                "in_system_view": self.in_system_view,
                "info": {
                    "hostname": self.info.hostname,
                    "device_type": self.info.device_type,
                    "software_version": self.info.software_version,
                    "uptime": self.info.uptime,
                },
                "last_error": self.last_error,
                "created_at": self.created_at,
                "last_active": self.last_active,
                "idle_seconds": self.idle_seconds,
                "buffer_lines": len(self.output_buffer),
            }

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def open(self, *, auto_refresh: bool = True) -> None:
        """建立 Telnet 连接、激活会话并（可选）刷新设备信息。"""
        with self._lock:
            self.status = SessionStatus.CONNECTING
            self.last_error = None
            try:
                self.client.connect()
                # activate 按回车激活会话，并把激活后的首条提示符保留在缓冲区；
                # 必须再用 wait_for_prompt 将其消费掉，否则紧随其后的第一条
                # send_command 会通过 read_until(_PROMPT_RE) 误匹配这条陈旧提示符，
                # 返回错位输出（命令回显整体偏移一格）。consumed 失败时说明设备未就绪。
                self.client.activate()
                self.client.wait_for_prompt()
            except TelnetError as exc:
                self.status = SessionStatus.ERROR
                self.last_error = str(exc)
                self.client.close()
                raise
            self.status = SessionStatus.ACTIVE
            self._touch()
        if auto_refresh:
            with contextlib.suppress(TelnetError):
                # 信息收集失败不阻塞会话创建
                self.refresh_info()

    def close(self) -> None:
        with self._lock:
            self.client.close()
            self.status = SessionStatus.CLOSED
            self._touch()

    # ------------------------------------------------------------------ #
    # 命令交互
    # ------------------------------------------------------------------ #
    def _detect_prompt_state(self, output: str) -> None:
        """根据最近一段输出判断是否处于 system-view。"""
        # 简易启发：system-view 下的提示符形如 [HUAWEI]；用户视图形如 <HUAWEI>
        if "[~HUAWEI]" in output and "]" in output and output.rstrip().endswith("]"):
            # [~HUAWEI] 或 [HUAWEI] 结尾表示仍在 system-view
            self.in_system_view = True
        elif output.rstrip().endswith("]") and "[" in output and "~" in output.splitlines()[-1]:
            self.in_system_view = True
        elif output.rstrip().endswith(">") and "<" not in output.splitlines()[-1]:
            self.in_system_view = False
        else:
            # 兜底：根据当前已知的 in_system_view 不做修改
            pass

    def send_command(
        self,
        command: str,
        *,
        wait: float | None = None,
        timeout: float = 10.0,
    ) -> TelnetResult:
        """发送单条命令并返回结果。

        ``wait`` 参数用于在两条命令之间加额外等待（覆盖默认 ``default_command_delay``）。
        """
        with self._lock:
            if self.status == SessionStatus.CLOSED:
                raise SessionError(f"会话 {self.name} 已关闭")
            try:
                result = self.client.send_command(command, timeout=timeout)
            except TelnetError as exc:
                self.status = SessionStatus.ERROR
                self.last_error = str(exc)
                raise
            self._append_output(result.output)
            self._detect_prompt_state(result.output)
            if command.strip() in {"system-view", "sys"}:
                self.in_system_view = True
            elif command.strip() in {"quit"} and self.in_system_view:
                # 退一层但不确定是否回 user-view，等下次 send 时再判断
                self.in_system_view = False
            self.status = SessionStatus.ACTIVE
            self._touch()
            if wait is not None:
                time.sleep(wait)
            else:
                time.sleep(self.config.default_command_delay)
            return result

    def send_commands(
        self,
        commands: list[str],
        *,
        stop_on_error: bool = False,
        long_command_delay: float | None = None,
    ) -> list[TelnetResult]:
        """批量下发命令。

        ``stop_on_error=True`` 时某条命令抛出会立刻中断后续；
        否则即使失败也会收集后继续。返回每条命令对应的 :class:`TelnetResult`。
        """
        results: list[TelnetResult] = []
        long_delay = long_command_delay or self.config.default_long_command_delay
        for cmd in commands:
            try:
                result = self.send_command(cmd)
            except TelnetError as exc:
                err_result = TelnetResult(cmd, f"<<ERROR: {exc}>>", 0.0)
                results.append(err_result)
                if stop_on_error:
                    break
                continue
            results.append(result)
            # 命令层报错（设备不支持该命令）时也按需中断
            if stop_on_error and result.errored:
                break
            # 长命令（写入 flash、ping 等待）后多等一拍
            if cmd.startswith(("ping ", "tracert ", "save", "reboot")):
                time.sleep(long_delay)
        return results

    def save(self) -> list[TelnetResult]:
        """保存配置，等价于 ``quit`` + ``save`` + 确认 ``y``。"""
        with self._lock:
            return self.client.save_config()

    def get_recent_output(self, lines: int = 200) -> str:
        """返回输出缓冲中最近 ``lines`` 行。"""
        with self._lock:
            data = list(self.output_buffer)[-lines:]
        return "\n".join(data)

    def clear_buffer(self) -> None:
        with self._lock:
            self.output_buffer.clear()

    def refresh_info(self) -> DeviceInfo:
        """通过 ``display version`` 提取设备元信息，解析失败时返回已有值。"""
        commands = [
            "display version",
        ]
        results = self.send_commands(commands)
        combined = "\n".join(r.output for r in results)

        hostname = _extract_field(combined, r"HUAWEI\s+(\S+)\s+uptime")
        device_type = _extract_field(combined, r"(AR\d+|S\d+|S\d+SI|USG\d+|AC\d+|NE\w+)")
        software_version = _extract_field(
            combined, r"VRP\s+\(R\)\s+software[,\s]+Version\s+([\w.\(\)]+)"
        )
        if not software_version:
            software_version = _extract_field(combined, r"Version\s+([\w.\(\)]+)")
        uptime = _extract_field(combined, r"uptime\s+is\s+([^\r\n]+)")

        with self._lock:
            if hostname:
                self.info.hostname = hostname
            if device_type:
                self.info.device_type = device_type
            if software_version:
                self.info.software_version = software_version
            if uptime:
                self.info.uptime = uptime
            return self.info


# ---------------------------------------------------------------------- #
# 帮助函数
# ---------------------------------------------------------------------- #
def _extract_field(text: str, pattern: str) -> str | None:
    import re

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


# ---------------------------------------------------------------------- #
# SessionManager
# ---------------------------------------------------------------------- #
class SessionManager:
    """进程级单例：管理所有 :class:`DeviceSession`。

    支持按 ``name`` / ``session_id`` / ``(host, port)`` 多种方式检索。
    """

    def __init__(self, config: EnspConfig | None = None) -> None:
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = threading.RLock()
        self._config = config or load_config()

    @property
    def config(self) -> EnspConfig:
        return self._config

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def list_sessions(self) -> list[dict[str, object]]:
        with self._lock:
            return [s.snapshot() for s in self._sessions.values()]

    def _key_for(self, host: str, port: int, name: str) -> str:
        return f"{name}@{host}:{port}"

    def _find_existing(self, host: str, port: int) -> DeviceSession | None:
        with self._lock:
            for session in self._sessions.values():
                if session.host == host and session.port == port:
                    return session
        return None

    def get(self, session_id_or_name: str) -> DeviceSession:
        with self._lock:
            for session in self._sessions.values():
                if session.session_id == session_id_or_name:
                    return session
                if session.name == session_id_or_name:
                    return session
        raise SessionError(f"未找到会话: {session_id_or_name}")

    def create(
        self,
        host: str | None = None,
        port: int = 2000,
        *,
        name: str | None = None,
        reuse: bool = True,
        auto_refresh: bool = True,
    ) -> DeviceSession:
        """建立新会话。

        - ``host`` 默认使用全局配置的主机。
        - ``port`` 必填。
        - ``name`` 默认是 ``f"device-{port}"``。
        - 当 ``reuse=True`` 时若已存在同 ``(host, port)`` 的活跃会话则直接返回。
        """
        actual_host = host or self._config.host
        if not (1 <= port <= 65535):
            raise SessionError(f"端口号非法: {port}")
        session_name = name or f"device-{port}"

        with self._lock:
            if reuse:
                existing = self._find_existing(actual_host, port)
                if existing is not None and existing.status != SessionStatus.CLOSED:
                    return existing
            session = DeviceSession(
                name=session_name,
                host=actual_host,
                port=port,
                config=self._config,
            )
            self._sessions[session.session_id] = session

        try:
            session.open(auto_refresh=auto_refresh)
        except Exception:
            with self._lock:
                self._sessions.pop(session.session_id, None)
            raise
        return session

    def close(self, session_id_or_name: str) -> None:
        session = self.get(session_id_or_name)
        session.close()
        with self._lock:
            self._sessions.pop(session.session_id, None)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            with contextlib.suppress(Exception):
                session.close()
        with self._lock:
            self._sessions.clear()

    def cleanup_idle(self, idle_seconds: float = 300.0) -> list[str]:
        """关闭闲置超过 ``idle_seconds`` 的会话，返回被关闭的 session_id 列表。"""
        closed: list[str] = []
        with self._lock:
            targets = [
                s
                for s in self._sessions.values()
                if s.idle_seconds >= idle_seconds and s.status != SessionStatus.CLOSED
            ]
        for session in targets:
            with contextlib.suppress(Exception):
                session.close()
            with self._lock:
                self._sessions.pop(session.session_id, None)
            closed.append(session.session_id)
        return closed


# 进程级单例
_manager: SessionManager | None = None
_manager_lock = threading.Lock()


def get_session_manager(config: EnspConfig | None = None) -> SessionManager:
    """获取进程级 ``SessionManager`` 单例。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager(config=config)
        return _manager


def reset_session_manager() -> None:
    """关闭并丢弃单例，主要用于测试。"""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close_all()
        _manager = None
