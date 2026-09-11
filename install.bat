@echo off
REM ==========================================================================
REM   grbj-ensp-mcp 一键安装脚本 (Windows)
REM
REM   用途：从同目录 wheel 装包到隔离 venv + 注册到 WorkBuddy MCP 配置
REM         + 安装配套 Skill (grbj-ensp-smart-config) 到 ~/.workbuddy/skills/
REM   兼容：Windows 10 / 11，Python 3.10+
REM
REM   用法：
REM     把 grbj-ensp-mcp-v0.2.2.zip 解压后，跑里面的 install.bat。
REM     脚本自动检测同目录最新 wheel 文件 (grbj_ensp_mcp-*.whl)
REM     并装到本地 venv，无需任何额外下载。
REM
REM   流程：
REM     1. 检测 Python ≥ 3.10
REM     2. 建 ~/.venvs/grbj-ensp-mcp 虚拟环境
REM     3. pip install 同目录 wheel
REM     4. 写 %APPDATA%\WorkBuddy\mcp.json（自动备份 .bak）
REM     5. 安装配套 Skill 到 %USERPROFILE%\.workbuddy\skills\
REM     6. 验证安装
REM ==========================================================================

setlocal enabledelayedexpansion

REM ---------- 1. 定位同目录 wheel（取字典序最大的一个） ----------
set "WHEEL_PATH="
for /f "delims=" %%f in ('dir /b /o:n "%~dp0grbj_ensp_mcp-*.whl" 2^>nul') do set "WHEEL_PATH=%~dp0%%f"

echo.
echo ============================================================
echo   grbj-ensp-mcp 一键安装
echo ============================================================
echo.

if not defined WHEEL_PATH (
    echo [错误] 同目录未找到 wheel 文件 (grbj_ensp_mcp-*.whl)
    echo.
    echo   请确认 install.bat 是在解压后的 grbj-ensp-mcp-v0.2.2 目录里跑的：
    echo     1. 下载 grbj-ensp-mcp-v0.2.2.zip
    echo     2. 解压到任意目录
    echo     3. cd 进解压目录后再跑 install.bat
    echo.
    pause
    exit /b 1
)

echo   Wheel : %WHEEL_PATH%
echo   目标  : %USERPROFILE%\.venvs\grbj-ensp-mcp
echo.

REM ---------- 2. 检测 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10 及以上版本
    echo         下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PY_VERSION=%%v"
echo [1/6] 检测到 Python !PY_VERSION!

REM 校验版本 >= 3.10
for /f "tokens=1,2 delims=." %%a in ("!PY_VERSION!") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
if !PY_MAJOR! LSS 3 (
    echo [错误] Python 版本过低，需要 3.10+，当前 !PY_VERSION!
    pause
    exit /b 1
)
if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 10 (
    echo [错误] Python 版本过低，需要 3.10+，当前 !PY_VERSION!
    pause
    exit /b 1
)

REM ---------- 3. 建 venv ----------
set "VENV_DIR=%USERPROFILE%\.venvs\grbj-ensp-mcp"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [2/6] 创建虚拟环境 %VENV_DIR%
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [错误] 创建 venv 失败
        pause
        exit /b 1
    )
) else (
    echo [2/6] 虚拟环境已存在 %VENV_DIR%
)

REM ---------- 4. 装包 ----------
echo.
echo [3/6] 安装 grbj-ensp-mcp（本地 wheel）...
"%VENV_PY%" -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [错误] pip 升级失败
    pause
    exit /b 1
)
"%VENV_PY%" -m pip install --upgrade "%WHEEL_PATH%"
if errorlevel 1 (
    echo.
    echo [错误] 装包失败
    echo   手动测试：python -m pip install %WHEEL_PATH%
    echo.
    pause
    exit /b 1
)

REM ---------- 5. 注册到 WorkBuddy MCP ----------
REM [enhanced 修正] 上游写 %APPDATA%\WorkBuddy\mcp.json，WorkBuddy 实际读取
REM 的是 %USERPROFILE%\.workbuddy\mcp.json，此处已修正（上游 v0.2.2 的已知坑）。
set "MCP_JSON=%USERPROFILE%\.workbuddy\mcp.json"
set "MCP_JSON_BAK=%MCP_JSON%.bak"

echo.
echo [4/6] 注册到 WorkBuddy MCP 配置
echo       路径: %MCP_JSON%

if not exist "%USERPROFILE%\.workbuddy" mkdir "%USERPROFILE%\.workbuddy"

if exist "%MCP_JSON%" (
    copy /y "%MCP_JSON%" "%MCP_JSON_BAK%" >nul
    echo       已备份原配置到 %MCP_JSON_BAK%
)

REM 用 Python 合并 JSON
"%VENV_PY%" -c ^
"import json, sys, os
path = r'%MCP_JSON%'
data = {}
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print('警告：原配置不是合法 JSON，已重置：', e, file=sys.stderr)
            data = {}

if not isinstance(data, dict):
    data = {}
data.setdefault('mcpServers', {})

python_exe = r'%VENV_PY%'
data['mcpServers']['grbj-ensp'] = {
    'command': python_exe,
    'args': ['-m', 'grbj_ensp_mcp.server']
}

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('已写入', path)
"

if errorlevel 1 (
    echo [错误] 注册 MCP 失败
    pause
    exit /b 1
)

REM ---------- 6. 安装配套 Skill ----------
set "SKILL_SRC=%~dp0skill\grbj-ensp-smart-config"
set "SKILL_DST=%USERPROFILE%\.workbuddy\skills\grbj-ensp-smart-config"
set "SKILLS_ROOT=%USERPROFILE%\.workbuddy\skills"

echo.
echo [5/6] 安装配套 Skill (grbj-ensp-smart-config)

if exist "%SKILL_SRC%" (
    if not exist "%SKILLS_ROOT%" mkdir "%SKILLS_ROOT%"
    xcopy /e /i /y "%SKILL_SRC%" "%SKILL_DST%" >nul
    if errorlevel 1 (
        echo [警告] Skill 安装失败，MCP 功能不受影响，可手动复制：
        echo         xcopy /e /i /y "%SKILL_SRC%" "%SKILL_DST%"
    ) else (
        echo       已安装到 %SKILL_DST%
    )
) else (
    echo [警告] 未找到 skill 目录 %SKILL_SRC%，跳过 Skill 安装
)

REM ---------- 7. 验证 ----------
echo.
echo [6/6] 验证安装...
"%VENV_PY%" -c "import grbj_ensp_mcp; print('  ✓ grbj_ensp_mcp', grbj_ensp_mcp.__version__)"
if errorlevel 1 (
    echo [错误] 验证失败
    pause
    exit /b 1
)

REM ---------- 7. 总结 ----------
echo.
echo ============================================================
echo   安装完成
echo ============================================================
echo.
echo   虚拟环境 : %VENV_DIR%
echo   Python   : %VENV_PY%
echo   MCP 配置 : %MCP_JSON%
echo   Skill    : %USERPROFILE%\.workbuddy\skills\grbj-ensp-smart-config
echo.
echo   接下来请在 WorkBuddy 中：
echo     1. 打开 设置 ^>^> 连接器 / MCP
echo     2. 找到新增的 grbj-ensp
echo     3. 点击「信任」/「启用」
echo.
echo   之后在 eNSP GUI 启动设备，对 AI 说：
echo     "用 grbj-ensp 扫一下当前可达的 eNSP 设备端口"
echo.
echo   如需卸载：运行 uninstall.bat
echo.
pause
endlocal