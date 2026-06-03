@echo off
chcp 65001 >nul
title ETFer 网格交易分析工具
cd /d "%~dp0backend"

echo ============================================
echo   ETFer 网格交易分析工具 - 启动中
echo ============================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python。
    echo 请先安装 Python 3.10-3.13: https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

REM 首次运行：创建虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [首次运行] 正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
    echo [首次运行] 正在安装依赖（约需 2-5 分钟，请耐心等待）...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 安装依赖失败，请检查网络后重试。
        pause
        exit /b 1
    )
    echo [首次运行] 依赖安装完成！
    echo.
)

echo 启动服务中... 启动后浏览器将自动打开 http://127.0.0.1:5000
echo （关闭此窗口即可停止服务）
echo.

REM 生产模式：后端同时托管前端页面，单端口访问
set FLASK_ENV=production
set FLASK_HOST=127.0.0.1
set FLASK_PORT=5000

REM 延迟 8 秒后自动打开浏览器
start "" cmd /c "timeout /t 8 >nul && start http://127.0.0.1:5000"

.venv\Scripts\python.exe main.py --port 5000 --host 127.0.0.1

pause
