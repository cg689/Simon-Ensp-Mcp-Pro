---
name: grbj-ensp-smart-config
description: eNSP 网络设备智能配置下发、故障诊断与 Word 实验报告生成。当用户要求配置 eNSP 设备、排查配置下发失败原因、对网络设备进行健康检查、或在实验完成后输出实验报告时触发。本 Skill 覆盖错误根因分析、自动修复建议、逐跳故障排查决策树，并按统一品牌色规范生成可打印的 .docx 实验报告。
---

# eNSP 智能配置与故障诊断（LLM 驱动版）

本 Skill 的核心目标：让 LLM 像一位资深网络工程师一样思考和操作 eNSP 仿真设备，而非机械地执行脚本。

## 关键环境认知

**你操作的所有设备都运行在 eNSP（Enterprise Network Simulation Platform）仿真平台中，不是真实物理硬件。**

这意味着：
- 设备通过 Telnet（127.0.0.1:端口号）连接，非真实 console 线
- 设备型号是软件模拟的（AR2220、S5700 等），行为与真实设备基本一致
- 存在一些 eNSP 仿真环境特有的约束（如接口名缩写不识别、console 超时等）
- 修复命令必须符合 eNSP 仿真环境的约束，不能照搬真实设备的经验

## 核心思维模型

### 你是网络工程师，不是脚本执行器

每次与 eNSP 设备交互时，请遵循以下思维模式：

1. **先理解，后行动**：在发送任何命令前，先搞清楚设备当前状态
2. **假设驱动**：对失败原因形成假设，然后收集证据验证
3. **上下文推理**：不只看错误信息本身，要结合设备型号、已有配置、当前视图综合判断
4. **渐进式修复**：从最可能的修复开始，每次只改一个变量，快速验证

### 三层真相体系（v0.2.1 引入）

MCP 同时提供三层视角来理解 eNSP 实验环境。LLM 必须学会在三层之间切换：

| 层级 | 来源 | 工具 | 何时使用 |
|------|------|------|---------|
| **设计图** | `.topo` 文件（学生/老师规划） | `ensp_parse_topology` | 拿到「按规划应该有哪些设备、连哪些线」 |
| **物理事实** | 设备间 LLDP 通告 | `ensp_verify_lldp_neighbors` | 拿到「线缆真的接到了哪些接口」 |
| **配置真相** | 设备运行配置 | `ensp_get_running_config` | 拿到「设备现在实际跑了哪些配置」 |

**关键原则：物理事实优先。** 当设计图与物理事实冲突（接口号对不上）时：

- 下发配置前必须以物理事实为准（用 `ensp_verify_lldp_neighbors` 确认）
- 不要假设 `.topo` 是对的——学生可能画错了、可能改接线后忘了另存拓扑
- 用 `ensp_cross_validate_topology` 自动做双向比对，得到完整差异报告

### 规则引擎 vs LLM 推理

MCP 提供了双层诊断能力：

| 层级 | 方式 | 何时使用 |
|------|------|---------|
| 规则引擎 | `ensp_diagnose_error` / `diagnosis` 字段 | 快速匹配已知错误模式（confidence=high 时可靠） |
| LLM 推理 | `ensp_gather_diagnostic_context` / `llm_context` | 规则置信度低、未知错误、需要结合上下文深度分析 |

**关键原则**：规则引擎提供线索，LLM 做出最终判断。不要盲信规则引擎的预判（尤其 confidence=medium/low 时），必须结合设备上下文做独立推理。

## 工作流程

### 流程 A：LLM 驱动的智能配置下发（推荐）

