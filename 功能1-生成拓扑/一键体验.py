"""一键体验：30 秒生成一个 eNSP 能直接打开的拓扑文件。

零安装——直接读仓库 src/ 里的源码，不需要 pip install 任何东西。
用法（在仓库根目录）：
    python "功能1-生成拓扑/一键体验.py"

跑完会在当前目录生成 体验拓扑.topo，拖进 eNSP 即可看到效果。
"""
import sys
import pathlib

# 关键：把仓库 src 加入搜索路径（零安装的秘密）
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder

OUT = REPO_ROOT / "体验拓扑.topo"

# ---- 设备表：(名字, 型号, Telnet端口[无CLI填0], x, y) ----
DEVICES = [
    ("R1",      "AR2220", 2000, 300, 150),
    ("R2",      "AR2220", 2001, 500, 150),
    ("SW1",     "S5700",  2002, 400, 320),
    ("PC-教学",  "PC",     0,    300, 470),
    ("PC-办公",  "PC",     0,    500, 470),
]

# ---- 连线表：(设备A, 接口A, 设备B, 接口B, 备注) —— 只写接口名，编号自动换算 ----
LINES = [
    ("R1", "GE0/0/0", "SW1", "GE0/0/1", "R1 下联交换机"),
    ("R2", "GE0/0/0", "SW1", "GE0/0/2", "R2 下联交换机"),
    ("PC-教学", "Ethernet0/0/1", "SW1", "GE0/0/3", "教学 PC"),
    ("PC-办公", "Ethernet0/0/1", "SW1", "GE0/0/4", "办公 PC"),
]


def main() -> None:
    b = NativeTopoBuilder()
    for name, model, port, x, y in DEVICES:
        b.add_device(name, model=model, com_port=port, cx=x, cy=y)
    name2dev = {d.name: d.model for d in b._devices}

    # 接口名 → eNSP 内部编号（按型号换算，这正是手算最容易错的地方）
    def idx(model: str, ifname: str) -> int:
        num = int(ifname.rsplit("/", 1)[-1])
        if model == "AR2220":            return num          # GE0/0/N → N（0 起）
        if model == "S5700":             return num - 1      # GE0/0/N → N-1（1 起）
        if model == "PC":                return 0            # 单口
        raise ValueError(f"未支持的型号 {model}，请在模板脚本里查换算表")

    for a, ia, bb, ib, desc in LINES:
        b.add_line(a, bb, src_index=idx(name2dev[a], ia),
                   tar_index=idx(name2dev[bb], ib))

    errors = b.validate()
    if errors:
        print("builder 校验失败：", *errors, sep="\n  - ")
        sys.exit(1)

    xml = b.render()
    # 落盘三要素（照抄勿改）：UNICODE 假声明 + CRLF + GBK —— eNSP 是老 Win32 程序，
    # 声明是硬编码假象，实际按 ANSI/GBK 读；UTF-8 落盘中文必乱码。
    xml = xml.replace('encoding="UTF-8"', 'encoding="UNICODE"')
    OUT.write_bytes(xml.replace("\n", "\r\n").encode("gbk"))

    print(f"[1] 生成 OK -> {OUT.name}（设备 {len(DEVICES)} 台，连线 {len(LINES)} 条）")
    print("[2] 下一步：把 体验拓扑.topo 拖进 eNSP 窗口，确认设备与连线渲染正常")
    print("[3] 想生成自己的拓扑？复制本目录的 模板脚本.py 改两个表格即可")
    print("ALL PASS")


if __name__ == "__main__":
    main()
