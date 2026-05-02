#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time
import argparse
import tempfile
import shutil
import traceback
from typing import List
from datetime import datetime

import config_manager
import compressor
import mailer


class TeeLogger:
    """将 stdout/stderr 同时输出到控制台和日志文件"""
    def __init__(self, log_path):
        self.terminal = sys.stdout
        self.log_file = open(log_path, 'a', encoding='utf-8', buffering=1)
        
    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
        
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
        
    def close(self):
        self.log_file.close()


def setup_logging(file_name: str = None) -> TeeLogger:
    """设置日志输出，自动保存到 send_logs/ 目录"""
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "send_logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    if file_name:
        base_name = os.path.splitext(os.path.basename(file_name))[0]
        log_name = f"{base_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    else:
        log_name = f"cli_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    log_path = os.path.join(logs_dir, log_name)
    logger = TeeLogger(log_path)
    # 保存原始 stdout/stderr，退出时恢复
    logger._original_stdout = sys.stdout
    logger._original_stderr = sys.stderr
    sys.stdout = logger
    sys.stderr = logger
    return logger

def timed_input(prompt: str, timeout: int, default: str) -> str:
    """Windows 下带倒计时的按键捕获，倒计时结束默认返回 default"""
    if sys.platform == 'win32':
        import msvcrt
        print(f"\r{prompt} (将在 {timeout} 秒后默认执行 '{default}') ", end='', flush=True)
        start_time = time.time()
        last_sec = timeout
        while True:
            elapsed = time.time() - start_time
            remaining = int(timeout - elapsed)
            
            if remaining < 0:
                print(f"\n[超时自动执行] -> {default}")
                return default
            
            if remaining != last_sec:
                # 刷新同行文字
                print(f"\r{prompt} (将在 {remaining} 秒后默认执行 '{default}') ", end='', flush=True)
                last_sec = remaining
            
            if msvcrt.kbhit():
                char = msvcrt.getch()
                try:
                    char = char.decode('utf-8')
                except:
                    continue
                if char.lower() in ('y', 'n'):
                    print(char)
                    return char.upper()
            time.sleep(0.1)
    else:
        ans = input(f"{prompt} [按 Y 重试 / N 清理退出，回车默认 {default}]: ")
        if not ans.strip():
            return default
        return 'Y' if 'y' in ans.lower() else 'N'

def build_sender_accounts(account_identifiers: str) -> list:
    """根据逗号分隔的账号标识列表，构建 sender_accounts 列表"""
    accounts = config_manager.load_accounts()
    result = []
    for ident in account_identifiers.split(','):
        ident = ident.strip()
        if not ident:
            continue
        for acc in accounts:
            if acc['name'] == ident or acc['email'] == ident:
                result.append({
                    'smtp_config': {
                        'smtp_server': acc['smtp_server'],
                        'smtp_port': acc['smtp_port'],
                        'password': acc['password'],
                        'use_ssl': acc['use_ssl']
                    },
                    'from_addr': acc['email']
                })
                break
        else:
            print(f"⚠️ 未找到账号 '{ident}'，已跳过。")
    return result

def console_compress_progress(msg: str, progress: float):
    """用于压缩环节的纯文本进度反馈"""
    if progress >= 0:
        print(f"[打包进度 {progress:.1f}%] {msg}")
    else:
        print(f"[打包中...] {msg}")

def console_send_progress(msg: str, current: int, total: int):
    """用于发信环节的纯文本进度反馈"""
    if total > 0:
        print(f"[发信 {current}/{total}] {msg}")
    else:
        print(msg)