```
0. 接口级任务的预检查（必须）
   ensp_verify_interfaces(session) → 获取当前所有接口的 PHY / Protocol 状态
   对照 .topo 解析结果中的连线表，确认要配置的接口确实有对端设备
   → S5700 的 .topo `srcIndex` 是 0-based，设备真实接口号是 `srcIndex + 1`
     例如 XML `srcIndex="0"` 对应 `GigabitEthernet0/0/1`
   → 如果目标接口 physical=down，先用 LLDP 确认真实接线，禁止仅凭 .topo 标号下发配置
   例：单臂路由中 SW1 的 trunk 口要先看 GE0/0/24 是否 up（up=连 AR1）

1. 探查设备状态（必须）
   ensp_get_device_info → 确认 VRP 版本 / 型号
   ensp_get_running_config → 查看已有配置

2. 基于探查结果生成命令
   → 接口名用全称（GigabitEthernet0/0/1 而非 GE0/0/1）
   → 关键字用正确大小写（LoopBack0 而非 Loopback0）
   → 确认当前视图
   → 复用步骤 0 拿到的"接口状态表"，把 down 的接口排除在配置目标之外
   → 只向 physical=up 的目标接口下发配置

3. 智能配置下发 + LLM 上下文收集
   ensp_smart_config(commands, stop_on_error=True, auto_diagnose=True, auto_gather_llm_context=True)
   → 失败时返回:
     - first_error_diagnosis（规则引擎预判，含 confidence）
     - llm_context.reasoning_prompt（供 LLM 深度推理的完整上下文）

4. 如果 has_error=True:
   a) 先查看 first_error_diagnosis.confidence
      - confidence=high → 规则引擎判断可靠，直接按 fix_suggestions 修正
      - confidence=medium/low → 规则引擎不确定，必须进行 LLM 推理

   b) LLM 推理步骤（置信度低或未知错误时）:
      阅读 llm_context.reasoning_prompt 中的完整上下文
      → 分析设备型号、VRP 版本、已有配置
      → 判断错误类型（命令不存在？视图错误？参数错误？）
      → 结合 eNSP 设备约束推理根因
      → 生成修复命令
      → 重新调用 ensp_smart_config

5. 验证
   ensp_verify_interfaces → 再次确认目标接口 PHY/Protocol 已正确 up
   ensp_ping 或 display mac-address → 确认配置效果（VLAN 学习到 MAC、ARP 解析成功）

6. 实验报告输出（流程 A 的最终交付物）
   → 详见「实验报告生成」专章
   → 严格按七章结构组织内容，按视觉规范排版，最终输出 .docx（不输出 .md）
   → 报告封面、章节标题、表格、代码块、TIP 块、页面边距、页眉页码均按规范执行
   → present_files 把 .docx 路径给用户直接打开预览
```

### 流程 B：LLM 驱动的深度故障排查

```
1. 收集诊断上下文
   ensp_gather_diagnostic_context(session_id_or_name, failed_command, error_text, experiment_description)
   → 自动执行 display version / display current-configuration / display ip interface brief
   → 返回 llm_context.reasoning_prompt（结构化推理 prompt）

2. LLM 阅读 reasoning_prompt 并分析
   → 推理 prompt 包含：
     - 设备信息（型号、VRP 版本、当前视图）
     - 失败命令与错误回显
     - 相关配置片段
     - 接口状态
     - 最近命令输出
     - 规则引擎预判
     - 四步推理框架（错误类型判断 → 根因分析 → 修复方案 → 验证步骤）

3. 生成修复方案
   → 修复命令必须符合 eNSP 设备约束
   → 给出验证步骤

4. 执行修复并验证
   ensp_smart_config 重新下发 → ensp_health_check 验证
```

### 流程 C：全面健康检查

```
1. 执行健康检查
   ensp_health_check(check_ospf=True, check_routes=True, expected_up_interfaces=[...])

2. 阅读返回的 markdown_report
   → 按 severity 从高到低处理问题
   → critical > error > warning > info

3. 对每个 issue 进行 LLM 推理
   → 不要只看 suggestion 字段，结合设备上下文做独立判断
   → 生成修复命令

4. 修复后重新检查
   ensp_health_check → 确认 issues 已清零
```

### 流程 D：LLDP × 拓扑交叉验证（纠错与配置前接口核对）

