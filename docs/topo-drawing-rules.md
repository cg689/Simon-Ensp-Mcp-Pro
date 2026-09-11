# eNSP .topo 绘制规则手册

> 适用范围：本项目 `grbj-ensp-mcp`。基于 `C:\Program Files\Huawei\eNSP\examples` 下 40 个原生
> 示例拓扑（RIP / OSPF / BGP / 组播 / PIM-SM / VLAN / STP / WLAN / 安全 / 校园网 / HCNA 实验），
> 提取共性规则；同时保留本项目简化变体的写法。

---

## 1. 文件本质

| 项 | 原生 eNSP | 本项目约定 |
|---|---|---|
| 编码 | UTF-16（声明 `encoding="UNICODE"`，实际合法值） | UTF-8 |
| 根元素 | `<topo version="1.x.x.xxx">` | `<topology name="..." version="1.0">` |
| 设备 ID | 真 UUID（如 `BD156A08-9D0D-4881-A55C-3A68537852CF`） | 简写 `D1`/`D2`/`D3`（机器可读即可） |
| 注释 | 可选 `<!--  -->` | 可选 `<!--  -->`（鼓励） |

本项目的 `topo_parser.py` 已兼容两种根元素、两种 ID 风格。

> **重要**：eNSP GUI **只识别原生 `<topo>` 根元素**，项目简化变体 `<topology>` 在
> `topo_parser.py` 解析正常，但用 eNSP 打开后画布会一片空白、不渲染任何设备。
> 如果文件要让 eNSP GUI 渲染，**必须按原生格式写**：根元素 `<topo>`、UUID id、
> 完整 `poe/settings/system_mac/com_port/bootmode/cx/cy/edit_left/edit_top` 属性、
> `<interfacePair>` 完整 `srcBoundRect_*` / `tarBoundRect_*` / `srcOffset_*` / `tarOffset_*` 锚点。
> 仅供 parser 测试 / 离线分析的 .topo 才可使用简化变体。

---

## 2. 顶层结构

```xml
<?xml version="1.0" encoding="UTF-8"?>
<topology name="实验标题" version="1.0">
    <!-- 注释块（可选，便于人阅读）：拓扑图、IP 规划、验证点 -->
    <devices>...</devices>
    <lines>...</lines>
    <!-- 可选：shapes / txttips / startup-config，仅原生 eNSP 才有 -->
</topology>
```

`shapes`（彩色区域框）和 `txttips`（loopback/网段标签）在项目简化版本中省略即可。

---

## 3. 设备（`<dev>`）

### 3.1 必填属性

| 属性 | 含义 | 示例 |
|---|---|---|
| `id` | 唯一标识 | `D1`（项目）或真实 UUID（原生） |
| `name` | 画布上显示的标签 | `AR1`、`LSW1`、`PC1`、`AC1` |
| `model` | 设备型号 | `AR2240`、`S5700`、`PC`、`AC6005` |
| `com_port` | Telnet 端口 | 路由器 `2000` 起递增；`PC`/`STA`/`Laptop`/`MCS` 固定 `0`；`Cloud` 固定 `0` |
| `cx` `cy` | 画布坐标 | 整数 |
| `system_mac` | 设备 MAC（建议填，否则 eNSP 随机） | `00-E0-FC-12-34-56` |

### 3.2 接口槽位

每台设备用 `<slot number="slot17" isMainBoard="1">` 声明插槽，里面放若干
`<interface sztype="Ethernet" interfacename="GE|Ethernet|Serial" count="N" />`。

> **关键规则**：每个 `<interface>` 子元素代表一组连续同类型接口；**多组之间按文件顺序累加索引**。
> 全局接口名公式 `GE0/<slot_idx>/<slot_offset>`。

例：`AR2240` 默认 6 个 GE 口，分 3 个 LPU 板 → 项目写法：
```xml
<slot number="slot17" isMainBoard="1">
    <interface sztype="Ethernet" interfacename="GE" count="2" />  <!-- GE0/0/0, GE0/0/1 -->
    <interface sztype="Ethernet" interfacename="GE" count="2" />  <!-- GE0/0/2, GE0/0/3 -->
    <interface sztype="Ethernet" interfacename="GE" count="2" />  <!-- GE0/0/4, GE0/0/5 -->
</slot>
```

### 3.3 设备型号 × 默认接口（速查表）

