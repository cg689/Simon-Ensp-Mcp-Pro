#!/usr/bin/env bash
# ==========================================================================
#   grbj-ensp-mcp 一键安装脚本 (macOS / Linux)
#
#   用途：从同目录 wheel 装包到隔离 venv + 注册到 WorkBuddy MCP 配置
#         + 安装配套 Skill (grbj-ensp-smart-config) 到 ~/.workbuddy/skills/
#   注意：eNSP 官方仅支持 Windows，本脚本给开发者在 macOS/Linux 上联调测试用
#
#   用法：
#     把 grbj-ensp-mcp-v0.2.0.zip 解压后，跑里面的 install.sh。
#     脚本自动检测同目录最新 wheel 文件 (grbj_ensp_mcp-*.whl)
#     并装到本地 venv，无需任何额外下载。
#
#   流程：
#     1. 检测 Python ≥ 3.10
#     2. 建 ~/.venvs/grbj-ensp-mcp 虚拟环境
#     3. pip install 同目录 wheel
#     4. 写 WorkBuddy mcp.json（自动备份 .bak）
#     5. 安装配套 Skill 到 ~/.workbuddy/skills/
#     6. 验证安装
# ==========================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 取字典序最大的 wheel（版本号升级时字典序递增）
WHEEL_PATH="$(ls -1 "$SCRIPT_DIR"/grbj_ensp_mcp-*.whl 2>/dev/null | sort | tail -1)"

VENV_DIR="$HOME/.venvs/grbj-ensp-mcp"
VENV_PY="$VENV_DIR/bin/python"

# macOS / Linux 上的 WorkBuddy 配置路径
if [[ "$OSTYPE" == "darwin"* ]]; then
    MCP_JSON="$HOME/Library/Application Support/WorkBuddy/mcp.json"
else
    MCP_JSON="$HOME/.config/WorkBuddy/mcp.json"
fi
MCP_JSON_BAK="${MCP_JSON}.bak"

echo
echo "============================================================"
echo "  grbj-ensp-mcp 一键安装 (macOS / Linux)"
echo "============================================================"
echo

if [[ -z "$WHEEL_PATH" ]]; then
    echo "[错误] 同目录未找到 wheel 文件 (grbj_ensp_mcp-*.whl)"
    echo
    echo "  请确认 install.sh 是在解压后的 grbj-ensp-mcp-v0.2.0 目录里跑的："
    echo "    1. 下载 grbj-ensp-mcp-v0.2.0.zip"
    echo "    2. 解压到任意目录"
    echo "    3. cd 进解压目录后再跑 install.sh"
    exit 1
fi

echo "  Wheel : $WHEEL_PATH"
echo "  目标  : $VENV_DIR"
echo

# ---------- 1. 检测 Python ----------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[1/6] 检测到 Python ${PY_VERSION}"

if ! python3 -c "import sys; sys.exit(0 if (sys.version_info.major, sys.version_info.minor) >= (3, 10) else 1)"; then
    echo "[错误] Python 版本过低，需要 3.10+，当前 ${PY_VERSION}"
    exit 1
fi

# ---------- 2. 建 venv ----------
if [ ! -x "$VENV_PY" ]; then
    echo "[2/6] 创建虚拟环境 $VENV_DIR"
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
else
    echo "[2/6] 虚拟环境已存在 $VENV_DIR"
fi

# ---------- 3. 装包 ----------
echo
echo "[3/6] 安装 grbj-ensp-mcp（本地 wheel）..."
"$VENV_PY" -m pip install --upgrade pip --quiet
if ! "$VENV_PY" -m pip install --upgrade "$WHEEL_PATH"; then
    echo
    echo "[错误] 装包失败"
    echo "  手动测试：python3 -m pip install $WHEEL_PATH"
    exit 1
fi
echo "      安装完成"

# ---------- 4. 注册到 WorkBuddy MCP ----------
echo
echo "[4/6] 注册到 WorkBuddy MCP 配置"
echo "      路径: $MCP_JSON"

mkdir -p "$(dirname "$MCP_JSON")"

if [ -f "$MCP_JSON" ]; then
    cp -f "$MCP_JSON" "$MCP_JSON_BAK"
    echo "      已备份原配置到 $MCP_JSON_BAK"
fi

VENV_PY="$VENV_PY" MCP_JSON="$MCP_JSON" "$VENV_PY" - <<'PY'
import json
import os

mcp_json = os.environ["MCP_JSON"]
venv_py = os.environ["VENV_PY"]

data = {}
if os.path.exists(mcp_json):
    with open(mcp_json, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as exc:
            print(f"警告：原配置不是合法 JSON，已重置: {exc}", flush=True)
            data = {}

if not isinstance(data, dict):
    data = {}
data.setdefault("mcpServers", {})

data["mcpServers"]["grbj-ensp"] = {
    "command": venv_py,
    "args": ["-m", "grbj_ensp_mcp.server"],
}

with open(mcp_json, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"      已写入 {mcp_json}")
PY

# ---------- 5. 安装配套 Skill ----------
echo
echo "[5/6] 安装配套 Skill (grbj-ensp-smart-config)"

SKILL_SRC="$SCRIPT_DIR/skill/grbj-ensp-smart-config"
SKILL_DST="$HOME/.workbuddy/skills/grbj-ensp-smart-config"

if [ -d "$SKILL_SRC" ]; then
    mkdir -p "$SKILL_DST"
    cp -f "$SKILL_SRC"/* "$SKILL_DST"/
    echo "      已安装到 $SKILL_DST"
else
    echo "      [警告] 未找到 skill 目录 $SKILL_SRC，跳过 Skill 安装"
fi

# ---------- 6. 验证 ----------
echo
echo "[6/6] 验证安装..."
"$VENV_PY" -c "import grbj_ensp_mcp; print('      ✓ grbj_ensp_mcp', grbj_ensp_mcp.__version__)"

# ---------- 总结 ----------
echo
echo "============================================================"
echo "  安装完成"
echo "============================================================"
echo
echo "  虚拟环境 : $VENV_DIR"
echo "  Python   : $VENV_PY"
echo "  MCP 配置 : $MCP_JSON"
echo "  Skill    : $HOME/.workbuddy/skills/grbj-ensp-smart-config"
echo
echo "  接下来请在 WorkBuddy 中："
echo "    1. 打开 设置 → 连接器 / MCP"
echo "    2. 找到新增的 grbj-ensp"
echo "    3. 点击「信任」/「启用」"
echo
echo "  注意：eNSP 官方仅支持 Windows，macOS / Linux 上 MCP 可装但连不到真机"
echo "  可用 examples/_diag_topo.py / examples/run_*_lab.py 在测试机模拟调试"
echo