```
1. 每台设备上各采一次 LLDP 邻居（自动开启 LLDP）
   for 设备 in 拓扑里的设备:
       neighbors = ensp_verify_lldp_neighbors(session_id_or_name=设备)
       → 默认 auto_enable=True：工具内部先发 display lldp neighbor，
         若检测到 LLDP 未使能，自动发 system-view → lldp enable → quit，
         等 2 秒让邻居收敛，再重采一次
       → 返回字段 lldp_was_enabled / auto_enable_attempts 告诉调用方
         本次是否自动使能过 LLDP（避免 LLM 重复操作）
       → 若用户已经手工开过 LLDP，可传 auto_enable=False 跳过自动使能

2. 与 .topo 文件做交叉验证
   ensp_cross_validate_topology(
       topo_path="xxx.topo",
       lldp_results=[neighbors1, neighbors2, ...]
   )
   → 返回 4 类结果:
     - matched：拓扑与物理一致（放心用拓扑里的接口号）
     - topo_only：拓扑说有但物理没有（线没接 / 对端未启）
     - lldp_only：物理上有但拓扑没记（拓扑图过时）
     - mismatched：接口号对不上（拓扑图错位）

3. 按差异类型处理:
   - mismatched：以下发到 LLDP 返回的真实接口号为准，重新生成配置
   - topo_only：检查线缆、对端设备状态、LLDP 是否使能
   - lldp_only：在 eNSP GUI 里检查是否漏画了线，或设置 device_aliases

4. 验证后再生成配置（纠错后的接口号才是真相）
```

**为什么必须有这个流程**：`.topo` 文件是设计意图，eNSP GUI 拖线时容易把接口画错位（例如 AR1 的 GE0/0/0 拖到了 AR2 的 GE0/0/1），保存拓扑后再用 LLDP 一对照就发现对不上。如果不验证就发配置命令，很可能把 IP 配到错误接口，导致链路不通但又找不到原因。

**关于自动使能**：eNSP 部分镜像（AR2240 / S5700）默认 LLDP 是开的，但 AR201 / 旧版 S5700 可能需要手动开。`ensp_verify_lldp_neighbors` 默认会自动探测并开启 LLDP，省掉用户手工调 `ensp_enable_lldp` 的步骤。返回结构里的 `lldp_was_enabled` 字段告诉 LLM「这一轮工具替你开了 LLDP」，避免 LLM 重复触发 enable。

**典型工作场景**：

- 实验开始前：对实验拓扑做一次 `ensp_cross_validate_topology`，确保接线和设计一致
- 排查「明明配置了 IP 但 ping 不通」时：先用 LLDP 核对线是否真的接在了你以为是的那一端
- 学生保存错位拓扑后：用 mismatched 字段直接告诉用户哪里错了
- LLDP 没开启时：不用手动调 enable，直接采就行，工具会自动处理

## LLM 推理框架

当遇到配置失败时，请按以下四步框架进行推理：

### 第一步：错误类型判断

分析错误回显，判断属于哪一类：

- **命令层错误**：`Error: Unrecognized / Incomplete / Wrong parameter / Ambiguous`
  → 命令本身有问题（拼写、参数、视图）
- **连接层错误**：`Connection refused / Connection closed / Timeout`
  → 设备可达性问题
- **配置逻辑错误**：命令语法正确但逻辑冲突（IP 冲突、VLAN 不存在、OSPF 未使能）
- **校验异常**：命令执行成功但效果不符合预期（ping 不通、邻居 down）

### 第二步：根因分析

结合以下信息做综合判断：

1. **设备上下文**：型号、VRP 版本、当前视图
2. **已有配置**：是否已有冲突配置？前置条件是否满足？
3. **eNSP 设备约束**（必须牢记）：
   - VRP 不认缩写接口名（`GE0/0/0` → 必须 `GigabitEthernet0/0/0`）
   - S5700 接口从 1 开始编号（没有 GE0/0/0，从 GE0/0/1 开始）
   - LoopBack0 是大写 B（不是 Loopback0）
   - console 5 分钟无操作自动登出
   - 孤立接口 protocol 永远 down
   - system-view 不能重复进入
4. **接口状态**：是否 physical/protocol up？IP 是否正确？

### 第三步：修复方案

