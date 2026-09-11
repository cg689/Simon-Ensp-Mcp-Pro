# AGENTS.md — AI 助手执行手册（本文件是给你看的，不是给用户看的）

用户把本仓库交给你的典型场景：**"我想用这个项目搭建一个拓扑"**。你不需要用户会跑命令——照本手册直接替他干完，把结果文件交给他。

## 第 0 步：判断用户要哪个功能

| 用户说的话 | 走哪条路 |
|---|---|
| "搭一个拓扑 / 画个拓扑 / 生成 .topo，设备是 xxx" | → **功能 A：生成拓扑**（最常见，无需任何安装） |
| "拓扑已经启动了，帮我配置 / 下发 OSPF / 配 VLAN" | → **功能 B：配置下发**（需要 MCP 已注册） |
| "报错了 / 装好了吗 / 帮我检查环境" | → **功能 C：自检与排错** |

不确定就先问一句"你是要生成拓扑文件，还是设备已经启动了要配配置？"

---

## 功能 A：生成拓扑（零安装，优先走这条）

**不需要 pip install**。把仓库 `src/` 加入 `sys.path` 即可（见下方代码第 4~6 行）。

### 标准工作流（照抄，已实测）

```python
import sys, pathlib
REPO_ROOT = pathlib.Path(r"C:\Users\你\下载\Simon-Ensp-Mcp-Pro")  # ← 改成仓库实际所在目录
sys.path.insert(0, str(REPO_ROOT / "src"))           # ← 零安装的关键
from grbj_ensp_mcp.topo_builder import NativeTopoBuilder

b = NativeTopoBuilder()
# 设备表：(名字, 型号, Telnet端口[无CLI填0], x, y)
b.add_device("R1", model="AR2220", com_port=2000, cx=300, cy=150)
b.add_device("SW1", model="S5700",  com_port=2002, cx=400, cy=320)
# 连线：src_index/tar_index 用下面的换算表算，不要猜
b.add_line("R1", "SW1", src_index=0, tar_index=0)
b.validate()                          # 返回错误列表，非空必须停下报告
xml = b.render()
```

**落盘三要素（缺一必乱码/打不开）**：

```python
xml = xml.replace('encoding="UTF-8"', 'encoding="UNICODE"')  # 1. eNSP 硬编码假声明
OUT.write_bytes(xml.replace("\n", "\r\n").encode("gbk"))     # 2. CRLF  3. GBK 编码
```

### 接口名 → index 换算表（最易错环节）

| 型号 | 规则 | 例：GE0/0/3 → |
|---|---|---|
| AR1220/2220/2240 | index = 接口号（0 起） | 3 |
| USG6000V | 0=GE0/0/0 管理口；业务口 GE1/0/N → index=N+1 | GE1/0/3 → 4 |
| S5700/S6700 | index = 接口号 − 1（1 起） | 2 |
| S3700 | Ethernet0/0/N → N−1（0~21）；GE0/0/N → N+21（GE0/0/23~24 → 22~23） | — |
| AC6005 | 同 S5700 惯例（推定，交付时提醒用户核对） | — |
| PC/Server/STA/Laptop | 单口，index = 0 | 0 |

### 红线（违反会导致文件损坏或项目事故）

1. **不得修改 XML 给设备加口**——只生成与 eNSP GUI 默认形态一致的设备（USG6000V = 1 管理口 + 7 业务口）
2. **中文设备名必须 GBK 落盘**，UTF-8 会乱码
3. com_port 全局唯一、2000 起递增；PC/Server/STA/AP 无 CLI 填 0
4. 生成后必须**回读自检**（`from grbj_ensp_mcp.topo_parser import parse_topo`，把 GBK 文件先转 UTF-8 临时副本再解析），连线逐条核对后才能交付

### 交付话术模板

给用户：`.topo` 文件 + 这三句话：
> 1. 把文件拖进 eNSP 窗口（或双击打开），核对设备渲染和连线
> 2. 有中文设备名若显示乱码立刻告诉我（不应发生，但需你确认）
> 3. 核对无误后启动设备，然后对我说"拓扑已启动，开始配置"（要走功能 B 需先完成 MCP 安装，见仓库 `功能2-配置下发/使用说明.md`）

更完整的模板与示例：`功能1-生成拓扑/`（模板脚本.py / 示例-双出口.py / 一键体验.py），需要细节时读 `功能1-生成拓扑/使用说明.md`。

---

## 功能 B：配置下发（设备已启动后）

前提检查：
1. MCP 已注册且未被禁用（WorkBuddy 读 `%USERPROFILE%\.workbuddy\mcp.json`，服务名 `grbj-ensp`；没装就引导用户跑 `install.bat` 或看 `功能2-配置下发/使用说明.md`）
2. **用户已在 eNSP 启动设备**——这是唯一必须人工做的步骤，设备没启动你连不上

你的执行链：`ensp_load_topology`（解析 .topo 并扫描 Telnet 127.0.0.1:2000+ 建连）→ 逐台探查（型号/版本/已有配置）→ `ensp_send_commands` 或 `ensp_apply_experiment`（下发→校验→保存→报告）→ 出错走 `ensp_diagnose_error`（15 类 VRP 根因知识库），不要瞎猜报错原因。

注意：接口名写全称（`GigabitEthernet0/0/1`、`LoopBack0`）；Telnet 5 分钟空闲超时，长时间停顿后重新建连；设备多时建议用户分批启动。

---

## 功能 C：自检与排错

跑 `scripts/check_health.py`（8 项环境体检）和 `tests/verify_usg6000v.py`（补丁专项，免安装）。结果解读与常见报错速查表在 `功能3-自检与排错/使用说明.md`——把表中"解决"列的动作直接替用户执行掉。

---

## 与用户沟通的规矩

- 说人话，不堆术语；先给结果文件，再给下一步动作
- 每次交付拓扑都要提醒"打开 eNSP 核对"——你是生成者，用户是验证者，**验证这一步永远不能替他省略**
- 用户问"为什么"时，引导他看对应功能文件夹的《使用说明.md》，不要现场重新发明解释
