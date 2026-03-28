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
    smtp_config: Dict,
    from_addr: str,
    to_addrs: List[str],
    subject_prefix: str,
    volume_paths: List[str],
    interval_seconds: int = 30,
    max_retries: int = 10,
    retry_wait: int = 15,
    sent_indices: Optional[List[int]] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    volume_sent_callback: Optional[Callable[[int, str], None]] = None
) -> None:
    """
    逐一发送分卷压缩文件，每封邮件间隔指定时间。

    Args:
        smtp_config: SMTP 配置字典
        from_addr: 发件人邮箱地址
        to_addrs: 收件人邮箱地址列表
        subject_prefix: 邮件主题前缀（自动追加 [1/N] 等编号）
        volume_paths: 分卷文件路径列表（按顺序）
        interval_seconds: 每封邮件之间的间隔（秒）
        max_retries: 网络故障最大重传次数
        retry_wait: 重连等待间隔（秒）
        sent_indices: 已经成功发送的分卷索引列表（用于断点续传跳过）
        progress_callback: 进度回调 (状态文本, 当前编号, 总数)
        cancel_flag: 取消标志函数，返回 True 时中止发送
        volume_sent_callback: 单个分卷发送成功后的回调 (索引, 文件路径)

    Raises:
        Exception: 发送过程中的任何错误
    """
    total = len(volume_paths)

    for i, vol_path in enumerate(volume_paths, start=1):
        # 如果这是个断点续传任务且此卷之前已发过，则直接跳过
        if sent_indices and i in sent_indices:
            if progress_callback:
                progress_callback(f"⏭️ 续传跳过已发分卷: [{i}/{total}]", i, total)
            continue

        # 检查是否取消
        if cancel_flag and cancel_flag():
            if progress_callback:
                progress_callback("已取消发送。", i, total)
            return

        # 方案：将 7z 分卷套一层标准的 zip 压缩包来发送。
        # 很多严格的邮箱（如 Gmail）通过读取二进制头直接阻断 .7z.001 等分卷或带密压缩包，
        # 套一层 zip 通常能骗过扫描器，且接收方可以用系统自带功能直接解压出 .001 文件。
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

        if progress_callback:
            progress_callback(f"正在发送 [{i}/{total}]: {zip_file_name}", i, total)

        # 发送套了 zip 壳的邮件（带智能重试防断机制）
        attempt = 0
        while True:
            try:
                send_email(smtp_config, from_addr, to_addrs, subject, body, zip_path, zip_file_name)
                break  # 成功，跳出重试
            except Exception as e:
                # 区分永久性错误和临时网络错误
                if isinstance(e, (smtplib.SMTPAuthenticationError, smtplib.SMTPDataError)):
                    # 密码错误或安全策略阻断，重试无用，直接向上抛出
                    raise e
                    
                attempt += 1
                if attempt > max_retries:
                    # 超过最大重试次数，宣告彻底失败
                    raise e
                
                if progress_callback:
                    progress_callback(f"⚠️ [网络重试 {attempt}/{max_retries}] 等待 {retry_wait}s 重连...", i, total)
                
                # 延时避让，并允许在此期间依然能秒切取消动作
                for _ in range(retry_wait):
                    if cancel_flag and cancel_flag():
                        if progress_callback:
                            progress_callback("已取消发送（重连中）。", i, total)
                        return
                    time.sleep(1)
        
        # 发送完毕后删除临时的 zip 文件节省空间
        try:
            os.remove(zip_path)
        except Exception:
            pass

        # 触发单卷完成回调（用于外层断点存盘记录）
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
            # 分段等待，以便检查取消标志
            for _ in range(interval_seconds):
                if cancel_flag and cancel_flag():
                    if progress_callback:
                        progress_callback("已取消发送。", i, total)
                    return
                time.sleep(1)

    if progress_callback:
        progress_callback("全部发送完成！", total, total)