生成修复命令，优先级从高到低：

1. **最可能修复**：基于根因分析的最直接修复
2. **备选方案**：如果第一种不行，尝试什么？
3. **前置条件**：修复前需要满足什么条件？

### 第四步：验证步骤

修复后如何验证：

1. 重新下发修正后的命令
2. 执行验证命令（display / ping / tracert）
3. 如果仍然失败，收集新的 LLM 上下文重新推理

## eNSP 仿真环境约束（LLM 推理时必须检查）

以下约束是 eNSP 仿真平台特有的，与真实物理设备可能不同。每次生成命令或分析错误时，必须逐项检查：

1. **接口名必须全称**：VRP 不认缩写（`GE0/0/0` → 必须 `GigabitEthernet0/0/0`），这是 eNSP 最常见的配置错误
2. **大小写敏感**：`LoopBack0` 而非 `Loopback0`（B 大写）
3. **接口编号规则**：S5700 系列从 1 开始（`GE0/0/1` 起），AR 系列从 0 开始（`GE0/0/0` 起）
4. **console 超时**：5 分钟无操作自动登出，需 `disconnect → connect` 重建 Telnet 会话
5. **孤立接口**：单接口无对端连线时 protocol 永远 down，OSPF 不通告该网段
6. **system-view 不可重复**：已在系统视图时再执行 `system-view` 会报 Unrecognized
7. **Telnet 连接**：设备通过 `127.0.0.1:端口号` 访问，非真实 console 线
8. **.topo 接口索引偏移**：在 .topo XML 中 `<slot count="N">` 生成的接口数组下标是 0~N-1，但 eNSP 中 S5700 实际接口名是 GE0/0/1~GE0/0/N（存在 +1 偏移）。比如 `count=24` 时，`srcIndex=23` 实际对应 `GE0/0/24`（不是 GE0/0/23）。NativeTopoBuilder 生成 XML 与 `topo_parser` 解析结果必须保持这一映射。
9. **配置前核对原则**：S5700 连接 PC 时，`.topo` 的 `srcIndex=0/1` 才分别对应设备真实 `GE0/0/1/2`；若拓扑解析结果出现 `GE0/0/2/3`，必须先检查 XML 索引，不应直接把端口配置到错误接口。
10. **接口级任务的"配置前先看 up"原则**：在下发 `interface X` / `port link-type` / `ip address` 等接口级配置前，必须先用 `display interface brief` 确认目标接口 PHY 状态。如果目标接口 physical=down，说明这个接口在 .topo 中没有被连线索引命中（+1 偏移、对端未开机、或连线写错），此时配置命令虽然能"成功执行"但不会有任何效果。**正确的做法是：找到 up 的接口，按"实际生效的接口名"配置**。

## 典型场景推理示例

### 场景：命令报 Unrecognized command

```
错误回显：Error: Unrecognized command found at '^' position.
失败命令：interface GE0/0/0

LLM 推理过程：
1. 错误类型：命令层错误，Unrecognized command
2. 根因分析：
   - 检查 eNSP 约束：VRP 不认缩写接口名
   - 命令中使用了 "GE0/0/0" 而非 "GigabitEthernet0/0/0"
   - 这是最常见的 eNSP 配置错误
3. 修复方案：改为全称 "interface GigabitEthernet0/0/0"
4. 验证：重新下发后检查 display ip interface brief
```

### 场景：接口配置"看起来成功"但链路不通

