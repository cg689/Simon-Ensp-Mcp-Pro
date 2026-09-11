"""原始抓包：连接后读取若干秒原始回显，诊断 eNSP 会话为何未出现提示符。"""

import sys
import time

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 2000

client = TelnetClient(HOST, PORT, connect_timeout=5.0, read_timeout=2.0)
client.connect()
print(f"[connected] {HOST}:{PORT}")

# 连续发送少量回车，模拟激活
for i in range(3):
    try:
        client.send("")
    except Exception as e:  # noqa: BLE001
        print(f"[send err {i}] {e}")
    time.sleep(0.6)
    try:
        chunk = client.read(timeout=2.0)
    except Exception as e:  # noqa: BLE001
        chunk = f"<read err {e}>"
    print(f"--- after Enter #{i + 1} ---")
    print(repr(chunk[:2000]))

# 再尝试一条 display 命令
try:
    client.send("display version")
except Exception as e:  # noqa: BLE001
    print(f"[send display err] {e}")
time.sleep(1.5)
try:
    chunk = client.read(timeout=3.0)
except Exception as e:  # noqa: BLE001
    chunk = f"<read err {e}>"
print("--- after display version ---")
print(repr(chunk[:2000]))

client.close()
