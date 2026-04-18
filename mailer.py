# -*- coding: utf-8 -*-
"""
邮件发送模块 - 负责构建带附件的邮件并通过 SMTP 发送。

架构说明:
    - 使用 smtplib 标准库进行 SMTP 连接
    - 支持 SSL（端口 465）和 STARTTLS（端口 587）两种安全连接方式
    - 支持多个收件人

安全性:
    - 所有 SMTP 连接均通过加密通道（SSL/TLS）
    - 密码仅在建立连接时使用，不写入日志
"""

import os
import smtplib
import time
import zipfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Dict, Optional, Callable

from config_manager import decrypt_password


def send_email(
    smtp_config: Dict,
    from_addr: str,
    to_addrs: List[str],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    attachment_name: Optional[str] = None
) -> None:
    """
    发送一封带附件的邮件。

    Args:
        smtp_config: SMTP 配置字典，包含:
            - smtp_server: SMTP 服务器地址
            - smtp_port: SMTP 端口号
            - password: 加密后的密码（Fernet 密文）
            - use_ssl: 是否使用 SSL 连接
        from_addr: 发件人邮箱地址
        to_addrs: 收件人邮箱地址列表（支持多个）
        subject: 邮件主题
        body: 邮件正文
        attachment_path: 附件文件路径（可选）

    Raises:
        smtplib.SMTPException: SMTP 连接或发送失败
        Exception: 其他错误
    """
    # 构建 MIME 邮件
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = "; ".join(to_addrs)  # 多个收件人用分号分隔
    msg['Subject'] = subject

    # 添加邮件正文
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # 添加附件
    if attachment_path and os.path.isfile(attachment_path):
        file_name = attachment_name if attachment_name else os.path.basename(attachment_path)
        with open(attachment_path, 'rb') as f:
            mime_base = MIMEBase('application', 'octet-stream')
            mime_base.set_payload(f.read())

        # Base64 编码附件内容
        encoders.encode_base64(mime_base)
        mime_base.add_header(
            'Content-Disposition',
            'attachment',
            filename=('utf-8', '', file_name)  # 支持中文文件名
        )
        msg.attach(mime_base)

    # 解密 SMTP 密码
    plain_password = decrypt_password(smtp_config['password'])

    # 建立 SMTP 连接并发送
    server = smtp_config['smtp_server']
    port = smtp_config['smtp_port']
    use_ssl = smtp_config.get('use_ssl', True)

    # 智能判断连接方式：端口 465 使用 SSL 直连，端口 587 使用 STARTTLS
    # 优先根据端口号判断，避免用户配置错误导致连接失败
    if port == 465:
        # SSL 直连（端口 465）
        with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
            smtp.login(from_addr, plain_password)
            smtp.sendmail(from_addr, to_addrs, msg.as_string())
    elif port == 587 or not use_ssl:
        # STARTTLS（端口 587 或明确标记为非 SSL）
        with smtplib.SMTP(server, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(from_addr, plain_password)
            smtp.sendmail(from_addr, to_addrs, msg.as_string())
    else:
        # 其他端口按 use_ssl 标志决定
        if use_ssl:
            with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
                smtp.login(from_addr, plain_password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(server, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(from_addr, plain_password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())


def send_volumes(
    sender_accounts: List[Dict],
    to_addrs: List[str],
    subject_prefix: str,
    volume_paths: List[str],
    interval_seconds: int = 30,
    send_limit_per_account: int = 0,
    max_retries: int = 10,
    retry_wait: int = 15,
    sent_indices: Optional[List[int]] = None,
    start_account_index: int = 0,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    volume_sent_callback: Optional[Callable[[int, str], None]] = None,
    account_switch_callback: Optional[Callable[[int, str], None]] = None
) -> None:
    """
    逐一发送分卷压缩文件，支持多账号轮询与自动切换。

    Args:
        sender_accounts: 发件账号配置列表，每项为 dict:
            {'smtp_config': {...}, 'from_addr': '...'}
        to_addrs: 收件人邮箱地址列表
        subject_prefix: 邮件主题前缀（自动追加 [1/N] 等编号）
        volume_paths: 分卷文件路径列表（按顺序）
        interval_seconds: 每封邮件之间的间隔（秒）
        send_limit_per_account: 每个账号最大发件数，0 = 不限制
        max_retries: 网络故障最大重传次数
        retry_wait: 重连等待间隔（秒）
        sent_indices: 已经成功发送的分卷索引列表（用于断点续传跳过）
        start_account_index: 起始账号索引（用于断点续传时跳过前面的账号）
        progress_callback: 进度回调 (状态文本, 当前编号, 总数)
        cancel_flag: 取消标志函数，返回 True 时中止发送
        volume_sent_callback: 单个分卷发送成功后的回调 (索引, 文件路径)
        account_switch_callback: 账号切换时的回调 (新索引, 新邮箱地址)

    Raises:
        Exception: 所有账号均无法完成发送时抛出
    """
    total = len(volume_paths)
    num_accounts = len(sender_accounts)

    # 当前账号指针与该账号已发送计数
    cur_acc_idx = start_account_index % num_accounts
    cur_acc_sent = 0

    def _get_current():
        """获取当前账号的 smtp_config 和 from_addr"""
        acc = sender_accounts[cur_acc_idx]
        return acc['smtp_config'], acc['from_addr']

    def _switch_account(reason: str):
        """切换到下一个账号，返回是否成功（循环一圈则失败）"""
        nonlocal cur_acc_idx, cur_acc_sent
        old_idx = cur_acc_idx
        cur_acc_idx = (cur_acc_idx + 1) % num_accounts
        cur_acc_sent = 0
        new_addr = sender_accounts[cur_acc_idx]['from_addr']
        if account_switch_callback:
            account_switch_callback(cur_acc_idx, new_addr)
        if progress_callback:
            progress_callback(f"🔄 {reason}，切换发件账号 → {new_addr}", 0, total)
        # 返回是否已经绕了一整圈（无可用账号）
        return cur_acc_idx != old_idx or num_accounts == 1

    for i, vol_path in enumerate(volume_paths, start=1):
        # 断点续传跳过已发分卷
        if sent_indices and i in sent_indices:
            if progress_callback:
                progress_callback(f"⏭️ 续传跳过已发分卷: [{i}/{total}]", i, total)
            continue

        # 检查是否取消
        if cancel_flag and cancel_flag():
            if progress_callback:
                progress_callback("已取消发送。", i, total)
            return

        # 检查当前账号是否达到发件上限（send_limit > 0 时生效）
        if send_limit_per_account > 0 and cur_acc_sent >= send_limit_per_account:
            _switch_account(f"账号已达发件上限 ({send_limit_per_account}封)")

        # 准备 zip 封装
        file_name = os.path.basename(vol_path)
        zip_path = f"{vol_path}.zip"
        zip_file_name = f"{file_name}.zip"

        if progress_callback:
            progress_callback(f"正在进行 zip 封装防拦截: {zip_file_name}", i, total)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(vol_path, arcname=file_name)

        subject = f"{subject_prefix} [{i}/{total}]"
        body = (
            f"这是第 {i}/{total} 个分卷文件: {file_name}\n\n"
            f"【重要提示】\n"
            f"为防止邮件服务器的安全审查拦截拆分压缩包，附件已被自动套上一层常规的 .zip 外壳。\n"
            f"📥 下载本邮件附带的 {zip_file_name} 后，请先直接双击解压它，您会得到真实的 '{file_name}'。\n"
            f"请将所有解压出来的分卷（.001, .002...）放于同一文件夹内，最后使用 7-Zip 打开 .001 即可完整解压全部文件。"
        )

        smtp_config, from_addr = _get_current()

        if progress_callback:
            progress_callback(f"正在发送 [{i}/{total}] (账号: {from_addr}): {zip_file_name}", i, total)

        # 带智能重试与账号自动切换的发送逻辑
        attempt = 0
        tried_accounts = 0  # 已尝试过的账号数（用于判定全部耗尽）
        while True:
            try:
                send_email(smtp_config, from_addr, to_addrs, subject, body, zip_path, zip_file_name)
                break  # 成功
            except Exception as e:
                # 认证错误或数据策略错误 → 该账号不可用，直接切换
                if isinstance(e, (smtplib.SMTPAuthenticationError, smtplib.SMTPDataError)):
                    tried_accounts += 1
                    if tried_accounts >= num_accounts:
                        raise e  # 所有账号都不可用
                    if progress_callback:
                        progress_callback(f"⛔ 账号 {from_addr} 认证/策略错误: {e}", i, total)
                    _switch_account("账号认证失败")
                    smtp_config, from_addr = _get_current()
                    attempt = 0
                    continue

                # 网络类临时错误 → 重试
                attempt += 1
                if attempt > max_retries:
                    # 当前账号重试耗尽，切换下一个账号
                    tried_accounts += 1
                    if tried_accounts >= num_accounts:
                        raise e  # 所有账号均重试耗尽
                    _switch_account(f"重试 {max_retries} 次仍失败")
                    smtp_config, from_addr = _get_current()
                    attempt = 0
                    continue

                if progress_callback:
                    progress_callback(f"⚠️ [网络重试 {attempt}/{max_retries}] 等待 {retry_wait}s 重连...", i, total)

                # 延时避让，可被取消中断
                for _ in range(retry_wait):
                    if cancel_flag and cancel_flag():
                        if progress_callback:
                            progress_callback("已取消发送（重连中）。", i, total)
                        return
                    time.sleep(1)

        # 发送完毕后删除临时 zip
        try:
            os.remove(zip_path)
        except Exception:
            pass

        # 更新当前账号已发计数
        cur_acc_sent += 1

        # 触发单卷完成回调（断点存盘）
        if volume_sent_callback:
            volume_sent_callback(i, vol_path)

        if progress_callback:
            progress_callback(f"已发送 [{i}/{total}]: {file_name}", i, total)

        # 等待间隔（最后一封不等待）
        if i < total:
            if progress_callback:
                progress_callback(
                    f"等待 {interval_seconds} 秒后发送下一封...",
                    i, total
                )
            for _ in range(interval_seconds):
                if cancel_flag and cancel_flag():
                    if progress_callback:
                        progress_callback("已取消发送。", i, total)
                    return
                time.sleep(1)

    if progress_callback:
        progress_callback("全部发送完成！", total, total)
