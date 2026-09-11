"""精简探查（后台运行）：每台路由器读取 sysname / 接口 / OSPF 邻居，写日志。"""

import sys

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient

HOST = "127.0.0.1"
DEVICES = {"AR1": 2000, "AR2": 2001, "AR3": 2002, "AR4": 2003}
CMDS = [
    "display current-configuration | include sysname",
    "display ip interface brief",
    "display ospf peer brief",
]


def main() -> None:
    for name, port in DEVICES.items():
        print("\n" + "=" * 70)
        print(f"设备 {name} ({HOST}:{port})")
        print("=" * 70)
        try:
            c = TelnetClient(HOST, port, connect_timeout=4.0, read_timeout=2.0)
            c.connect()
            c.activate()
            c.wait_for_prompt(timeout=6.0)
            for cmd in CMDS:
                r = c.send_command(cmd, timeout=8.0)
                print(f"\n-- {cmd} (errored={r.errored}) --")
                for ln in r.output.splitlines()[:40]:
                    print(ln)
            c.close()
        except Exception as e:  # noqa: BLE001
            print(f"  !! 失败: {e!r}")


if __name__ == "__main__":
    main()
    print("\n[FAST PROBE DONE]")