```
现象：在 GE0/0/1 上配了 access vlan 10，但 PC1 仍 ping 不通网关
      display interface brief 显示 GE0/0/1 PHY = down，GE0/0/2 PHY = up

LLM 推理过程：
1. 错误类型：校验异常，配置未生效
2. 根因分析：
   - 目标接口 physical=down，说明这个接口没有对端设备
   - .topo 中 PC1 的 srcIndex=0、SW1 端的 tarIndex 决定了 PC1 实际连接 SW1 的哪个接口
   - 如果 .topo 中 SW1 端 tarIndex=1，但 S5700 接口数组 +1 偏移后实际生效的是 GE0/0/2，那么 PC1 实际连 GE0/0/2，不是 GE0/0/1
   - 所以正确的 VLAN 10 配置应该下发到 GE0/0/2
3. 修复方案：
   - 先 display interface brief 列出所有接口的 PHY/Protocol 状态
   - 找到连 PC1 那个真正 up 的接口（这里是 GE0/0/2）
   - 在该接口上重新配置 port link-type access / port default vlan 10
   - 同时撤销错配的 GE0/0/1 上的 access vlan 10（undo port default vlan / undo port link-type）
4. 验证：display port vlan 应显示 GE0/0/2 access PVID 10；AR1 ping PC1 100% 通
```

### 场景：ping 不通

```
错误回显：100% packet loss
目标：ping 10.0.0.2 from AR1

LLM 推理过程：
1. 错误类型：校验异常，连通性失败
2. 根因分析（逐跳排查）：
   - AR1 是否有到 10.0.0.0/24 的路由？
   - AR1 的出口接口是否 protocol up？
   - 中间设备是否配置了路由/ACL？
   - 目标设备接口是否配置了 IP？
3. 收集证据：
   - display ip routing-table（检查路由）
   - display ip interface brief（检查接口状态）
   - tracert 10.0.0.2（定位故障跳）
4. 根据证据生成修复方案
```

### 场景：未知错误（规则引擎 confidence=low）

```
错误回显：Some unexpected error
规则引擎：category=unknown, confidence=low

LLM 推理过程：
1. 规则引擎不可靠，启用 LLM 深度推理
2. 调用 ensp_gather_diagnostic_context 收集完整上下文
3. 阅读 reasoning_prompt 中的设备信息、配置、接口状态
4. 结合 eNSP 约束和网络知识做独立判断
5. 生成修复方案
```

## 工具使用指南

### 配置下发（首选）

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `ensp_smart_config` | 智能配置下发 | `auto_diagnose=True`, `auto_gather_llm_context=True` |
| `ensp_send_commands` | 批量命令下发 | `auto_diagnose=True` |

### LLM 推理诊断

| 工具 | 用途 | 何时使用 |
|------|------|---------|
| `ensp_gather_diagnostic_context` | 收集 LLM 推理上下文 | 未知错误、规则置信度低、需要深度推理 |
| `ensp_diagnose_error` | 规则引擎快速诊断 | 错误类型明确、需要快速判断 |
| `ensp_health_check` | 全面健康检查 | 排查故障、验证配置效果 |

### 探查与验证

| 工具 | 用途 |
|------|------|
| `ensp_get_device_info` | 获取设备型号 / VRP 版本 |
| `ensp_get_running_config` | 获取当前运行配置 |
| `ensp_verify_interfaces` | 检查接口状态 |
| `ensp_verify_ospf` | 检查 OSPF 邻居 |
| `ensp_verify_routes` | 检查路由表 |
| `ensp_ping` | 连通性测试 |

### 拓扑纠错（LLDP × .topo 交叉验证）

| 工具 | 用途 | 何时使用 |
|------|------|---------|
| `ensp_enable_lldp` | 在设备上手工开启 LLDP（`lldp enable` 全局 + 可选接口） | 想跳过自动使能、直接手工开 |
| `ensp_verify_lldp_neighbors` | 采集设备的 LLDP 邻居（物理事实）。默认会自动使能 LLDP，无需先调 enable | 想知道「这根线真的接到了哪个接口」 |
| `ensp_cross_validate_topology` | 与 `.topo` 做双向比对 | 实验前后、配置前、怀疑拓扑图错位时 |

## 注意事项

