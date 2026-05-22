@echo off
chcp 65001 >nul
title 量化平台V4 部署脚本
echo ============================================
echo   量化交易平台 V4 部署脚本
echo ============================================
echo.

:: 检查 Python 是否安装
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [信息] Python 版本:
python --version
echo.

:: 创建必要目录
echo [信息] 创建必要目录...
if not exist "logs" mkdir logs
if not exist "data_cache" mkdir data_cache
if not exist "reports" mkdir reports
if not exist "config" mkdir config

:: 创建默认配置文件（如果不存在）
if not exist "config\user_config.json" (
    echo [信息] 创建默认用户配置...
    echo {^"max_leverage^": 20,^"risk_per_trade_pct^": 1.0,^"daily_loss_limit_pct^": 5.0,^"strategy_cooldown_hours^": 12,^"exchange_mode^": "模拟",^"exchange_id^": "okx",^"custom_capital^": 10000.0} > config\user_config.json
)

if not exist "config\api_config.json" (
    echo [信息] 创建默认 API 配置模板...
    echo {^"version^": 1,^"data^": "",^"_note^": "API密钥加密存储。请通过应用内设置页填写API凭证。"} > config\api_config.json
)

:: 安装依赖
echo.
echo [信息] 安装 Python 依赖...
pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [警告] pip install 返回错误码 %ERRORLEVEL%%，请检查网络连接
    echo 可尝试手动执行: pip install -r requirements.txt
)

echo.
echo ============================================
echo   部署完成！
echo ============================================
echo.
echo 启动方式：
echo   1. 带自动重启守护:  python start_v4.py
echo   2. 直接启动主程序:  python main.py
echo   3. 仅状态页:        python start_v4.py --status
echo.
echo 状态页: http://localhost:8099
echo 日志目录: logs/
echo.
echo 注意事项：
echo   - OKX 模拟盘需要代理运行中
echo   - 首次运行请在设置页填写 API 凭证
echo   - 风控规则硬编码，AI 不可修改
echo   - 代码含中文注释，推荐 VS Code 编辑
echo   - 部署环境: Windows Server（已测试）
echo ============================================
echo.

pause
