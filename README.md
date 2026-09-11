# grbj-ensp-mcp (enhanced)

**华为 eNSP 的 MCP 服务器 · 社区增强版**——在官方 v0.2.2 基础上打了本地补丁，让 AI 能**程序化生成含 USG6000V 防火墙的 eNSP 拓扑文件**，并在设备启动后直接经 Telnet 完成配置下发、校验与诊断。

> 让 AI 直接操作你 eNSP 里的华为设备：你一句话说需求，AI 生成 `.topo` → 你在 eNSP 打开点启动 → AI 经 MCP 建连、探查、下发配置、跑校验、存档。

---

## 与上游的差异（为什么有这个 fork）

| 增强项 | 说明 |
|---|---|
| **USG6000V 拓扑生成支持** | 上游 `NativeTopoBuilder` 不含 USG6000V。本 fork 逆向 eNSP 实存拓扑样本，为其补上**枚举式**接口表（`<slot id="1">` 逐口声明：GE0/0/0 管理口 + GE1/0/0~6 七业务口），与 GUI 拖出的默认形态逐项一致，已实测 eNSP 可正常打开并启动 |
| **S3700 接口表修正** | 上游默认 24 FE 与实机不符，修正为实测形态 **22 Ethernet + 2 GE**（GE0/0/23~24） |
| **install.bat 路径修正** | 上游把 MCP 注册写到 `%APPDATA%\WorkBuddy\mcp.json`，WorkBuddy 实际读取 `%USERPROFILE%\.workbuddy\mcp.json`，已修正 |
| **一键健康自检** | `scripts/check_health.py`：8 项检查（包导入 / 补丁生效 / MCP 注册 / 端到端渲染等），零配置可跑 |
| **补丁验证脚本** | `tests/verify_usg6000v.py`：免安装直接用仓库 src，验证补丁 + GBK 落盘 + parser 回读 |
| **可分发技能包** | `skill/ensp-topo-generate/`：拓扑生成方法论 + 模板脚本 + 一键环境脚本，去个人化可移植 |

补丁本体在 `src/grbj_ensp_mcp/topo_builder.py`（约 65 行差异）。

## ⚠️ 来源与许可（请先读）

- 上游项目 **grbj-ensp-mcp v0.2.2** 由「广然笔记」发布，作者声明的官方分发渠道为 [广然笔记下载站 grbj.cn](https://www.grbj.cn)（原版说明见 [`docs/README-upstream.md`](docs/README-upstream.md)）。
- 本仓库是**社区增强 fork，不是官方分发**。原版以 **MIT License** 发布（见 [`LICENSE`](LICENSE)），本仓库在同等许可下再分发并保留原版权声明；对本仓库改动的部分，同样以 MIT 提供。
- 请优先支持原作者的官方渠道获取更新；本 fork 的补丁如被上游吸收，建议回归上游。

## 工作原理

```
你说需求 ──> NativeTopoBuilder 生成 .topo（GBK 编码，eNSP 原生格式）
             │
             ▼
     你在 eNSP 打开并点启动（GUI，只能人工）
             │
             ▼
MCP 服务（grbj-ensp，31 个工具）
  ensp_load_topology   解析拓扑，自动扫描 Telnet 127.0.0.1:2000+ 建连
  ensp_send_commands   批量下发配置
  ensp_apply_experiment  一站式：下发 → 校验 → 保存 → 报告
  ensp_diagnose_error    VRP 错误知识库诊断（15 类根因）
  ensp_cross_validate_topology  LLDP 实测 × 设计图交叉纠错
```

## 安装

**前置**：Windows 10/11、华为 eNSP、Python 3.10+（3.13 实测通过）。

### 方式一：脚本安装（WorkBuddy 用户）

```bat
install.bat
```

自动：建 venv → 装本地 wheel（含补丁）→ 注册到 `~/.workbuddy/mcp.json`（自动备份）→ 在连接器页点「信任」即启用。

### 方式二：手动安装（任意环境）

```bash
python -m venv .venv
.venv\Scripts\pip install grbj_ensp_mcp-0.2.2-py3-none-any.whl
# 关键一步：用补丁版覆盖
copy /y src\grbj_ensp_mcp\topo_builder.py .venv\Lib\site-packages\grbj_ensp_mcp\topo_builder.py
```

然后在你的 AI 工具 MCP 配置中注册（`command` 指向 venv 的 python.exe，`args` 为 `["-m", "grbj_ensp_mcp.server"]`）。

### 验证

```bash
python scripts/check_health.py      # 8 项自检
python tests/verify_usg6000v.py     # 补丁专项验证（免安装）
```

## 拓扑生成（不走 MCP，本地库直调）

MCP 的 31 个工具**不含生成能力**（只有解析/建连/下发/校验）。生成拓扑直接调库：

```python
from grbj_ensp_mcp.topo_builder import NativeTopoBuilder

b = NativeTopoBuilder()
b.add_device("FW-1", model="USG6000V", com_port=2004)
b.add_device("SW-1", model="S5700", com_port=2006)
b.add_line("FW-1", "SW-1", src_index=1, tar_index=1)
xml = b.render()
# 落盘必须：encoding="UNICODE" 声明 + CRLF + GBK 编码（UTF-8 中文必乱码）
```

完整可跑示例见 [`examples/gen_example_dual_exit.py`](examples/gen_example_dual_exit.py)（双出口 + 双 USG6000V + 双核心，11 设备 14 连线），模板见 [`skill/ensp-topo-generate/scripts/gen_topo_template.py`](skill/ensp-topo-generate/scripts/gen_topo_template.py)。

**型号支持**（builder 内置接口表）：AR1220/2220/2240、S2700/3700/5700/6700、AC6005、AP6050、USG5500、**USG6000V（本 fork 补丁）**、PC/STA/Laptop/Server。

## 两个配套 Skill

| Skill | 用途 |
|---|---|
| `skill/grbj-ensp-smart-config/` | 配置方法论：先探查再配置、报错走诊断流程、实验报告规范 |
| `skill/ensp-topo-generate/` | 拓扑生成 SOP：型号 index 映射表、GBK 落盘三要素、自检要点、常见坑 |

安装到你的 AI 工具技能目录即可（WorkBuddy：`~/.workbuddy/skills/`；Claude Code：`~/.claude/skills/`）。

## 目录结构

```
├── src/                  含补丁的源码（补丁权威源）
├── skill/                两个配套 Skill
├── scripts/check_health.py   一键自检
├── tests/                上游测试套件 + verify_usg6000v.py 补丁验证
├── examples/             拓扑生成示例（含可打开的样例 .topo）
├── docs/                 上游文档 + 日常使用手册 + 原版 README
├── install.bat / install.sh
├── grbj_ensp_mcp-0.2.2-py3-none-any.whl   原厂 wheel（不含补丁，装完必须覆盖 topo_builder.py）
└── LICENSE               MIT（上游）
```

## 已知限制

- **eNSP 是 Windows GUI 程序**：打开文件与点启动必须人工完成，MCP 只接管启动后的操作
- **Console 5 分钟空闲超时**、接口名必须写全称（`GigabitEthernet0/0/1`、`LoopBack0`）
- `.topo` 含中文设备名必须 **GBK** 编码落盘（详见使用手册「落盘三要素」）
- USG6000V 补丁按 GUI 默认形态（1 管理 + 7 业务口）生成；**不支持通过改 XML 给设备加口**

## 许可

[MIT License](LICENSE) · 上游 © 2026 grbj-ensp-mcp contributors · enhanced fork 的改动同样以 MIT 提供