- `ensp_smart_config` 是推荐的配置下发入口，设置 `auto_gather_llm_context=True` 可在失败时自动收集 LLM 推理上下文
- 规则引擎的 `confidence` 是关键信号：high 时可信任，medium/low 时必须启用 LLM 推理
- `ensp_gather_diagnostic_context` 会执行多条 display 命令，耗时数秒，但提供的上下文对 LLM 推理至关重要
- 所有生成的修复命令必须符合 eNSP 设备约束
- 设备 console 5 分钟无操作超时后，需要先 disconnect 再 connect
- 修复后必须验证，不要假设修复成功
- **配置命令的接口名必须与物理事实一致**：优先用 `ensp_verify_lldp_neighbors` 返回的接口号，不要盲信 `.topo` 文件里的标号——`ensp_cross_validate_topology` 给出 mismatched 时，说明拓扑图与物理接线对不上，下发配置前必须改用 LLDP 的接口号
- **接口名归一化**：MCP 输出的 LLDP 接口名是 `GigabitEthernet0/0/0` 全称形式，与 `display ip interface brief` 一致；下发命令时直接用此全称即可

## 实验报告生成（流程 A 的最终交付物）

eNSP 实验配置完成后，必须输出 Word（.docx）格式的实验报告。Markdown 仅作中间产物，不直接交付。报告的章节结构、视觉规范、输出流程均按本章规定执行。

### 为什么必须是 .docx

教师群体对 .docx 熟悉，可直接打印分发、插入学校归档系统；Markdown 在教师群体里接受度低。已在 `~/.workbuddy/MEMORY.md`「文件格式偏好」中固化此规则。报告必须以 .docx 落地，所有视觉规范都在 docx 中精确呈现。

### 报告章节结构

报告分七章，按以下顺序生成；无内容的章节不写占位，直接跳过（如实验顺利完成且无故障排查，则不写「六、故障排查记录」）。

#### 一、实验概述
- **实验名称**：与本次实验内容相符的简短名称（如「双核心园区网综合实验」「单臂路由与 DHCP 中继实验」）
- **实验目标**：2~4 条，从用户最初的实验意图提炼（如「验证 VRRP 主备切换」「验证 ACL 阻断效果」）
- **实验环境**：一段文字 + 列表，包含
  - eNSP 版本号
  - 设备型号清单（AR2220 / S5700 / S3700 / AC6605 等）
  - 拓扑规模（设备总数、PC 总数）

#### 二、网络拓扑
- **拓扑说明**：3~5 句文字描述拓扑结构（核心层 / 接入层 / 出口层 / 终端分别有什么设备、怎么连接）
- **设备清单表**（必含）：

  | 设备名 | 型号 | 接口数 | 角色 |
  |--------|------|--------|------|

- **物理连接表**（必含）：

  | 本端设备 | 本端接口 | 对端设备 | 对端接口 |
  |----------|----------|----------|----------|

#### 三、地址与协议规划
- **IP 地址规划表**（必含）：

  | 设备名 | 接口 | IP 地址 | 子网掩码 | 用途 |
  |--------|------|----------|----------|------|

- **VLAN 规划表**（如适用，无 VLAN 实验则跳过整张表）：

  | VLAN ID | 名称 | 网段 | 网关 | 包含端口 |
  |---------|------|------|------|----------|

- **路由协议规划表**（如适用）：

  | 设备 | 协议 | 进程 / 区域 | 通告网段 | Router ID |
  |------|------|-------------|----------|-----------|

#### 四、配置实施
- 按设备分小节，每个设备一个二级标题（如「4.1 设备 AR1 配置」）
- 设备下用三级标题分配置段（接口配置 / 路由配置 / DHCP 配置 / ACL 配置 / VRRP 配置 等）
- 每段下放完整配置命令的代码块；代码块以 `system-view` / `interface ...` / `ospf ...` 等关键命令起始，保持下发时的原貌
- **不**带行号（行号在 docx 中会破坏复制粘贴体验）

#### 五、验证测试
- **接口状态验证**：display interface brief 关键接口的 PHY / Protocol 状态（用表格或代码块呈现）
- **连通性测试**：ping 矩阵表（行 = 源设备，列 = 目标 IP 或目标设备，单元格 = 通 / 不通 / 丢包率）
- **协议邻居验证**（如适用）：display ospf peer brief / display vrrp brief / display bgp peer 等关键回显
- **业务功能验证**：与实验目标对应的功能验证（如 VRRP 切换、ACL 阻断效果、DHCP 地址分配）