| 型号 | 类别 | 默认接口 | com_port 起点 |
|---|---|---|---|
| `AR1220` | 路由器 | 2 个 GE + 8 个 Ethernet + 2 个 Serial | 2000 |
| `AR2220` | 路由器 | 1 个 GE + 2 个 GE（两个 LPU 板） | 2000 |
| `AR2240` | 路由器 | 同 AR1220 双板 | 2000 |
| `S5700` | 交换机 | 24 个 GE | 2000 |
| `S3700` | 接入交换机 | 24 个 FE | 2000 |
| `S2700` | 接入交换机 | 24 个 FE | 2000 |
| `AC6005` | 无线控制器 | 8 个 GE | 2000 |
| `AP6050` | AP | 2 个 GE（上联 + Console） | 2000 |
| `STA` | 终端 | 0 端口（无线） | 0 |
| `Laptop` | 终端 | 1 个 Ethernet | 0 |
| `MCS` | 组播服务器 | 1 个 Ethernet | 0 |
| `PC` | PC | 1 个 GE（面板里改 Ethernet 也可） | 0 |
| `USG5500` | 防火墙 | 多个 GE + Serial | 2000 |
| `Cloud` | 本地网卡桥接 | Ethernet × N（按需） | 0 |

### 3.4 完整设备示例

```xml
<dev id="D1" name="AR1" model="AR2240" com_port="2000"
     cx="300" cy="120"
     system_mac="00-E0-FC-41-57-4F">
    <slot number="slot17" isMainBoard="1">
        <interface sztype="Ethernet" interfacename="GE" count="2" />
        <interface sztype="Ethernet" interfacename="GE" count="2" />
        <interface sztype="Ethernet" interfacename="GE" count="2" />
    </slot>
</dev>
```

### 3.5 PC / 终端（settings 字段）

`PC`/`Laptop`/`STA`/`MCS` 通常带 `settings=" -simpc_ip ... -simpc_mask ... -simpc_gateway ..."`
预置 IP/MAC。**新建拓扑可省略 settings**，启动后用户在 eNSP GUI 配。

---

## 4. 连线（`<line>`）

```xml
<line srcDeviceID="D1" destDeviceID="D2">
    <interfacePair lineName="Copper" srcIndex="0" tarIndex="0" />
</line>
```

| 字段 | 含义 |
|---|---|
| `srcDeviceID` / `destDeviceID` | 设备 id（必须 `<devices>` 里出现过） |
| `lineName` | `Copper`（网线，默认） / `Serial`（串口） / `Auto`（自动协商） |
| `srcIndex` | 源设备在 slot 树中的**全局接口序号**（0-based，从上到下累加） |
| `tarIndex` | 目标设备的全局接口序号 |

> 同设备自环（如 SW 的 trunk 自环）：`srcDeviceID="D1" destDeviceID="D1"`，`srcIndex` `tarIndex`
> 不同。

---

## 5. 链路类型选择规则

| 场景 | 推荐 lineName |
|---|---|
| 两台路由器/交换机以太口相连 | `Copper` |
| 串口背靠背（PPP/HDLC/帧中继实验） | `Serial` |
| 链路类型不确定 / 让 eNSP 自适应 | `Auto` |

---

## 6. 画布布局惯例

- **cx / cy 单位为像素**，画布默认约 1600×900。
- **连线清爽原则**：
  - 多设备排成网格（每行 80~150px）；
  - 直连用正交走线（坐标贴近 `src/dest` 的边中点）；
  - 不要让一条线水平穿过 3 台以上设备。
- **避免重叠**：
  - 路由器/交换机独占一行；
  - PC/STA 贴在所连交换机两侧，距离 50~80px；
  - 同区域用注释块解释。

### 常用坐标排布参考

| 模式 | 坐标 |
|---|---|
| 三角形（3 路由器） | 上 `(400,120)`、左下 `(150,360)`、右下 `(650,360)` |
| 全互联 4 节点 | 四角 `(120,120)(680,120)(120,440)(680,440)` |
| 串型 5 节点 | 横向均匀 `cx = 100, 300, 500, 700, 900`，同一行 |

---

## 7. 命名与编号约定

- **设备名**：业务命名（R1/R2/...；SW1/SW2/...；PC1/PC2/...；AC1；AP1）。
- **设备 ID**：项目用 `D1`/`D2`（按文件顺序 +1）。
- **com_port 分配**：所有可网管设备从 `2000` 起**严格递增 1**，禁止跳号，禁止复用。
- **MAC 地址**：`00-E0-FC-XX-XX-XX`，前 3 段固定（eNSP 真实设备风格），后 3 段随机。

---

## 8. IP 与子网规划（顺带规则）

- P2P 链路：`10.x.y.0/24` 或 `192.168.xy.0/24`（xy = 设备编号对，如 R1-R2 用 `12`）；
- LAN 网段：与链路号错开（如 R1 LAN = `192.168.1.0/24`，R2 LAN = `192.168.2.0/24`）；
- Loopback：`10.0.x.x/32`（IGP）或 `10.x.x.x/24`（汇总友好）；
- 末段习惯：`.1` 给路由器网关、`.2..6` 给下游设备。

