"""最小化单设备连通性测试，直接打印，用于定位 activate 修复后的行为。"""

import sys
import traceback

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient

print("import OK", flush=True)
try:
    c = TelnetClient("127.0.0.1", 2000, connect_timeout=4.0, read_timeout=2.0)
    print("connecting...", flush=True)
    c.connect()
    print("connected", flush=True)
    c.activate()
    print(f"activated, buffer={c._buffer[:80]!r}", flush=True)
    p = c.wait_for_prompt(timeout=6.0)
    print(f"prompt={p!r}", flush=True)
    r = c.send_command("display version", timeout=10.0)
    print(f"display version errored={r.errored} len={len(r.output)}", flush=True)
    print(r.output[:300], flush=True)
    c.close()
    print("DONE", flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(2)
