---
name: ensp-topo-generate
description: 用 grbj-ensp-mcp 的 NativeTopoBuilder 库生成 eNSP 拓扑文件（.topo），可直接在 eNSP 打开。当用户要求"生成/画 eNSP 拓扑""做个实验拓扑""输出拓扑文件""批量产出 .topo"，或需要从设备清单+连线关系程序化生成拓扑时使用。自带环境引导脚本与 USG6000V/S3700 补丁，覆盖各型号接口 index 映射、GBK 编码落盘防中文乱码、parser 回读自检、com_port 规划与型号支持边界。
agent_created: true
---

# eNSP 拓扑文件生成

## 核心认知（先破除误区）

**生成 .topo 不走 MCP 工具，也不走 Skill 调用链**——MCP 服务端（31 个工具）只做"解析已有拓扑 + 连设备 + 下发命令 + 校验 + 诊断"，**没有生成能力**（其 `server.py` 不 import topo_builder）。
生成 = **写一个本地 Python 脚本，直接调用 `grbj_ensp_mcp` 包里的 `NativeTopoBuilder` 库**，用装了该包的解释器执行。

三段职责别混：

| 阶段 | 靠什么 |
|---|---|
| 生成 .topo | 本地脚本调 `grbj_ensp_mcp.topo_builder` 库 |
| 解析/核对已有 .topo | 本地脚本调 `grbj_ensp_mcp.topo_parser` 库 |
| 设备启动后配置下发/校验 | MCP 工具（需先启用该 MCP 服务） |

## 环境准备（首次使用，三步）

```bash
python scripts/setup_env.py        # 1) 建技能目录下的 .venv + 装 wheel + 打补丁
"<技能目录>/.venv/Scripts/python.exe" scripts/selfcheck.py   # 2) 端到端自检，全 PASS 即可用
```

3) 记住 setup 打印的解释器路径，后续所有生成脚本都用它执行。

- 环境位置：`<技能目录>/.venv`（Windows 解释器 `.venv/Scripts/python.exe`，Linux/macOS `.venv/bin/python`）
- 依赖 + 补丁都在 `assets/` 与 `scripts/patched/`，**不需要另找源码**
- **补丁说明**：官方 wheel 的 `topo_builder.py` 不支持 USG6000V（且 S3700 接口表有误）；本技能内附已打好补丁的完整文件与 unified diff（`scripts/patched/`），setup 会自动覆盖安装副本。**重装或升级 wheel 后必须重跑 setup_env.py**（或手动把 `scripts/patched/topo_builder.py` 覆盖到 site-packages 的 `grbj_ensp_mcp/` 下）

## 标准工作流

1. **要需求**：设备清单（名称 + 型号）+ 连线关系（谁连谁、哪个接口）。用户没给接口时，按型号默认口顺序规划，并在交付说明里逐条列出让他确认。
2. **拷模板**：复制 `scripts/gen_topo_template.py` 到目标目录改名，如 `gen_xxx_topo.py`。
3. **填两张表**：`D`（设备表：名称/型号/com_port/cx/cy）与 `L`（连线表：源/源index/目标/目标index/说明）。说明书用接口名（GE0/0/1）写，脚本用 `ar_ge()/usg_ge1()/s5700_ge()/s3700_eth()/s3700_ge()/ac_ge()/first()` 转 index。
4. **跑**：`"<技能目录>/.venv/Scripts/python.exe" gen_xxx_topo.py [输出路径]`
   自检必须全绿：builder 校验 + parser 回读设备清单一致 + 连线逐条命中 + GBK 回读无损。
5. **交付**：把 .topo（可附一份逐条校验清单）给用户，并说明"请在 eNSP 打开核对渲染、按资源预案分批启动"。
6. **顺手**：把接口名↔index 换算规则和推定项（无实测样本的型号/接口）写进交付说明。

## 型号接口 index 映射（XML 全局序号，0-based）

| 型号 | index → 实物接口 |
|---|---|
| AR2220 | 0,1,2… = GE0/0/0, GE0/0/1…（路由器 0 起，无偏移） |
| USG6000V | 0 = GE0/0/0（MGMT，禁连线）；1~7 = GE1/0/0~GE1/0/6（**枚举式**，7 业务口） |
| S5700 | 0~23 = GE0/0/1~GE0/0/24（**偏移 +1**） |
| S3700 | 0~21 = Ethernet0/0/1~0/0/22；22~23 = GE0/0/1~2 |
| AC6005 | 0~7 = GE0/0/1~8（**按交换机惯例推定，无实测样本**） |
| PC / Server / STA / AP | 单口，index = 0 |