def run_cli(args: argparse.Namespace):
    """CLI 的核心调度主循环"""
    # 先确定日志文件名（根据第一个文件命名）
    logger = None
    if getattr(args, 'files', None) and len(args.files) > 0:
        logger = setup_logging(args.files[0])
    elif args.resume:
        # 断点续传模式，从 active task 中获取文件名
        active_task = config_manager.load_active_task()
        if active_task and active_task.get("volume_paths"):
            first_vol = active_task["volume_paths"][0]
            logger = setup_logging(os.path.basename(first_vol))
    
    print("\n📦 文件分卷压缩 & 邮件发送工具 [CLI 命令行模式]")
    print("=" * 60)

    # 1. 拦截断点续传模式
    active_task = config_manager.load_active_task()
    task_data = None
    if args.resume:
        if not active_task:
            print("❌ 错误: 未发现任何被中断的未完成任务快照。")
            sys.exit(1)
        
        temp_dir = active_task.get("temp_dir", "")
        if not temp_dir or not os.path.exists(temp_dir):
            print("❌ 错误: 先前任务的临时文件目录已丢失。")
            config_manager.clear_active_task()
            sys.exit(1)
        
        print("✅ 检测到之前中断的任务，跳过压缩环节。")
        task_data = active_task
    else:
        if not args.files:
            print("❌ 参数缺失: 需通过 --files 指定文件路径。")
            sys.exit(1)
        if not args.to_addrs:
            print("❌ 参数缺失: 需通过 --to-addrs 指定收件人邮箱。")
            sys.exit(1)

    # 2. 构建多账号发件池
    sender_accounts = build_sender_accounts(args.account)
    if not sender_accounts:
        print(f"❌ 未找到任何有效的发件账号。请确保已在 GUI 中配置过对应账号。")
        sys.exit(1)
    
    to_addrs = [addr.strip() for addr in args.to_addrs.split(',')] if getattr(args, 'to_addrs', None) else task_data['recipients']
    send_limit = getattr(args, 'send_limit', 0)

    # 如果是新任务，先走压缩流程
    if not task_data:
        first_path = args.files[0]
        base_name = os.path.basename(first_path)
        archive_name = os.path.splitext(base_name)[0] if os.path.isfile(first_path) else base_name
        subject_prefix = args.subject if args.subject else archive_name
        
        print("\n⚙️ 阶段 1：正在压缩分卷...")
        temp_dir = tempfile.mkdtemp(prefix="file_mailer_cli_")
        
        volume_paths = compressor.compress_and_split(
            source_paths=args.files,
            output_dir=temp_dir,
            archive_name=archive_name,
            password=args.password,
            volume_size_mb=args.volume,
            progress_callback=console_compress_progress
        )
        print(f"✅ 压缩完成，共 {len(volume_paths)} 份分卷。")
        
        task_data = {
            "temp_dir": temp_dir,
            "subject_prefix": subject_prefix,
            "volume_paths": volume_paths,
            "recipients": to_addrs,
            "sent_indices": []
        }
        config_manager.save_active_task(task_data)

    # 发信主循环
    while True:
        sent_indices = task_data.get("sent_indices", [])
        total = len(task_data["volume_paths"])
        if len(sent_indices) >= total:
            print(f"\n🎉 全部 {total} 份分卷已发送完毕。")
            break

        addrs = [a['from_addr'] for a in sender_accounts]
        print(f"\n📧 阶段 2：启动 SMTP 发送...")
        print(f"   发件账号池: {', '.join(addrs)}")
        print(f"   收件人: {', '.join(to_addrs)}")
        if send_limit > 0:
            print(f"   每账号上限: {send_limit} 封")
        print(f"   已发送: {len(sent_indices)}/{total}")
        print("-" * 60)

        def volume_sent_cb(index: int, path: str):
            if index not in task_data["sent_indices"]:
                task_data["sent_indices"].append(index)
            config_manager.save_active_task(task_data)

        def on_account_switch(new_idx: int, new_addr: str):
            print(f"📌 已切换到发件账号: {new_addr}")
        
        try:
            mailer.send_volumes(
                sender_accounts=sender_accounts,
                to_addrs=task_data["recipients"],
                subject_prefix=task_data["subject_prefix"],
                volume_paths=task_data["volume_paths"],
                interval_seconds=args.interval,
                send_limit_per_account=send_limit,
                max_retries=args.max_retries,
                retry_wait=args.retry_wait,
                sent_indices=task_data["sent_indices"],
                progress_callback=console_send_progress,
                cancel_flag=None,
                volume_sent_callback=volume_sent_cb,
                account_switch_callback=on_account_switch
            )
            print("\n🎉 全部发送完成。")
            break
            
        except Exception as e:
            print(f"\n❌ 发送失败: {str(e)}")
            choice = timed_input(
                prompt="\n是否重试？[Y 重试 / N 退出]",
                timeout=args.prompt_timeout,
                default='N'
            )
            if choice == 'Y':
                print("\n[交互决议]收到：已充能重跑派信循环！")
                continue
            else:
                print("\n[交互决议]默许：已放弃重试准备善后。")
                break # 退出 while 到达最终清除逻辑

    # 凡是运行至此（不管成功发完或是惨遭滑铁卢）一律清库清快照！
    config_manager.clear_active_task()
    temp_dir = task_data.get("temp_dir", "")
    if temp_dir and os.path.exists(temp_dir):
        print(f"🧹 执行清理计划，正在擦除生成的巨无霸零碎分卷空间... ({temp_dir})")
        try:
            shutil.rmtree(temp_dir)
            print("✅ 擦除完毕。释放回本原 C 盘。")
        except:
            pass
            
    print("👋 CLI 运行终止。")
    if logger:
        # 先恢复原始 stdout/stderr，再关闭 logger，避免 Python 退出清理阶段异常
        sys.stdout = logger._original_stdout
        sys.stderr = logger._original_stderr
        logger.close()
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='FileMailer CLI - 分卷压缩邮件发送工具')
    parser.add_argument('--files', nargs='+', help='要发送的文件路径')
    parser.add_argument('--to-addrs', type=str, help='收件人邮箱，多个用逗号分隔')
    parser.add_argument('--account', type=str, required=True, help='发件人账号名称')
    parser.add_argument('--volume', type=int, default=20, help='分卷大小(MB)，默认20')
    parser.add_argument('--password', type=str, default='', help='压缩包密码')
    parser.add_argument('--interval', type=int, default=60, help='发送间隔(秒)，默认60')
    parser.add_argument('--send-limit', type=int, default=0, help='每个账号最大发件数，0=不限制，默认50')
    parser.add_argument('--max-retries', type=int, default=3, help='最大重试次数，默认3')
    parser.add_argument('--retry-wait', type=int, default=15, help='重试等待(秒)，默认15')
    parser.add_argument('--prompt-timeout', type=int, default=5, help='用户提示超时(秒)，默认5')
    parser.add_argument('--subject', type=str, default='', help='邮件主题前缀')
    parser.add_argument('--resume', action='store_true', help='断点续传')
    args = parser.parse_args()
    run_cli(args)

if __name__ == "__main__":
    main()