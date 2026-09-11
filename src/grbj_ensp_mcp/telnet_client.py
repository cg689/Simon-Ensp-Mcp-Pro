"""基于 socket 的轻量级 Telnet 客户端。

仅实现与 eNSP 设备交互所必需的最小子集：发送文本、读取到提示符、维持心跳，
并对 Windows 控制台常用的 GBK 编码做正确处理。Python 3.13 已移除 ``telnetlib``，
因此这里直接基于标准库 ``socket`` 实现。
"""

from __future__ import annotations

import contextlib
import re
import socket
import threading
import time
from dataclasses import dataclass, field

# Telnet 协议常量
IAC = b"\xff"  # Interpret As Command
DONT = b"\xfe"
DO = b"\xfd"
WONT = b"\xfc"
WILL = b"\xfb"
SB = b"\xfa"  # Sub-option Begin
SE = b"\xf0"  # Sub-option End

# 常见 eNSP / 华为设备提示符正则：<...> 或 [...] 或 HUAWEI>
# 例如 <AR1>、<Huawei>、[AR1]、HUAWEI
# 提示符后允许换行 / 空白 / 状态标记
_PROMPT_RE = re.compile(
    rb"(?:<[^>]+>|\[[^\]]+\]|HUAWEI)(?:\([^)]*\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# 退到用户视图的提示符：<...> 或 <HUAWEI>
_USER_VIEW_PROMPT_RE = re.compile(rb"<[^>]+>\s*$")


def _strip_telnet_commands(data: bytes) -> bytes:
    """剔除 IAC 协商字节，保留可显示的净数据。

    提升为模块级纯函数，便于单元测试直接调用。
    """
    out = bytearray()
    i = 0
    n = len(data)
    do_byte = DO[0]
    dont_byte = DONT[0]
    will_byte = WILL[0]
    wont_byte = WONT[0]
    sb_byte = SB[0]
    while i < n:
        b = data[i]
        if b == 0xFF and i + 2 < n:
            cmd = data[i + 1]
            if cmd in (do_byte, dont_byte, will_byte, wont_byte):
                # 三字节协商命令
                i += 3
                continue
            if cmd == sb_byte:
                # 子选项，直到 SE 结束
                j = data.find(SE, i + 2)
                if j == -1:
                    return bytes(out)
                i = j + 1
                continue
        out.append(b)
        i += 1
    return bytes(out)


class TelnetError(Exception):
    """Telnet 会话相关的基础异常。"""


class TelnetConnectionError(TelnetError):
    """连接建立或维持过程中出错。"""


class TelnetTimeoutError(TelnetError):
    """读取 / 等待操作超时。"""


@dataclass
class TelnetResult:
    """一次 send-and-read 操作的结果。"""

    command: str
    output: str
    elapsed: float
    errored: bool = False
    errors: list[str] = field(default_factory=list)


# 华为设备命令报错的回显格式（行首 ``Error:`` 后跟具体类别）：
#   Error: Unrecognized command found at '^' position.
#   Error: Incomplete command found at '^' position.
#   Error: Wrong parameter found at '^' position.
#   Error: Too many parameters found at '^' position.
#   Error: Ambiguous command found at '^' position.
# 仅匹配行首 ``Error:`` 且后面是上述类别，避免误判普通 display 输出里的 "error" 字样。
_COMMAND_ERROR_RE = re.compile(
    r"(?im)^\s*error:\s*(unrecognized|incomplete|wrong parameter|"
    r"too many parameters|ambiguous)\b.*$",
)


class TelnetClient:
    """简单的同步 Telnet 客户端。

    使用方式::

        client = TelnetClient("127.0.0.1", 2000)
        client.connect()
        client.activate()  # 触发回车激活会话
        client.wait_for_prompt()
        client.close()

    设计取舍：
    - 协议层：忽略所有 IAC 选项协商，对 eNSP 设备足够。
    - 并发：通过外部锁保证多线程安全；实例本身不维护后台线程。
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        encoding: str = "gbk",
        connect_timeout: float = 5.0,
        read_timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.encoding = encoding
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

        self._sock: socket.socket | None = None
        self._buffer: bytes = b""
        self._lock = threading.RLock()
        self._closed = True

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return not self._closed and self._sock is not None

    def connect(self) -> None:
        """建立 TCP 连接，失败时抛出 :class:`TelnetConnectionError`。"""
        with self._lock:
            if self.is_connected:
                return
            try:
                sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=self.connect_timeout,
                )
                sock.settimeout(self.read_timeout)
            except OSError as exc:
                raise TelnetConnectionError(f"无法连接到 {self.host}:{self.port}: {exc}") from exc
            self._sock = sock
            self._buffer = b""
            self._closed = False

    def close(self) -> None:
        """关闭底层 socket。幂等。"""
        with self._lock:
            if self._sock is not None:
                with contextlib.suppress(OSError):
                    self._sock.shutdown(socket.SHUT_RDWR)
                with contextlib.suppress(OSError):
                    self._sock.close()
                self._sock = None
            self._buffer = b""
            self._closed = True

    def __enter__(self) -> TelnetClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 数据收发
    # ------------------------------------------------------------------ #
    def send(self, data: str) -> None:
        """发送文本，自动附加换行符。"""
        with self._lock:
            if not self.is_connected:
                raise TelnetConnectionError("客户端未连接")
            payload = data.encode(self.encoding, errors="ignore") + b"\r\n"
            try:
                self._sock.sendall(payload)  # type: ignore[union-attr]
            except OSError as exc:
                raise TelnetConnectionError(f"发送失败: {exc}") from exc

    def _recv_some(self, max_bytes: int = 65535) -> bytes:
        if not self.is_connected or self._sock is None:
            raise TelnetConnectionError("客户端未连接")
        try:
            chunk = self._sock.recv(max_bytes)
        except TimeoutError:
            return b""
        except OSError as exc:
            raise TelnetConnectionError(f"接收失败: {exc}") from exc
        if not chunk:
            # 对端关闭
            self._closed = True
            raise TelnetConnectionError(f"对端关闭了连接 {self.host}:{self.port}")
        return chunk

    def read(self, timeout: float | None = None) -> str:
        """读取当前可用的所有数据，按 ``encoding`` 解码。

        阻塞直到至少一字节可读或超时。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            while True:
                if self._buffer:
                    break
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0.0:
                    return ""
                if self._sock is None:
                    return ""
                prev_timeout = self._sock.gettimeout()
                self._sock.settimeout(remaining)
                try:
                    chunk = self._recv_some()
                finally:
                    self._sock.settimeout(prev_timeout)
                if not chunk:
                    # 若是临时性 timeout，循环继续
                    if deadline is None or time.monotonic() < deadline:
                        continue
                    return ""
                self._buffer += _strip_telnet_commands(chunk)

            data, self._buffer = self._buffer, b""
            return data.decode(self.encoding, errors="ignore")

    def read_until(
        self,
        pattern: bytes | str | re.Pattern[bytes],
        timeout: float = 10.0,
    ) -> str:
        """持续读取直到匹配 ``pattern`` 或超时。

        ``pattern`` 支持：

        - ``str``：按字面匹配（编码后比较）
        - ``bytes``：按字面匹配
        - :class:`re.Pattern`：按正则匹配（要求 pattern 是 ``bytes`` 模式）

        返回匹配前累积的全部输出。
        """
        if isinstance(pattern, str):
            pattern = pattern.encode(self.encoding, errors="ignore")
        if isinstance(pattern, re.Pattern):
            regex = pattern
        else:
            # 智能识别：只有以 ``b"(?"`` 开头的 bytes 才视为正则
            if pattern.startswith(b"(?"):
                regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)
            else:
                regex = re.compile(re.escape(pattern), re.IGNORECASE | re.DOTALL)

        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        # 用 sentinel 解决"buffer 已有匹配时第一次 break 后 remaining 未定义"的问题。
        remaining: float | None = None
        with self._lock:
            while True:
                if regex.search(self._buffer):
                    idx = regex.search(self._buffer)
                    end = idx.end() if idx else len(self._buffer)
                    data = self._buffer[:end]
                    self._buffer = self._buffer[end:]
                    chunks.append(data)
                    break
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0.0:
                    chunks.append(self._buffer)
                    self._buffer = b""
                    raise TelnetTimeoutError(f"等待 {pattern!r} 超时（{timeout}s）")
            if self._sock is None:
                raise TelnetConnectionError("客户端未连接")
            # 匹配后再尝试多读一字节，避免陈旧 prompt 残留；timeout 用剩余预算或兜底 0。
            prev_timeout = self._sock.gettimeout()
            self._sock.settimeout(0 if remaining is None else remaining)
            try:
                chunk = self._recv_some()
            finally:
                self._sock.settimeout(prev_timeout)
            if chunk:
                cleaned = _strip_telnet_commands(chunk)
                self._buffer += cleaned
                chunks.append(cleaned)

        return b"".join(chunks).decode(self.encoding, errors="ignore")

    # ------------------------------------------------------------------ #
    # 设备交互辅助
    # ------------------------------------------------------------------ #
    def activate(self, retries: int = 3) -> None:
        """对 eNSP 设备连续发送回车以激活会话。

        eNSP 设备启动后不会立即打印提示符，需要按回车触发。

        注意：回显会保留在 ``self._buffer`` 中（不会像 :meth:`read` 那样清空），
        以便随后的 :meth:`wait_for_prompt` 或 :meth:`send_command` 能直接识别提示符。
        """
        for attempt in range(retries):
            try:
                self.send("")
                time.sleep(0.4)
                if self._sock is None:
                    return
                prev_timeout = self._sock.gettimeout()
                self._sock.settimeout(1.5)
                try:
                    chunk = self._recv_some()
                finally:
                    self._sock.settimeout(prev_timeout)
                if chunk:
                    self._buffer += _strip_telnet_commands(chunk)
                if _PROMPT_RE.search(self._buffer):
                    return
            except TelnetConnectionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.5)

    def wait_for_prompt(self, timeout: float = 10.0) -> str:
        """读取数据直到出现常见设备提示符（<...> / [...] / HUAWEI）。

        返回最近一行提示符字符串。
        """
        deadline = time.monotonic() + timeout
        collected = bytearray()
        with self._lock:
            while True:
                collected.extend(self._buffer)
                self._buffer = b""
                match = _PROMPT_RE.search(bytes(collected))
                if match:
                    return match.group(0).decode(self.encoding, errors="ignore").strip()
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0.0:
                    raise TelnetTimeoutError("等待设备提示符超时")
                if self._sock is None:
                    raise TelnetConnectionError("客户端未连接")
                prev_timeout = self._sock.gettimeout()
                self._sock.settimeout(remaining)
                try:
                    chunk = self._recv_some()
                finally:
                    self._sock.settimeout(prev_timeout)
                if chunk:
                    collected.extend(_strip_telnet_commands(chunk))

    def send_command(
        self,
        command: str,
        *,
        wait_prompt: bool = True,
        timeout: float = 10.0,
    ) -> TelnetResult:
        """发送单条命令并（可选）等待提示符返回。

        返回 :class:`TelnetResult`，包含命令本身、输出、耗时，以及命令层报错标记
        ``errored`` / ``errors``（华为设备 ``Error: ...`` 回显）。
        """
        start = time.monotonic()
        self.send(command)
        if not wait_prompt:
            time.sleep(self.read_timeout)
            output = self.read(timeout=self.read_timeout)
            errs = [m.group(0).strip() for m in _COMMAND_ERROR_RE.finditer(output)]
            return TelnetResult(command, output, time.monotonic() - start, bool(errs), errs)
        try:
            output = self.read_until(_PROMPT_RE, timeout=timeout)
        except TelnetTimeoutError:
            output = self.read(timeout=0.5)
        errs = [m.group(0).strip() for m in _COMMAND_ERROR_RE.finditer(output)]
        return TelnetResult(command, output, time.monotonic() - start, bool(errs), errs)

    def enter_system_view(self, timeout: float = 8.0) -> TelnetResult:
        """进入 system-view，等价于 ``system-view``。"""
        return self.send_command("system-view", timeout=timeout)

    def save_config(self, timeout: float = 8.0) -> list[TelnetResult]:
        """触发 ``save`` 流程：先 ``quit`` 退到用户视图，再 ``save`` 并确认 ``y``。"""
        results: list[TelnetResult] = []
        results.append(self.send_command("quit", timeout=timeout))
        # 在用户视图检测到提示符后再 save
        time.sleep(0.3)
        results.append(self.send_command("save", timeout=timeout))
        # 设备会问 The current configuration will be written to the device.
        # Are you sure to continue?[Y/N]:
        time.sleep(0.3)
        results.append(self.send_command("y", timeout=timeout))
        time.sleep(0.3)
        # 之后可能问 File name (leave empty for default): 直接回车
        results.append(self.send_command("", timeout=timeout))
        return results