#### 六、故障排查记录（如有）
仅在本次实验中实际遇到并解决问题时存在；无故障则整章跳过。结构：

| 字段 | 内容 |
|------|------|
| 问题描述 | 现象 + 错误回显 |
| 根因分析 | 结合 LLM 推理框架四步法（错误类型判断 → 根因分析 → 修复方案 → 验证步骤） |
| 修复方案 | 修复命令 |
| 修复验证 | 验证命令 + 期望结果 |

每条故障用 TIP 块呈现（浅绿底 + 深绿左条），便于读者快速定位。

#### 七、实验总结
- **实验收获**（2~3 段）：本次实验掌握的技能、验证的目标、典型约束（eNSP 接口名全称、+1 偏移等）
- **待改进点**（1~2 段）：可以优化的地方、未覆盖的边界

### 视觉规范（docx 输出标准）

报告所有视觉元素遵循以下标准。docx 不直接支持 CSS，但通过 HTML→docx 转换时内联样式可精确落地。

#### 配色变量（统一品牌色）

| 用途 | 色值 |
|------|------|
| 主色（强调元素、章节下划线） | `#41BC87` |
| 深绿（次级强调、表头、强调字） | `#2EA370` |
| 标题文字 | `#2C3E50` |
| 正文文字 | `#3a3a3a` |
| TIP / 引用块底色 | `#F5FBF8` |
| 表格描边 | `#e1f0ea` |
| 代码块底色 | `#2b2b2b` |
| 代码块字色 | `#f5f5f5` |

#### 排版规格

| 元素 | 字体 | 字号 | 字重 | 颜色 | 间距 / 其他 |
|------|------|------|------|------|-------------|
| 封面主标题 | 微软雅黑 | 28px | 加粗 | #2C3E50 | 段前 0 / 段后 24 / 居中 |
| 封面副标题 | 微软雅黑 | 16px | 常规 | #2EA370 | 段后 48 / 居中 |
| 封面元信息（实验人 / 日期） | 宋体 | 12px | 常规 | #3a3a3a | 段后 12 / 居中 |
| 顶级章节（一、二、…） | 微软雅黑 | 22px | 加粗 | #2C3E50 | 段前 24 / 段后 12 / 底边框 2px #41BC87 |
| 二级标题（4.1 设备 AR1 配置） | 微软雅黑 | 16px | 加粗 | #2C3E50 | 段前 18 / 段后 10 |
| 三级标题（接口配置） | 微软雅黑 | 14px | 加粗 | #2EA370 | 段前 12 / 段后 8 |
| 正文 | 宋体 | 12px | 常规 | #3a3a3a | 行距 1.5 / 首行缩进 2 字符 |
| 强调词 | 宋体 | 12px | 加粗 | #2EA370 | 用于关键术语、命令名 |
| 代码块 | Consolas | 10.5px | 常规 | #f5f5f5 | 深色底 #2b2b2b / 圆角 4px / 内外边距 4px 8px / 段前 6 / 段后 12 |
| 表格表头 | 微软雅黑 | 11px | 加粗 | #ffffff | #2EA370 底 / 居中 / 内边距 6px 10px |
| 表格内容 | 宋体 | 11px | 常规 | #3a3a3a | 斑马纹（奇行 #F5FBF8 / 偶行 #FFFFFF）/ 1px #e1f0ea 边框 / 内边距 6px 10px |
| TIP / 重点块 | 宋体 | 12px | 常规 | #3a3a3a | #F5FBF8 底 / 4px #2EA370 左条 / 圆角 6px / 内边距 12px 16px / 段前 12 / 段后 12 |

#### 页面规格

- 纸张：A4（21cm × 29.7cm）
- 上下边距：2.54cm
- 左右边距：3.18cm
- 页码：底部居中、阿拉伯数字、起始页 = 1
- 页眉：「实验报告：<实验名称>」、右对齐、字号 10px、颜色 #999999
- 页脚：留空

### 报告输出流程

按用户「先 md 后 HTML」偏好，报告生成严格按以下顺序：