脚本里已封装成 helper，直接传接口号：`s5700_ge(1)` → index 0。**别手算 index**。

## 落盘格式三要素（写错必出问题）

eNSP 是老 Win32 程序，`.topo` 实际是**单字节 ANSI/GBK**：

```python
xml_ensp = re.sub(r"^<\?xml[^?]*\?>", '<?xml version="1.0" encoding="UNICODE" ?>', xml)
xml_ensp = xml_ensp.replace("\n", "\r\n")
with open(OUT, "wb") as f:
    f.write(xml_ensp.encode("gbk"))
```

1. 声明恒为 `encoding="UNICODE"`（假声明，硬编码，照抄）
2. 换行 **CRLF**
3. 实际编码 **GBK**，**无 BOM**
→ UTF-8 写盘必致中文设备名乱码。

## 自检的两处讲究

- `parse_topo` **只认 UTF-8**：GBK 文件直接喂会 `errors=replace` 读坏中文。自检要用**内存中的 Unicode 串写临时 UTF-8 副本**再解析。
- 连线核对用**无向匹配**（`frozenset({src, dst})` + index 双向容错），因为 XML 里 `src_index/tar_index` 不保证与调用顺序一致。

## com_port 规划约定

- 网络设备从 **2000** 起递增分配，写进 `D` 表统一规划（如 ISP1=2000、AR=2002、USG=2004、Core=2006、接入=2008+、AC=2013）
- **无 CLI 设备（PC / Server / STA / AP）一律填 0**，不占端口
- 该表可作为设备启动后 MCP `ensp_scan_devices` 扫描结果的旁证（端口对上说明接线正确）

## 支持边界与红线

- 生成器支持的型号：AR2220、S5700、S3700、USG6000V（补丁）、AC6005、AP6050、PC、Server、STA。其他型号走默认接口表，需人工确认。
- **红线**：只生成"与 eNSP GUI 拖出来的默认形态一致"的设备；**不得为了加口而手工改拓扑 XML**（如给 USG6000V 加业务口）——未经华为官方支持，原项目既有裁决明确不作预案。
- 无实测样本的写法（`Server` 型号、AP6050、AC6005 双上联）属**推定项**，交付时必须标注"打开后重点核对"。

## 常见坑

| 坑 | 应对 |
|---|---|
| Git Bash 给 Windows python 传 `/c/...` 路径 → MSYS 转换建错位置 | 路径用 `C:/...` 风格 |
| 中文乱码 | 见"落盘格式三要素"，GBK + CRLF |
| 重装 wheel 后 USG6000V 报错/漏设备 | 补丁被覆盖，重跑 `scripts/setup_env.py` |
| parser 还原 USG 接口名缺 GE 前缀（显示 `0/0/x`） | 已知瑕疵，不影响建连/连线解析；要准确接口名时按 index 映射表自行还原 |
| 在 Git Bash 里 `grep` 搜中文设备名搜不到 | 正常——文件是 GBK 而 grep 按 UTF-8 匹配；用 Python 按 GBK 解码验证 |
| 终端侧端口写成了交换机侧端口号 | 单口设备 index 恒为 0（`first()`） |

## 附带文件

- `scripts/gen_topo_template.py` —— 生成脚本模板（改两张表即可用）
- `scripts/setup_env.py` / `scripts/selfcheck.py` —— 环境引导与端到端自检
- `scripts/patched/` —— 补丁完整文件 + unified diff（可审计）
- `README-分发说明.md` —— 分发给他人时的前置条件与故障排查

## 许可与来源

- 底层包 `grbj-ensp-mcp v0.2.2`：MIT License（Copyright (c) 2026 grbj-ensp-mcp contributors），原分发件见 `assets/`，分发请保留其 LICENSE。
- 本技能与其补丁为使用者本地构建；补丁内容为 USG6000V 枚举式接口渲染 + S3700 实测接口表，来源为真实 eNSP 保存样本的逆向。
