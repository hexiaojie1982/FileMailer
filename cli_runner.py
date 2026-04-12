# -*- coding: utf-8 -*-
import os
import sys
import time
import argparse
import tempfile
import shutil
import traceback
from typing import List

import config_manager
import compressor
import mailer

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

def get_account_config(account_identifier: str) -> dict:
    """根据别名或邮箱在一堆账号中找到对应的 SMTP 配置"""
    accounts = config_manager.load_accounts()
    for acc in accounts:
        if acc['name'] == account_identifier or acc['email'] == account_identifier:
            return {
                'smtp_server': acc['smtp_server'],
                'smtp_port': acc['smtp_port'],
                'password': acc['password'], # mailer 会自动解密
                'use_ssl': acc['use_ssl'],
                'from_addr': acc['email']
            }
    return {}

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
    print("\n📦 文件分卷压缩 & 邮件发送工具 [CLI 命令行模式]")
    print("=" * 60)

    # 1. 拦截断点续传模式
    active_task = config_manager.load_active_task()
    task_data = None
    if args.resume:
        if not active_task:
            print("❌ 错误: 未发现任何被中断的未完成任务快照。请勿使用 --resume 参数，传路径以开启新任务。")
            sys.exit(1)
        
        temp_dir = active_task.get("temp_dir", "")
        if not temp_dir or not os.path.exists(temp_dir):
            print("❌ 错误: 先前任务的临时文件目录已丢失。无法断点重连。")
            config_manager.clear_active_task()
            sys.exit(1)
        
        print("✅ 检测到之前中断的任务！启用短连接跳过压缩环节。")
        task_data = active_task
    else:
        # 新任务参数校验
        if not args.files:
            print("❌ 参数丢失: 当你不使用 --resume 续传时，需通过 --files 指定所需的文件路径。")
            sys.exit(1)
        if not args.to_addrs:
            print("❌ 参数丢失: 必须通过 --to 指定至少一位收件人邮箱，多可用逗号隔开。")
            sys.exit(1)

    # 2. 账号合法性校验
    account_config = get_account_config(args.account)
    if not account_config:
        print(f"❌ 查无此发件人账号快照: '{args.account}'。请确保您已在 GUI 下添加并成功保存过该账号的名字或邮箱！")
        sys.exit(1)
    
    from_addr = account_config.pop('from_addr')
    to_addrs = [addr.strip() for addr in args.to_addrs.split(',')] if getattr(args, 'to_addrs', None) else task_data['recipients']

    # 如果是新任务，先走压缩流程
    if not task_data:
        first_path = args.files[0]
        base_name = os.path.basename(first_path)
        archive_name = os.path.splitext(base_name)[0] if os.path.isfile(first_path) else base_name
        subject_prefix = args.subject if args.subject else archive_name
        
        print("\n⚙️ 阶段 1：正在对指定资源进行压模分卷打包...")
        temp_dir = tempfile.mkdtemp(prefix="file_mailer_cli_")
        
        volume_paths = compressor.compress_and_split(
            source_paths=args.files,
            output_dir=temp_dir,
            archive_name=archive_name,
            password=args.password,
            volume_size_mb=args.volume,
            progress_callback=console_compress_progress
        )
        print(f"✅ 压缩完成，共切分成 {len(volume_paths)} 份分卷。")
        
        # 立刻写入数据库快照图，备日后断连之需
        task_data = {
            "temp_dir": temp_dir,
            "subject_prefix": subject_prefix,
            "volume_paths": volume_paths,
            "recipients": to_addrs,
            "sent_indices": []
        }
        config_manager.save_active_task(task_data)

    # 进入发信死亡循环 (能被 Y/N 交互掌控)
    while True:
        sent_indices = task_data.get("sent_indices", [])
        total = len(task_data["volume_paths"])
        if len(sent_indices) >= total:
            print(f"\n🎉 恭喜！{total} 件分卷早已全部派发完毕，无需多此一举。")
            break

        print("\n📧 阶段 2：启动 SMTP 分页集群派送...")
        print(f"   [发 件 人] -> {from_addr}")
        print(f"   [发 送 至] -> {', '.join(to_addrs)}")
        print(f"   [进度跳过] -> {'、'.join(map(str, sent_indices))} 号分卷 (历史已补)")
        print("-" * 60)

        # 回调勾子：存活并更新数据库快照
        def volume_sent_cb(index: int, path: str):
            if index not in task_data["sent_indices"]:
                task_data["sent_indices"].append(index)
            config_manager.save_active_task(task_data)
        
        try:
            mailer.send_volumes(
                smtp_config=account_config,
                from_addr=from_addr,
                to_addrs=task_data["recipients"],
                subject_prefix=task_data["subject_prefix"],
                volume_paths=task_data["volume_paths"],
                interval_seconds=args.interval,
                max_retries=args.max_retries,
                retry_wait=args.retry_wait,
                sent_indices=task_data["sent_indices"],
                progress_callback=console_send_progress,
                cancel_flag=None, # CLI 不接收手动终止快捷键阻断
                volume_sent_callback=volume_sent_cb
            )
            print("\n🎉 全部完成！所有分卷已按批次稳定发送出去。")
            break # 成功则顺延爬出 while 循环
            
        except Exception as e:
            print(f"\n❌ 致命抛出抛压网络异常或风控：{str(e)}")
            choice = timed_input(
                prompt="\n👉 您配置的最高重连数已耗尽，或是遭遇了极其苛刻的安全阻断。\n是否强行再重试一遍发病节点包？[Y 试 / N 删]",
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
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='FileMailer CLI - 分卷压缩邮件发送工具')
    parser.add_argument('--files', nargs='+', help='要发送的文件路径')
    parser.add_argument('--to-addrs', type=str, help='收件人邮箱，多个用逗号分隔')
    parser.add_argument('--account', type=str, required=True, help='发件人账号名称')
    parser.add_argument('--volume', type=int, default=20, help='分卷大小(MB)，默认20')
    parser.add_argument('--password', type=str, default='', help='压缩包密码')
    parser.add_argument('--interval', type=int, default=60, help='发送间隔(秒)，默认60')
    parser.add_argument('--max-retries', type=int, default=3, help='最大重试次数，默认3')
    parser.add_argument('--retry-wait', type=int, default=15, help='重试等待(秒)，默认15')
    parser.add_argument('--prompt-timeout', type=int, default=5, help='用户提示超时(秒)，默认5')
    parser.add_argument('--subject', type=str, default='', help='邮件主题前缀')
    parser.add_argument('--resume', action='store_true', help='断点续传')
    args = parser.parse_args()
    run_cli(args)

if __name__ == "__main__":
    main()