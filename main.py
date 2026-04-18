# -*- coding: utf-8 -*-
"""
文件分卷压缩 & 邮件自动发送工具 - 入口文件

功能概述:
    1. 浏览选择文件或文件夹
    2. 分卷压缩（7z + AES-256 加密）
    3. 选择发件账号和多个收件人
    4. 自动逐一发送分卷邮件，支持设定发送间隔
"""

import sys
import os

# 将当前脚本目录添加到 Python 路径，确保模块导入正常
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import argparse

def main():
    """程序入口"""
    parser = argparse.ArgumentParser(description="文件分卷压缩 & 邮件自动发送工具")
    parser.add_argument("--cli", action="store_true", help="启用无界面的命令行静默模式")
    parser.add_argument("--files", nargs='+', help="需要打包发送的文件或目录路径。如: --files path1 path2")
    parser.add_argument("--to-addrs", help="收件人邮箱，多个以逗号隔开")
    parser.add_argument("--account", help="使用的发件账号名称或邮箱，多个以逗号分隔（需在界面提前配置过）")
    parser.add_argument("--subject", default="", help="(可选) 邮件主题前缀")
    parser.add_argument("--volume", type=int, default=10, help="(可选) 压缩分卷大小(MB)，默认 10")
    parser.add_argument("--password", default=None, help="(可选) 解压密码")
    parser.add_argument("--interval", type=int, default=30, help="(可选) 发送间隔(秒)，默认 30")
    parser.add_argument("--send-limit", type=int, default=0, help="(可选) 每个发件账号最大发件数，0=不限制，达到上限自动切换下一个账号")
    parser.add_argument("--max-retries", type=int, default=10, help="(可选) 单包断网最高重连次数，默认 10")
    parser.add_argument("--retry-wait", type=int, default=15, help="(可选) 重连时的避让耗时(秒)，默认 15")
    parser.add_argument("--prompt-timeout", type=int, default=30, help="(可选) 失败时留给用户选择 Y/N 的倒计时(秒)，默认 30")
    parser.add_argument("--resume", action="store_true", help="(高级) 断点续传，跳过压缩直接续发")

    # 仅提取已知参数防止在 GUI 运行双击时带有其他传参引发报错
    args, unknown = parser.parse_known_args()

    if args.cli:
        import cli_runner
        cli_runner.run_cli(args)
    else:
        # 进入常规 GUI 面板
        # 延迟导入，防止在纯 CLI/Termux 环境下因缺少 tkinter 库而报错
        from app import FileMailerApp
        app = FileMailerApp()
        app.run()


if __name__ == "__main__":
    main()