---

## 9. 创建新 .topo 的推荐流程

1. **明确需求**：几台设备、什么拓扑形状、要验证什么协议/特性。
2. **选型**：路由器 `AR2240`（默认）、交换机 `S5700`（默认）；特殊场景换 CE / USG。
3. **定 ID/坐标**：在脑里画一遍网格，再落到 `cx`/`cy`。
4. **写 `<devices>`**：按拓扑顺序写 `D1, D2, ...`；`com_port` 从 2000 连号。
5. **声明接口**：按"全局索引 = srcIndex/tarIndex 用到的最大值 + 1"声明。
6. **写 `<lines>`**：列出每条连接，`lineName`/`srcIndex`/`tarIndex` 三件套。
7. **Jinja2 / XML 语法自检**：标签闭合、配对、无中文逗号在属性里。
8. **复检**：用 `topo_parser.parse_topo()` 读一遍，确认 device/connection 数对得上。

---

## 10. 最小可工作模板（必须用 eNSP 渲染时）

```xml
<?xml version="1.0" encoding="UTF-8"?>
<topo version="1.3.00.100">
    <devices>
        <dev id="243E08DD-3632-4006-8EA7-A614538D9467" name="AR1" poe="0" model="AR2240"
             settings="" system_mac="00-E0-FC-00-00-01" com_port="2000" bootmode="0"
             cx="300.000000" cy="200.000000" edit_left="327" edit_top="254">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="2" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
        <dev id="36334775-2840-4764-8C7C-487118E7FCC4" name="AR2" poe="0" model="AR2240"
             settings="" system_mac="00-E0-FC-00-00-02" com_port="2001" bootmode="0"
             cx="600.000000" cy="200.000000" edit_left="627" edit_top="254">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="GE" count="2" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
                <interface sztype="Ethernet" interfacename="GE" count="2" />
            </slot>
        </dev>
        <dev id="C36F2B24-0795-497C-BF8A-3D66EE47BC17" name="PC1" poe="0" model="PC"
             settings="" system_mac="00-00-00-00-00-01" com_port="0" bootmode="0"
             cx="300.000000" cy="400.000000" edit_left="327" edit_top="454">
            <slot number="slot17" isMainBoard="1">
                <interface sztype="Ethernet" interfacename="Ethernet" count="1" />
            </slot>
        </dev>
    </devices>
    <lines>
        <line srcDeviceID="243E08DD-3632-4006-8EA7-A614538D9467"
              destDeviceID="36334775-2840-4764-8C7C-487118E7FCC4">
            <interfacePair lineName="Copper" srcIndex="0" srcBoundRectIsMoved="1"
                           srcBoundRect_X="350.000000" srcBoundRect_Y="250.000000"
                           srcOffset_X="50.000000" srcOffset_Y="50.000000"
                           tarIndex="0" tarBoundRectIsMoved="1"
                           tarBoundRect_X="550.000000" tarBoundRect_Y="250.000000"
                           tarOffset_X="-50.000000" tarOffset_Y="50.000000" />
        </line>
        <line srcDeviceID="243E08DD-3632-4006-8EA7-A614538D9467"
              destDeviceID="C36F2B24-0795-497C-BF8A-3D66EE47BC17">
            <interfacePair lineName="Copper" srcIndex="1" srcBoundRectIsMoved="1"
                           srcBoundRect_X="264.644661" srcBoundRect_Y="235.355339"
                           srcOffset_X="-35.355339" srcOffset_Y="35.355339"
                           tarIndex="0" tarBoundRectIsMoved="1"
                           tarBoundRect_X="335.355339" tarBoundRect_Y="364.644661"
                           tarOffset_X="35.355339" tarOffset_Y="-35.355339" />
        </line>
    </lines>
    <shapes />
    <txttips />
</topo>
```

> 锚点字段算法：`srcBoundRect` = 设备中心点 + 指向目标方向 50px 偏移；
> `srcOffset` = `srcBoundRect` − 设备中心点（即 ±50 方向分量）。
> 简化场景下可直接用设备中心点 + 一个 50px 朝目标方向的向量代替。

---

## 11. 验证清单（提交前自查）

- [ ] 所有 `<dev>` 的 `id` 在文件内唯一
- [ ] `com_port` 从 2000 连号且不重复
- [ ] 所有 `<line>` 的 `srcDeviceID` / `destDeviceID` 都能找到对应设备
- [ ] `srcIndex` / `tarIndex` 不超过目标设备声明的总接口数
- [ ] XML 标签全部正确闭合（可用浏览器或 `xmllint` 验证）
- [ ] `topo_parser.parse_topo(path)` 能成功解析，devices/connections 数符合预期
- [ ] `settings="..."` 中的参数都以空格 + `-` 前缀（PC/MCS/STA），无半角逗号冲突
- [ ] 中文标点、英文标点按使用规则不混用（在注释 block 里允许有中文标点）

