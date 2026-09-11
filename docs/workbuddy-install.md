# 在其他电脑上用 WorkBuddy 接入 grbj-ensp-mcp

> 适用对象：把本包分发给其他使用 Windows + eNSP 的同学 / 同事，让他在自己电脑上的
> WorkBuddy 中配置并使用本 MCP 服务。

> **本分发唯一渠道**：广然笔记下载站 grbj.cn。其他渠道（PyPI / GitHub 镜像 / 第三方下载点）
> 一律不存在。本文档不再罗列其他安装方式，拿到 zip 就够用。

## 0. 前置条件

- Windows 10 / 11
- Python **3.10 及以上**
- eNSP（已在 eNSP GUI 里准备好 `.topo` 拓扑并启动设备）
- WorkBuddy 桌面客户端最新版

## 1. 一键安装（zip + install.bat，推荐）

直接把以下指令发给任意 WorkBuddy AI（**记得替换占位 URL 为下载站真实链接**）：

```
请访问 https://www.xxx.cn/xxx.zip ，下载并安装 grbj-ensp-mcp
```

> **占位说明**：把 `https://www.xxx.cn/xxx.zip` 替换成 grbj.cn 下载站上的真实 zip 直链
> （例如 `https://www.grbj.cn/dl/grbj-ensp-mcp-v0.2.2.zip`）。AI 收到后会自行拉 zip、解压、跑 install.bat。

如果不想走 AI、想手动跑，对应命令是：

```powershell
# 替换为你的真实下载链接
$url = "https://www.xxx.cn/xxx.zip"
$zip = "$env:TEMP\grbj-ensp-mcp.zip"

# 1. 拉 zip
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

# 2. 解压
Expand-Archive -Path $zip -DestinationPath "$env:TEMP\grbj-ensp-mcp" -Force

# 3. 跑安装脚本（自动检测同目录 wheel 并装本地，完全离线）
& "$env:TEMP\grbj-ensp-mcp\grbj-ensp-mcp-v0.2.2\install.bat"

# 4. 装好后去 WorkBuddy 连接器页点「信任」启用 grbj-ensp
```

install.bat 会自动完成：

1. 检测 Python ≥ 3.10
2. 建隔离 venv `~/.venvs/grbj-ensp-mcp`
3. `pip install` 同目录 wheel（**无任何额外下载**）
4. 写 `%APPDATA%\WorkBuddy\mcp.json`（自动备份 `.bak`）
5. 安装配套 Skill `grbj-ensp-smart-config` 到 `~/.workbuddy/skills/`
6. `import` 验证安装成功

## 2. 在 WorkBuddy 中注册 MCP（开发者手动配置参考）

> **注**：用 install.bat 自动安装的用户**不需要看本节**——脚本已自动写好 mcp.json。
> 本节是给已经手动 `pip install grbj_ensp_mcp-0.2.2-py3-none-any.whl` 的开发者参考。

打开 WorkBuddy → **设置 → 连接器 / MCP** → 添加 MCP server。

### 配置方式 1：用 venv 中的 python（推荐，能 lock 依赖）

先建一个隔离 venv 装包：

```powershell
python -m venv C:\opt\grbj-ensp-mcp
C:\opt\grbj-ensp-mcp\Scripts\pip install grbj_ensp_mcp-0.2.2-py3-none-any.whl
```

然后在 WorkBuddy MCP 配置里填：

```json
{
  "mcpServers": {
    "grbj-ensp": {
      "command": "C:/opt/grbj-ensp-mcp/Scripts/python.exe",
      "args": ["-m", "grbj_ensp_mcp.server"]
    }
  }
}
```

### 配置方式 2：全局 Python（最简，但版本冲突风险）

```json
{
  "mcpServers": {
    "grbj-ensp": {
      "command": "python",
      "args": ["-m", "grbj_ensp_mcp.server"]
    }
  }
}
```

> 全局 Python 必须 ≥ 3.10 且已装好对应 wheel。多 Python 环境下
> 容易装错版本，建议用方式 1。

### 配置方式 3：直接调用 entry point（无需 -m）

```json
{
  "mcpServers": {
    "grbj-ensp": {
      "command": "C:/opt/grbj-ensp-mcp/Scripts/grbj-ensp-mcp.exe"
    }
  }
}
```

> 这是 wheel 注册的 console_script，等价于 `python -m grbj_ensp_mcp.server`。

### macOS / Linux（开发用）

```json
{
  "mcpServers": {
    "grbj-ensp": {
      "command": "/opt/grbj-ensp-mcp/bin/python",
      "args": ["-m", "grbj_ensp_mcp.server"]
    }
  }
}
```

### 安装配套 Skill（手动）

手动配置 MCP 的用户也建议安装配套 Skill（教 AI 先探查后配置、报错走诊断流程），
把 zip 内的 Skill 目录复制到 WorkBuddy 用户级技能目录：

