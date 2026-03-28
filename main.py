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

from app import FileMailerApp


def main():
    """程序入口"""
    app = FileMailerApp()
    app.run()


if __name__ == "__main__":
    main()