---

## 12. 参考示例（项目内已落地）

- `examples/topologies/router_on_a_stick.topo` —— 单臂路由验证（dot1q 子接口）
- `examples/topologies/ospf_single_area_three_gateways.topo` —— OSPF 三网关实验
- `examples/topologies/vlan_trunk_access.topo` —— VLAN Trunk/Access 验证

需要新拓扑时，**优先用 `NativeTopoBuilder` 生成**（见第 13 节），避免手写 XML
出错导致 eNSP 渲染空白。复制现有文件改 ID/坐标/连线作为兜底。

---

## 13. NativeTopoBuilder 用法（强制原生格式）

> 为根除「简化变体不被 eNSP GUI 渲染」类问题，本项目提供
> :class:`grbj_ensp_mcp.topo_builder.NativeTopoBuilder` 生成器。
> 调用者**只关心业务数据**（设备清单 + 连线关系），所有易错的 XML 细节
> （UUID、补齐 `poe/bootmode/settings/edit_left/edit_top`、6 位浮点坐标、
> `srcBoundRect_*` / `tarBoundRect_*` 锚点）由生成器强制按真实原生格式输出。

### 13.1 最小使用示例

```python
from grbj_ensp_mcp.topo_builder import NativeTopoBuilder

b = (
    NativeTopoBuilder()
    .add_device("AR1", model="AR2240", com_port=2000, cx=400, cy=100)
    .add_device("SW1", model="S5700", com_port=2001, cx=400, cy=300)
    .add_device("PC1", model="PC", com_port=0, cx=200, cy=500)
    .add_device("PC2", model="PC", com_port=0, cx=600, cy=500)
    .add_line("AR1", "SW1", src_index=0, tar_index=1)  # AR1.GE0/0/0 ↔ SW1.GE0/0/1
    .add_line("SW1", "PC1", src_index=2, tar_index=0)  # SW1.GE0/0/2 ↔ PC1.Ethernet0/0/0
    .add_line("SW1", "PC2", src_index=3, tar_index=0)  # SW1.GE0/0/3 ↔ PC2.Ethernet0/0/0
)
xml = b.render()
# xml 就是真实 eNSP 原生格式的 XML 字符串，可直接写入 .topo 文件
```

### 13.2 默认接口配置

`DEFAULT_SLOT_SPECS` 内置常用型号的默认接口规格：

| 型号 | slot 规格 | 总接口 |
|---|---|---|
| AR1220 | 2 GE + 8 Ethernet + 2 Serial | 12 |
| AR2220 | 1 GE + 2 GE | 3 |
| AR2240 | 2 GE + 2 GE + 2 GE | 6 |
| S2700 / S3700 | 24 FE | 24 |
| S5700 / S6700 | 24 GE | 24 |
| AC6005 | 8 GE | 8 |
| AP6050 | 2 GE | 2 |
| PC / Laptop / MCS | 1 Ethernet | 1 |
| STA | 无 | 0 |

需要自定义时，用 `slot_specs=[("GE", 4), ("Serial", 2)]` 显式指定。

### 13.3 校验与错误

`render()` 默认调用 `validate()`，发现以下问题直接抛 `ValueError`：

- 设备名重复
- `com_port` 重复（PC/STA=0 允许多个）
- 连线 `src_index` / `tar_index` 越界（超过设备接口数）
- 连线引用未声明设备

如果只是想预览不抛错，传 `validate=False`。

### 13.4 parser 自动联动

新版 `topo_parser.parse_topo()` / `parse_topo_bytes()` 会自动检测解析结果的
eNSP GUI 可渲染性，填入 `Topology.ensp_gui_renderable` 与 `ensp_gui_render_issues`。
简化变体（手写错）会被自动识别并报告具体问题（参考 `render_summary()` 输出）。

### 13.5 落地文件的工作流约束

本项目**严禁脚本直接写 .topo 文件**——必须用专用工具（Write / Edit）落地。
推荐流程：

1. 用 Python 写一段 builder 调用脚本（如 `examples/gen_xxx_topo.py`），
   内部 `print(b.render())` 输出 XML；
2. AI 把 print 输出复制到 Write 工具的 content 参数，写入目标路径；
3. 立即调用 `parse_topo()` 校验 `ensp_gui_renderable is True`；
4. git commit。

`examples/gen_router_on_stick_topo.py` 是这一流程的标准示范。
