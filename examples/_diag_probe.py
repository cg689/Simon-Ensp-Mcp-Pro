"""只读探查：登录每台可达路由器，读取版本/主机名/已有配置/接口/OSPF 邻居。

仅执行 display 类命令，不发送任何配置。
"""

import sys

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.telnet_client import TelnetClient

HOST = "127.0.0.1"
DEVICES = {
    "AR1": 2000,
    "AR2": 2001,
    "AR3": 2002,
    "AR4": 2003,
}

PROBES = [
    "display version",
    "display current-configuration",
    "display ip interface brief",
    "display ospf peer brief",
]


def main() -> None:
    for name, port in DEVICES.items():
        print("\n" + "=" * 72)
        print(f"设备 {name} (telnet {HOST}:{port})")
        print("=" * 72)
        try:
            client = TelnetClient(HOST, port, connect_timeout=4.0, read_timeout=3.0)
            client.connect()
            client.activate()
            client.wait_for_prompt(timeout=6.0)
            for cmd in PROBES:
                res = client.send_command(cmd, timeout=15.0)
                print(f"\n----- {cmd}  (errored={res.errored}) -----")
                # 仅打印前 60 行，避免刷屏
                lines = res.output.splitlines()
                for ln in lines[:60]:
                    print(ln)
                if len(lines) > 60:
                    print(f"... (共 {len(lines)} 行，截断)")
            client.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  !! 连接/读取失败: {exc}")


if __name__ == "__main__":
    main()
