# ensp-topo-generate · 分发说明

一个可独立使用的技能：**用脚本生成 eNSP 拓扑文件（.topo）**，产出文件可直接在 eNSP 打开。
自带环境引导脚本与 USG6000V / S3700 补丁，**不依赖原作者机器上的任何路径**。

## 前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Windows（eNSP 本身仅 Windows） |
| Python | 3.10 及以上（仅用于建独立 venv） |
| 网络 | 首次安装需联网（拉取 mcp SDK 等依赖）；**离线方案见下文 FAQ** |
| eNSP | 需自行安装（本技能只生成文件，不启动 eNSP） |
| MCP | **可选**。生成拓扑完全不需要 MCP；只有"设备启动后自动下发配置"才需要额外配置 MCP 服务 |

## 三步安装

```bash
# 1) 用任意 Python 3.10+ 建环境（在技能目录下创建 .venv，装包并打补丁）
python scripts/setup_env.py

# 2) 端到端自检（应输出多行 PASS + "全部通过，环境可用"）
"<技能目录>/.venv/Scripts/python.exe" scripts/selfcheck.py

# 3) 用 setup 打印的解释器路径执行生成脚本
"<技能目录>/.venv/Scripts/python.exe" scripts/gen_topo_template.py   # 模板：改两张表后运行
```

setup 成功时会打印环境位置与解释器绝对路径；自检全绿说明「装包 + 补丁 + GBK 落盘 + 回读」整条链路可用。

## 目录结构

```
ensp-topo-generate/
├── SKILL.md                      # 技能主文档（工作流、映射表、坑表）
├── README-分发说明.md             # 本文件
├── scripts/
│   ├── setup_env.py              # 一键建 venv + 装包 + 打补丁
│   ├── selfcheck.py              # 端到端自检
│   ├── gen_topo_template.py      # 生成脚本模板（改两张表即可用）
│   └── patched/
│       ├── topo_builder.py       # 已打补丁的完整文件（setup 会覆盖安装副本）
│       └── topo_builder.patch    # unified diff（可审计补丁内容）
└── assets/
    ├── grbj_ensp_mcp-0.2.2-py3-none-any.whl   # 待安装的包
    └── grbj-ensp-mcp-v0.2.2.zip                # 原厂分发件（含源码/文档/示例）
```

## 放到哪里

技能需要放进你所用的 agent 工具的**用户级技能目录**：

- WorkBuddy：`~/.workbuddy/skills/`
- Claude Code：`~/.claude/skills/`
- 其他工具：放到其文档说明的用户级 skill 根目录下（目录名保持 `ensp-topo-generate`）

放好后重启/刷新工具，技能即可被识别。**即使不放进技能目录也能直接用**——把 `scripts/` 当普通脚本跑即可，SKILL.md 只是给 agent 看的说明书。

## 常见问题

**Q：没有网络怎么装？**
在能联网的机器上把依赖下载齐，放进 `assets/wheels/`（目录不存在就新建），setup 会自动把它作为额外来源：
```bash
pip download -d assets/wheels "grbj_ensp_mcp-0.2.2-py3-none-any.whl 所需依赖"
```
然后再执行 `python scripts/setup_env.py`。

**Q：装了之后生成含 USG6000V 的拓扑报错 / 设备缺失？**
说明补丁被覆盖了（例如重装/升级过 wheel）。重跑一次 `python scripts/setup_env.py` 即可恢复（它会把 `scripts/patched/topo_builder.py` 重新覆盖到 site-packages）。

**Q：怎么确认补丁生效？**
```bash
"<技能目录>/.venv/Scripts/python.exe" -c "import grbj_ensp_mcp,os,re;p=os.path.dirname(grbj_ensp_mcp.__file__);print('USG6000V' in open(os.path.join(p,'topo_builder.py'),encoding='utf-8').read())"
```
输出 `True` 即已打补丁；或直接跑 `scripts/selfcheck.py`，其中一项专测"USG6000V 枚举式渲染"。

**Q：Open 出来的拓扑里中文设备名乱码？**
生成脚本必须按 eNSP 原生格式落盘（`encoding="UNICODE"` 假声明 + CRLF + GBK，无 BOM）——模板与示例已内置，**不要改成 UTF-8**。

**Q：Linux / macOS 能用吗？**
生成脚本本身是纯 Python，理论可跑；但 `.topo` 只能在 Windows 的 eNSP 中打开，且补丁/路径约定按 Windows 编写，未做跨平台验证。

**Q：支持哪些设备型号？**
AR2220、S5700、S3700、USG6000V（需补丁）、AC6005、AP6050、PC、Server、STA。其他型号走默认接口表，需人工确认。

## 许可与来源

- 底层包 `grbj-ensp-mcp v0.2.2` 为 **MIT License**（Copyright (c) 2026 grbj-ensp-mcp contributors），原分发件见 `assets/grbj-ensp-mcp-v0.2.2.zip`，**再分发请保留其 LICENSE**。
- 本技能与补丁为使用者在本地构建：补丁内容 = USG6000V 枚举式接口渲染 + S3700 实测接口表，来源为真实 eNSP 保存样本的逆向（非官方支持的手改拓扑行为）。
