"""针对性修复 AR1 的 LAN：真实拓扑中 AR1 的 GE0/0/2 未接设备，protocol 永远 down，
OSPF 不会通告该网段。将 192.168.1.0/24 改到 Loopback0（始终 up）后重通告。"""

import sys
import time

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")
from grbj_ensp_mcp.telnet_client import TelnetClient

HOST = "127.0.0.1"


def sess(port: int) -> TelnetClient:
    c = TelnetClient(HOST, port, connect_timeout=5.0, read_timeout=2.0)
    c.connect()
    c.activate()
    c.wait_for_prompt(timeout=6.0)
    return c


def main() -> None:
    c = sess(2000)
    print("== AR1 修复：清除 GE0/0/2 旧 IP，LAN 改到 Loopback0 ==")
    c.send_command("system-view", timeout=8.0)
    c.send_command("interface GigabitEthernet0/0/2", timeout=5.0)
    r = c.send_command("undo ip address", timeout=5.0)
    print(f"undo ip GE0/0/2 errored={r.errored} {r.errors}")
    c.send_command("quit", timeout=5.0)
    c.send_command("interface LoopBack0", timeout=5.0)
    r = c.send_command("ip address 192.168.1.1 255.255.255.0", timeout=5.0)
    print(f"LoopBack0 ip errored={r.errored} {r.errors}")
    c.send_command("quit", timeout=5.0)
    c.send_command("quit", timeout=5.0)  # 退回用户视图
    c.save_config(timeout=8.0)
    c.close()

    time.sleep(3)
    print("\n== 校验 ==")
    c = sess(2000)
    print("--- AR1 OSPF 路由 ---")
    print(c.send_command("display ip routing-table protocol ospf", timeout=10.0).output)
    c.close()

    for port, who in ((2001, "AR2"), (2002, "AR3"), (2003, "AR4(PC)")):
        cc = sess(port)
        r = cc.send_command("ping 192.168.1.1", timeout=12.0)
        loss = "?"
        for ln in r.output.splitlines():
            if "loss" in ln.lower():
                loss = ln.strip()
        print(f"[ping] {who} -> 192.168.1.1: {loss}")
        cc.close()

    print("\n[FIX AR1 LAN DONE]")


if __name__ == "__main__":
    main()
