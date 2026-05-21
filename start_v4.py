# -*- coding: utf-8 -*-
"""
启动脚本 —— 带异常自动重启的进程守护。
启动后监控 main.py 进程，异常退出时自动重启（最多3次/小时）。

运行方式:
    python start_v4.py
    python start_v4.py --status        (启动 HTTP 状态页)
    python start_v4.py --no-restart    (禁用自动重启，仅启动)

特性:
    - 子进程监控，异常退出自动重启
    - 每小时最多重启 3 次（防止死循环）
    - 日志轮转（RotatingFileHandler, 10MB × 30 份）
    - 可选 HTTP 状态页（端口 8099）
"""

import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ===== 路径配置 =====
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ===== 日志配置（RotatingFileHandler 轮转） =====
logger = logging.getLogger("start_v4")
logger.setLevel(logging.DEBUG)

# 控制台 handler
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console_fmt = logging.Formatter(
    "[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
console.setFormatter(console_fmt)
logger.addHandler(console)

# 文件 handler（轮转：10MB × 30 份 ≈ 300MB）
file_handler = RotatingFileHandler(
    LOG_DIR / "start_v4.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=30,              # 保留最近 30 份
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)
file_fmt = logging.Formatter(
    "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
)
file_handler.setFormatter(file_fmt)
logger.addHandler(file_handler)


def get_restart_count(window_seconds: int = 3600) -> int:
    """统计最近 window_seconds 秒内的重启次数（从日志读取）。"""
    log_file = LOG_DIR / "start_v4.log"
    if not log_file.exists():
        return 0
    cutoff = time.time() - window_seconds
    count = 0
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if "进程异常退出，准备重启" in line:
                    # 从日志行提取时间戳
                    try:
                        ts_str = line.split(",")[0].strip("[]")
                        ts = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
                        if ts >= cutoff:
                            count += 1
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return count


def run_main() -> int:
    """启动 main.py 并返回退出码。"""
    main_py = PROJECT_ROOT / "main.py"
    if not main_py.exists():
        logger.error(f"找不到主程序: {main_py}")
        return -1

    logger.info(f"启动主程序: {main_py}")
    proc = subprocess.Popen(
        [sys.executable, str(main_py)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # 实时读取并记录输出
    for line in proc.stdout:
        line = line.strip()
        if line:
            logger.debug(f"[main] {line}")

    proc.wait()
    exit_code = proc.returncode
    logger.info(f"主程序退出，退出码: {exit_code}")
    return exit_code


def start_http_status_server():
    """启动简单 HTTP 状态页（端口 8099，返回 JSON 状态）。"""
    import http.server
    import json as json_mod

    class StatusHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = {
                "status": "running",
                "project": "量化平台V4",
                "pid": os.getpid(),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "uptime_seconds": int(time.time() - start_time),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(
                json_mod.dumps(status, ensure_ascii=False, indent=2).encode("utf-8")
            )

        def log_message(self, format, *args):
            pass  # 抑制默认日志

    start_time = time.time()
    server = http.server.HTTPServer(("0.0.0.0", 8099), StatusHandler)
    logger.info(f"HTTP 状态页已启动: http://localhost:8099")
    server.serve_forever()


def main():
    """主入口：循环监控 + 自动重启。"""
    args = sys.argv[1:]

    # 只启动状态页
    if "--status" in args:
        start_http_status_server()
        return

    no_restart = "--no-restart" in args

    logger.info("=" * 50)
    logger.info("量化平台V4 启动守护进程")
    logger.info(f"项目目录: {PROJECT_ROOT}")
    logger.info(f"Python: {sys.executable}")
    logger.info(f"自动重启: {'禁用' if no_restart else '启用（最多3次/小时）'}")
    logger.info("=" * 50)

    # 在后台线程启动 HTTP 状态页
    import threading

    status_thread = threading.Thread(target=start_http_status_server, daemon=True)
    status_thread.start()
    time.sleep(0.5)  # 等待服务就绪

    if no_restart:
        exit_code = run_main()
        logger.info(f"主程序退出（无重启模式），退出码: {exit_code}")
        sys.exit(exit_code)

    # 循环监控 + 自动重启
    while True:
        exit_code = run_main()

        if exit_code == 0:
            logger.info("主程序正常退出，守护进程结束。")
            break

        # 检查重启次数
        recent_restarts = get_restart_count()
        logger.info(f"最近1小时内重启次数: {recent_restarts}/3")

        if recent_restarts >= 3:
            logger.error(
                "1小时内重启达到3次上限，停止守护。请检查错误日志后手动重启。"
            )
            break

        logger.warning(f"进程异常退出（退出码={exit_code}），等待5秒后重启...")
        time.sleep(5)
        logger.info("正在重启...")


if __name__ == "__main__":
    main()
