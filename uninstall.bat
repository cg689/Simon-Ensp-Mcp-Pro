@echo off
REM ==========================================================================
REM   grbj-ensp-mcp 一键卸载脚本 (Windows)
REM
REM   用法：uninstall.bat
REM   行为：
REM     1. 关闭并移除 ~/.venvs/grbj-ensp-mcp 虚拟环境
REM     2. 从 %APPDATA%\WorkBuddy\mcp.json 中移除 grbj-ensp 配置项
REM     3. 移除配套 Skill (%USERPROFILE%\.workbuddy\skills\grbj-ensp-smart-config)
REM     4. 保留 mcp.json.bak 备份（如有）
REM ==========================================================================

setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   grbj-ensp-mcp 一键卸载
echo ============================================================

set "VENV_DIR=%USERPROFILE%\.venvs\grbj-ensp-mcp"
set "MCP_JSON=%APPDATA%\WorkBuddy\mcp.json"
set "MCP_JSON_BAK=%MCP_JSON%.bak"
set "SKILL_DST=%USERPROFILE%\.workbuddy\skills\grbj-ensp-smart-config"

REM ---------- 1. 删 venv ----------
if exist "%VENV_DIR%" (
    echo [1/3] 删除虚拟环境 %VENV_DIR%
    rmdir /s /q "%VENV_DIR%"
    if errorlevel 1 (
        echo [警告] 删除 venv 失败，请手动删除
    )
) else (
    echo [1/3] 虚拟环境不存在 %VENV_DIR%
)

REM ---------- 2. 清理 mcp.json ----------
echo.
echo [2/3] 从 WorkBuddy MCP 配置中移除 grbj-ensp
echo       路径: %MCP_JSON%

if not exist "%MCP_JSON%" (
    echo       MCP 配置文件不存在，无需清理
    goto :done
)

REM 备份
copy /y "%MCP_JSON%" "%MCP_JSON_BAK%" >nul 2>&1
echo       已备份到 %MCP_JSON_BAK%

REM 用 Python 安全移除键
where python >nul 2>&1
if errorlevel 1 (
    echo [警告] 未找到 python，请手动编辑 %MCP_JSON% 删除 grbj-ensp 项
    goto :done
)

python -c ^
"import json, os
path = r'%MCP_JSON%'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)
if 'mcpServers' in data and 'grbj-ensp' in data['mcpServers']:
    del data['mcpServers']['grbj-ensp']
    if not data['mcpServers']:
        del data['mcpServers']
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('       已移除 grbj-ensp 配置项')
else:
    print('       未找到 grbj-ensp 配置项，无需清理')
"

REM ---------- 3. 移除配套 Skill ----------
echo.
echo [3/3] 移除配套 Skill (grbj-ensp-smart-config)

if exist "%SKILL_DST%" (
    rmdir /s /q "%SKILL_DST%"
    if errorlevel 1 (
        echo [警告] Skill 移除失败，请手动删除 %SKILL_DST%
    ) else (
        echo       已移除 %SKILL_DST%
    )
) else (
    echo       Skill 不存在 %SKILL_DST%，无需清理
)

:done
echo.
echo ============================================================
echo   卸载完成
echo ============================================================
echo.
echo   WorkBuddy 中记得刷新或重启连接器页生效。
echo.
pause
endlocal