```bash
# Windows
xcopy /E /I /Y skill\grbj-ensp-smart-config "%USERPROFILE%\.workbuddy\skills\grbj-ensp-smart-config"

# macOS / Linux
cp -r skill/grbj-ensp-smart-config ~/.workbuddy/skills/
```

复制后重启 WorkBuddy 会话即可生效。卸载时删除
`~/.workbuddy/skills/grbj-ensp-smart-config/` 目录即可（`uninstall.bat` 会自动处理）。

## 3. 启用 MCP

保存配置后，WorkBuddy 不会自动信任新 MCP，需要手动启用：

1. 打开 WorkBuddy 连接器页
2. 找到新增的 `grbj-ensp`
3. 点击 **「信任」** / **「启用」** 按钮
4. 状态变为 `connected` 后即可使用

## 4. 验证

在 WorkBuddy 对话里直接问：

> 「用 grbj-ensp 扫一下当前可达的 eNSP 设备端口」

AI 会调用 `ensp_scan_devices`，返回本机 2000-2100 端口段可达的设备列表。

或：

> 「解析一下 D:/lab/IPv4编址.topo，告诉我设备和连线」

AI 会调用 `ensp_parse_topology`。

如能正常返回说明 MCP 已就绪。

## 5. 典型工作流

```
1. 在 eNSP GUI 打开 .topo（文件 → 打开 → 选 .topo）
2. 启动所有设备（工具栏 ▶ / Ctrl+Alt+A）
3. 对 AI 说：「拓扑已启动，文件在 D:/lab/xxx.topo，做 IPv4 OSPF 单区域实验」
4. AI 调用 ensp_load_topology → 识别设备 + 探测端口 + 自动建连
5. AI 逐设备 ensp_get_device_info + ensp_get_running_config 探查真实状态
6. AI 基于探查结果生成命令，ensp_apply_experiment(plan=...) 下发 + 校验 + save
7. AI 输出「✅ 配置完成」报告
```

## 6. 故障排查

| 现象 | 排查 |
|---|---|
| WorkBuddy 提示「未找到命令」 | 检查 `command` 路径是否正确；用绝对路径而不是 `python` |
| WorkBuddy 提示「spawn failed」 | 在命令行手动跑一次 `command + args` 看具体报错 |
| `ModuleNotFoundError: grbj_ensp_mcp` | 装错了 Python：检查 `command` 指向的 python 是否就是 `pip install` 用的那个 |
| `EnsP port unreachable` | eNSP 设备没启动，去 eNSP GUI 看设备指示灯是否变绿 |
| `TimeoutError` 等回话 | 设备 console 5 分钟超时，断开重连（`ensp_disconnect_device` + `ensp_connect_device`） |
| 命令报 `Error: Unrecognized command` | 接口名要用全称（`GigabitEthernet0/0/0`），Loopback 写 `LoopBack0`（B 大写） |

## 7. 升级 / 卸载

升级流程：**永远通过重新跑 zip + install.bat**（不需要 `pip install -U`）：

```powershell
# 1. 从 grbj.cn 下载新版 zip（覆盖安装前先卸载旧 wheel）
C:\opt\grbj-ensp-mcp\Scripts\pip uninstall grbj-ensp-mcp -y

# 2. 解压新版 zip 并跑 install.bat
Expand-Archive .\grbj-ensp-mcp-v0.x.y.zip -DestinationPath .\grbj-ensp-mcp -Force
.\grbj-ensp-mcp\grbj-ensp-mcp-v0.x.y\install.bat
```

卸载：

```powershell
C:\opt\grbj-ensp-mcp\Scripts\pip uninstall grbj-ensp-mcp -y
```

并删除 `%APPDATA%\WorkBuddy\mcp.json` 里的 `grbj-ensp` 节点（备份 `.bak` 文件可保留作 rollback 参考）。

WorkBuddy 无需重启即可识别新版本（下次连接时生效）。

## 8. 重新构建 wheel 分发

如需打新 zip 发到下载站：

```bash
cd grbj-ensp-mcp
python -m build --outdir dist/
```

产物：

- `dist/grbj_ensp_mcp-0.2.2-py3-none-any.whl`
- `dist/grbj_ensp_mcp-0.2.2.tar.gz`（含 docs/examples/tests/skill 完整源码）

两份文件都已剔除 `.venv` / `.git` / 缓存 / 开发者本地 memory（`.workbuddy/`），
可直接发到广然笔记下载站。

打包成 zip（zip 不是 build 产物，是给终端用户的分发包）：

```bash
# 包含 install.bat / install.sh / uninstall.bat + 同目录 wheel 的完整分发 zip
# 详见项目根目录的 build_zip 流程（README 不展开）
```
