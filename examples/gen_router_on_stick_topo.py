"""用 :class:`NativeTopoBuilder` 生成单臂路由验证拓扑的示例脚本。

本脚本**只打印 XML 到 stdout**，不写任何文件。
把打印结果复制并由专用写入工具（Write/Edit）落地到
``examples/topologies/router_on_a_stick.topo`` 即可。

为何不直接生成文件？
    用户工作流要求所有文件修改必须走专用工具（Write/Edit），不得用脚本
    间接修改文件。本脚本严格只读、只计算，把 .topo 内容打印出来由用户决定
    是否落地、落地到哪里——避免脚本越过工作流边界。

如果想让 AI 一次完成「生成 + 落地」，正确做法是：
    1. 运行本脚本（它 print XML）
    2. AI 把 print 内容用 Write 工具写入目标 .topo 路径
    3. AI 立即调用 ensp_load_topology 等 MCP 工具校验 eNSP 可渲染性
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:/01_Workspace/code/ensp-mcp/src")

from grbj_ensp_mcp.topo_builder import NativeTopoBuilder
from grbj_ensp_mcp.topo_parser import parse_topo_bytes


def build_router_on_a_stick_xml() -> str:
    """构造 4 设备单臂路由拓扑，返回完整 eNSP 原生格式 XML 字符串。"""
    b = NativeTopoBuilder()

    # 设备清单（com_port 严格 2000 → 2001 递增）
    b.add_device("AR1", model="AR2240", com_port=2000, cx=400, cy=100)
    b.add_device("SW1", model="S5700", com_port=2001, cx=400, cy=300)
    b.add_device("PC1", model="PC", com_port=0, cx=200, cy=500)
    b.add_device("PC2", model="PC", com_port=0, cx=600, cy=500)

    # 连线（S5700 的 XML src_index 为 0-based，设备真实接口号需 +1）
    #   AR1.GE0/0/0 ↔ SW1.GE0/0/2（Trunk，承载 VLAN 10 / 20 子接口）
    #   SW1.GE0/0/3 ↔ PC1.Ethernet0/0/0（Access VLAN 10）
    #   SW1.GE0/0/4 ↔ PC2.Ethernet0/0/0（Access VLAN 20）
    b.add_line("AR1", "SW1", src_index=0, tar_index=1)
    b.add_line("SW1", "PC1", src_index=2, tar_index=0)
    b.add_line("SW1", "PC2", src_index=3, tar_index=0)

    return b.render()


def main() -> None:
    xml = build_router_on_a_stick_xml()

    # 自检：用 parser 解析刚生成的 XML，确认 eNSP GUI 可渲染
    topo = parse_topo_bytes(xml.encode("utf-8"), name="router_on_a_stick")
    if not topo.ensp_gui_renderable:
        print(
            "FATAL: builder 输出仍不可被 eNSP GUI 渲染，问题：",
            topo.ensp_gui_render_issues,
            file=sys.stderr,
        )
        sys.exit(1)

    print(xml)


if __name__ == "__main__":
    main()