1. **收集证据**（从本次实验的 LLM 推理过程中提取）
   - 设备清单：ensp_get_device_info 输出（型号、VRP 版本）
   - 配置命令：ensp_smart_config 实际下发的所有命令（按设备分组）
   - 验证命令及回显：display / ping / show 命令的输出
   - 故障记录：仅当有 first_error_diagnosis 命中且修复成功时记录
   - 关键配置回显：display current-configuration 关键片段、display ip interface brief 关键行

2. **撰写 Markdown 报告**
   - 按上述七章结构组织内容
   - 代码块用三个反引号包裹，标记语言 `text` 或 `bash`
   - 表格用 markdown 表格语法
   - TIP 块用 `> ` 引用（最终转 HTML 时再加视觉样式）
   - **不要在 markdown 阶段就写内联样式**——噪音太多，不便审阅
   - 设备命名全文统一（如 SW1 不能一会写 SW1 一会写 Switch1）

3. **转内联样式 HTML**
   - 按上表视觉规范，把 markdown 中的代码块、表格、引用块转换为带内联样式的 HTML 元素
   - 顶级章节用 `▍ 0X 标题` 字符级前缀（字符级前缀可保证 HTML→docx 转换时圆点样式不被渲染器吃掉）
   - 二级标题用 `■` 字符级前缀
   - 三级标题用 `▪` 字符级前缀
   - 表格的 `border`、`background`、`padding` 等样式全部内联到 `td` / `th` / `tr`
   - 代码块的 `background`、`color`、`border-radius` 全部内联到 `<pre>` 或容器 `div`

4. **HTML → .docx 转换**
   - 用 `html-to-docx` skill 或 `brief-compose` skill
   - 转换前确认所有样式已内联（避免 docx 丢失样式）
   - 转换后核对页码、表格、代码块、TIP 块是否正确显示

5. **present_files 预览**
   - 把 .docx 文件路径传给 present_files，让用户直接打开预览
   - 报告落地路径建议：`D:/01_Workspace/claw/gr/output/<实验名称>-实验报告.docx`（便于归档）
   - 报告命名规则：`<实验名称>-实验报告.docx`，如 `双核心园区网综合实验-实验报告.docx`

### 报告生成的反模式（禁忌）

下列写法会让报告显得不专业，**必须避免**：

1. **章节空占位**：无故障排查时不要写「六、故障排查（本实验无）」——直接跳过整章
2. **过度修辞**：避免「本次实验收获颇丰」「通过本实验深刻理解了 XX」等空话；用具体数据说话（如「成功验证了 8 条 ping 连通性、3 个 OSPF 邻居正常建立」）
3. **表格滥用**：能用一行文字说清的不用表格；但 IP 地址表、设备清单表、物理连接表等结构化数据**必须**用表格
4. **命令混杂**：display 回显要按设备分组，不要所有设备堆在一个代码块里
5. **缺关键回显**：配置完成后必须有 display current-configuration 关键片段 + display ip interface brief 的回显；不能只写「配置成功」四字
6. **无故障时硬凑**：实验顺利完成就不要硬编故障案例；没有「六、故障排查记录」章节是正常的
7. **使用 emoji**：报告正文中不出现任何 emoji
8. **首行缩进混用**：表格、代码块、标题、图片说明不需要首行缩进，仅正文段落需要
9. **代码块带行号**：配置命令代码块**不**带行号（行号在 docx 中会破坏复制粘贴体验，也会让代码块显得拥挤）
10. **设备命名不一致**：报告全文统一设备名（如 SW1 不能一会写 SW1 一会写 Switch1 或 交换机 1）
11. **章节编号跳号**：即使跳过了第六章，后续章节仍按「七、实验总结」连续编号，不要因为跳章而把「七」改成「六」
12. **代码块与配置不一致**：报告中的代码块必须与实际下发的命令完全一致，不允许简化或省略关键命令
13. **TIP 块滥用**：仅「实验环境」「关键提示」「易错点」「eNSP 约束」等需要重点提示的内容用 TIP 块；普通说明用正文段落