# -*- coding: utf-8 -*-
"""
GUI 主界面模块 - 文件分卷压缩 & 邮件自动发送工具。

架构说明:
    使用 ttkbootstrap 构建现代化的 tkinter GUI，界面分为 4 个功能区域:
        1. 文件选择区 - 浏览选择文件或文件夹
        2. 压缩设置区 - 设置密码和分卷大小
        3. 邮件设置区 - 选择发件账号、添加多个收件人
        4. 操作区 - 执行压缩发送 + 日志输出

    所有耗时操作在后台线程执行，避免 GUI 卡顿。
"""

import os
import threading
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List, Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

import compressor
import mailer
import config_manager


class AccountManagerDialog(ttk.Toplevel):
    """邮箱账号管理弹窗 - 支持新增、编辑、删除邮箱配置"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📧 管理邮箱账号")
        self.geometry("620x550")
        self.resizable(False, False)
        # 编辑模式状态：-1 表示新增，>=0 表示编辑对应索引的账号
        self._editing_index = -1
        self._build_ui()
        self._refresh_list()
        self.grab_set()  # 模态窗口

    def _build_ui(self):
        """构建账号管理界面"""
        # ====== 账号列表区 ======
        list_frame = ttk.LabelFrame(self, text="已保存的账号（双击编辑）")
        list_frame.pack(fill=X, padx=10, pady=(10, 5))

        self.account_list_var = tk.Variable()
        self.account_listbox = tk.Listbox(
            list_frame, listvariable=self.account_list_var, height=5, font=("Microsoft YaHei UI", 10),
            bg="#2b2b2b", fg="white", selectbackground="#375a7f"
        )
        self.account_listbox.pack(fill=X, padx=10, pady=(0, 5))
        # 双击加载账号到表单进行编辑
        self.account_listbox.bind("<Double-Button-1>", self._on_double_click)

        btn_row = ttk.Frame(list_frame)
        btn_row.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="🗑 删除选中", bootstyle=DANGER,
                   command=self._delete_selected).pack(side=RIGHT, padx=2)

        # ====== 新增/编辑区 ======
        self.form_frame = ttk.LabelFrame(self, text="添加新账号")
        self.form_frame.pack(fill=X, padx=10, pady=5)

        # 预设选择
        preset_row = ttk.Frame(self.form_frame)
        preset_row.pack(fill=X, pady=2)
        ttk.Label(preset_row, text="邮箱预设:", width=12).pack(side=LEFT)
        self.preset_var = ttk.StringVar(value="QQ邮箱")
        preset_combo = ttk.Combobox(
            preset_row, textvariable=self.preset_var,
            values=list(config_manager.SMTP_PRESETS.keys()),
            state="readonly", width=20
        )
        preset_combo.pack(side=LEFT, padx=5)
        preset_combo.bind("<<ComboboxSelected>>", self._on_preset_change)

        # 账号名称
        name_row = ttk.Frame(self.form_frame)
        name_row.pack(fill=X, pady=2)
        ttk.Label(name_row, text="账号名称:", width=12).pack(side=LEFT)
        self.name_var = ttk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var).pack(side=LEFT, fill=X, expand=True, padx=5)

        # 邮箱地址
        email_row = ttk.Frame(self.form_frame)
        email_row.pack(fill=X, pady=2)
        ttk.Label(email_row, text="邮箱地址:", width=12).pack(side=LEFT)
        self.email_var = ttk.StringVar()
        ttk.Entry(email_row, textvariable=self.email_var).pack(side=LEFT, fill=X, expand=True, padx=5)

        # SMTP 服务器
        server_row = ttk.Frame(self.form_frame)
        server_row.pack(fill=X, pady=2)
        ttk.Label(server_row, text="SMTP服务器:", width=12).pack(side=LEFT)
        self.server_var = ttk.StringVar(value="smtp.qq.com")
        ttk.Entry(server_row, textvariable=self.server_var).pack(side=LEFT, fill=X, expand=True, padx=5)

        # SMTP 端口
        port_row = ttk.Frame(self.form_frame)
        port_row.pack(fill=X, pady=2)
        ttk.Label(port_row, text="SMTP端口:", width=12).pack(side=LEFT)
        self.port_var = ttk.IntVar(value=465)
        ttk.Entry(port_row, textvariable=self.port_var, width=12).pack(side=LEFT, padx=5)
        self.ssl_var = ttk.BooleanVar(value=True)
        ttk.Checkbutton(port_row, text="使用 SSL", variable=self.ssl_var).pack(side=LEFT, padx=10)

        # 密码/授权码
        pwd_row = ttk.Frame(self.form_frame)
        pwd_row.pack(fill=X, pady=2)
        ttk.Label(pwd_row, text="授权码:", width=12).pack(side=LEFT)
        self.pwd_var = ttk.StringVar()
        self.pwd_entry = ttk.Entry(pwd_row, textvariable=self.pwd_var, show="●")
        self.pwd_entry.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.show_pwd_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(
            pwd_row, text="显示", variable=self.show_pwd_var,
            command=self._toggle_pwd_visibility
        ).pack(side=LEFT, padx=(5, 10))

        # 按钮行：保存 + 取消编辑
        btn_save_row = ttk.Frame(self.form_frame)
        btn_save_row.pack(fill=X, pady=(10, 0))

        self.save_btn = ttk.Button(
            btn_save_row, text="✅ 保存账号", bootstyle=SUCCESS,
            command=self._save_account
        )
        self.save_btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))

        self.cancel_edit_btn = ttk.Button(
            btn_save_row, text="取消编辑", bootstyle=(SECONDARY, OUTLINE),
            command=self._cancel_edit, state=DISABLED, width=10
        )
        self.cancel_edit_btn.pack(side=LEFT)

        # 初始化预设
        self._on_preset_change()

    def _on_preset_change(self, event=None):
        """当用户选择邮箱预设时，自动填充 SMTP 配置"""
        preset_name = self.preset_var.get()
        preset = config_manager.SMTP_PRESETS.get(preset_name, {})
        self.server_var.set(preset.get("smtp_server", ""))
        self.port_var.set(preset.get("smtp_port", 465))
        self.ssl_var.set(preset.get("use_ssl", True))

    def _toggle_pwd_visibility(self):
        """切换密码显示/隐藏"""
        self.pwd_entry.config(show="" if self.show_pwd_var.get() else "●")

    def _refresh_list(self):
        """刷新账号列表"""
        display_names = config_manager.get_account_display_list()
        self.account_list_var.set(display_names)

    def _on_double_click(self, event=None):
        """双击账号列表项，加载数据到表单进行编辑"""
        selection = self.account_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        accounts = config_manager.load_accounts()
        if index >= len(accounts):
            return

        account = accounts[index]
        self._editing_index = index

        # 填充表单
        self.name_var.set(account.get("name", ""))
        self.email_var.set(account.get("email", ""))
        self.server_var.set(account.get("smtp_server", ""))
        self.port_var.set(account.get("smtp_port", 465))
        self.ssl_var.set(account.get("use_ssl", True))
        # 密码留空，用户需要重新输入（安全起见不回显密码）
        self.pwd_var.set("")

        # 切换 UI 为编辑模式
        self.form_frame.config(text=f"编辑账号: {account.get('name', '')}")
        self.save_btn.config(text="✅ 更新账号")
        self.cancel_edit_btn.config(state=NORMAL)

    def _cancel_edit(self):
        """取消编辑，恢复为新增模式"""
        self._editing_index = -1
        self.name_var.set("")
        self.email_var.set("")
        self.pwd_var.set("")
        self.form_frame.config(text="添加新账号")
        self.save_btn.config(text="✅ 保存账号")
        self.cancel_edit_btn.config(state=DISABLED)
        self._on_preset_change()  # 重置 SMTP 配置

    def _save_account(self):
        """保存账号（新增或更新）"""
        name = self.name_var.get().strip()
        email = self.email_var.get().strip()
        server = self.server_var.get().strip()
        port = self.port_var.get()
        password = self.pwd_var.get()
        use_ssl = self.ssl_var.get()

        # 输入验证
        if not name:
            messagebox.showwarning("提示", "请输入账号名称", parent=self)
            return
        if not email or "@" not in email:
            messagebox.showwarning("提示", "请输入有效的邮箱地址", parent=self)
            return
        if not server:
            messagebox.showwarning("提示", "请输入 SMTP 服务器地址", parent=self)
            return

        if self._editing_index >= 0:
            # 编辑模式：更新已有账号
            accounts = config_manager.load_accounts()
            if self._editing_index < len(accounts):
                old_account = accounts[self._editing_index]
                # 如果密码为空，保留原密码（用户未修改）
                if password:
                    encrypted_pwd = config_manager.encrypt_password(password)
                else:
                    encrypted_pwd = old_account['password']

                updated = {
                    "name": name,
                    "email": email,
                    "smtp_server": server,
                    "smtp_port": port,
                    "password": encrypted_pwd,
                    "use_ssl": use_ssl
                }
                config_manager.update_account(self._editing_index, updated)
                messagebox.showinfo("成功", f"账号 '{name}' 已更新", parent=self)
        else:
            # 新增模式
            if not password:
                messagebox.showwarning("提示", "请输入授权码/密码", parent=self)
                return
            config_manager.add_account(name, email, server, port, password, use_ssl)
            messagebox.showinfo("成功", f"账号 '{name}' 已保存", parent=self)

        self._refresh_list()
        self._cancel_edit()  # 重置表单

    def _delete_selected(self):
        """删除选中的账号"""
        selection = self.account_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的账号", parent=self)
            return

        index = selection[0]
        if messagebox.askyesno("确认", "确定要删除选中的账号吗？", parent=self):
            config_manager.delete_account(index)
            self._refresh_list()
            # 如果删除的是正在编辑的账号，重置表单
            if index == self._editing_index:
                self._cancel_edit()


class ResumeTaskDialog(ttk.Toplevel):
    """断点续传确认弹窗 - 三按钮 + 10秒倒计时"""

    def __init__(self, parent, subject: str, sent: int, total: int):
        super().__init__(parent)
        self.title("断点续传")
        self.geometry("460x220")
        self.resizable(False, False)
        self.result = 'continue'  # 默认选项
        self._countdown = 10
        self._timer_id = None

        # 信息标签
        info = (
            f"检测到上次有未完成的发送任务 ({sent}/{total})。\n"
            f"标题: {subject}\n\n"
            f"请选择操作："
        )
        ttk.Label(
            self, text=info, font=("Microsoft YaHei UI", 10),
            wraplength=420, justify=LEFT
        ).pack(padx=15, pady=(15, 10))

        # 按钮区域
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=15, pady=(0, 15))

        self.btn_continue = ttk.Button(
            btn_frame, text=f"▶ 继续发送 ({self._countdown}s)",
            bootstyle=SUCCESS, command=self._on_continue
        )
        self.btn_continue.pack(fill=X, pady=2)

        self.btn_skip = ttk.Button(
            btn_frame, text="🔄 跳过当前账号，用下一个发送",
            bootstyle=INFO, command=self._on_skip
        )
        self.btn_skip.pack(fill=X, pady=2)

        self.btn_abandon = ttk.Button(
            btn_frame, text="✖ 放弃任务",
            bootstyle=DANGER, command=self._on_abandon
        )
        self.btn_abandon.pack(fill=X, pady=2)

        self.grab_set()
        self._tick()

    def _tick(self):
        """每秒更新倒计时"""
        if self._countdown <= 0:
            self._on_continue()
            return
        self.btn_continue.config(text=f"▶ 继续发送 ({self._countdown}s)")
        self._countdown -= 1
        self._timer_id = self.after(1000, self._tick)

    def _stop_timer(self):
        if self._timer_id:
            self.after_cancel(self._timer_id)
            self._timer_id = None

    def _on_continue(self):
        self._stop_timer()
        self.result = 'continue'
        self.destroy()

    def _on_skip(self):
        self._stop_timer()
        self.result = 'skip_account'
        self.destroy()

    def _on_abandon(self):
        self._stop_timer()
        self.result = 'abandon'
        self.destroy()


class FileMailerApp:
    """文件分卷压缩 & 邮件自动发送工具 - 主窗口"""

    def __init__(self):
        # 创建主窗口，使用 darkly 主题（暗色现代风格）
        self.root = ttk.Window(
            title="📦 文件分卷压缩 & 邮件发送工具",
            themename="darkly",
            size=(780, 900),
            resizable=(False, True)
        )
        self.root.place_window_center()

        # 状态变量
        self.selected_paths: List[str] = []   # 已选择的文件/文件夹路径
        self.recipients: List[str] = []       # 收件人列表
        self.sender_account_indices: List[int] = []  # 已添加的发件账号索引列表
        self._cancel_flag = False             # 取消标志
        self._running = False                 # 是否正在运行

        self._build_ui()
        self._refresh_accounts()
        self._load_settings()
        self.root.after(500, self._check_active_task)

    def _check_active_task(self):
        """检查是否有断点任务"""
        task = config_manager.load_active_task()
        if not task:
            return
            
        temp_dir = task.get("temp_dir", "")
        if not temp_dir or not os.path.exists(temp_dir):
            config_manager.clear_active_task()
            return
            
        sent = len(task.get("sent_indices", []))
        total = len(task.get("volume_paths", []))
        if sent >= total:
            config_manager.clear_active_task()
            return
            
        msg = f"检测到上次有未完成的发送任务 ({sent}/{total})。\n标题: {task.get('subject_prefix')}\n\n是否使用当前界面选中的账号继续发送剩下文件？\n(点“否”将放弃并清理该任务记录)"
        if messagebox.askyesno("断点续传", msg, parent=self.root):
            self._start_resume_task(task)
        else:
            config_manager.clear_active_task()

    def _build_sender_accounts_list(self) -> List[dict]:
        """根据界面上已添加的发件账号索引，构建 mailer 所需的 sender_accounts 列表"""
        accounts = config_manager.load_accounts()
        result = []
        for idx in self.sender_account_indices:
            if idx < len(accounts):
                acc = accounts[idx]
                result.append({
                    'smtp_config': {
                        'smtp_server': acc['smtp_server'],
                        'smtp_port': acc['smtp_port'],
                        'password': acc['password'],
                        'use_ssl': acc['use_ssl']
                    },
                    'from_addr': acc['email']
                })
        return result

    def _start_resume_task(self, task_data, skip_account=False):
        """开始执行续传任务"""
        if self._running:
            return
        if not self.sender_account_indices:
            messagebox.showwarning("提示", "请先在上方添加至少一个发件账号")
            return
            
        self._save_settings()
        self._running = True
        self._cancel_flag = False
        self.start_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)
        self.progress_var.set(0)
        
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=DISABLED)
        
        thread = threading.Thread(
            target=self._run_resume_task,
            args=(task_data, skip_account),
            daemon=True
        )
        thread.start()

    def _run_resume_task(self, task_data, skip_account=False):
        """执行断点续传子线程"""
        try:
            sender_accounts = self._build_sender_accounts_list()
            if not sender_accounts:
                self._log("❌ 错误：未找到有效的发件账号")
                return

            interval = self.interval_var.get()
            temp_dir = task_data["temp_dir"]
            subject_prefix = task_data["subject_prefix"]
            volume_paths = task_data["volume_paths"]
            recipients = task_data["recipients"]
            max_retries = self.max_retries_var.get()
            retry_wait = self.retry_wait_var.get()
            send_limit = self.send_limit_var.get()

            # 如果选择了"跳过当前账号"，则从第二个账号开始
            start_idx = 1 if (skip_account and len(sender_accounts) > 1) else 0

            self._log(f"🔄 启动断点续传任务...")
            addrs = [a['from_addr'] for a in sender_accounts]
            self._log(f"   发件账号池: {', '.join(addrs)}")
            self._log(f"   收件人: {', '.join(recipients)}")
            if start_idx > 0:
                self._log(f"   ⏭️ 已跳过第一个账号，从 {addrs[start_idx]} 开始")
            self._update_progress(50)

            def send_progress(msg, current, total):
                self._log(f"  {msg}")
                if total > 0:
                    pct = 50 + (current / total) * 50
                    self._update_progress(pct)

            def volume_sent_cb(index: int, path: str):
                if index not in task_data["sent_indices"]:
                    task_data["sent_indices"].append(index)
                config_manager.save_active_task(task_data)

            def on_account_switch(new_idx: int, new_addr: str):
                self._log(f"  📌 已切换到发件账号: {new_addr}")

            mailer.send_volumes(
                sender_accounts=sender_accounts,
                to_addrs=recipients,
                subject_prefix=subject_prefix,
                volume_paths=volume_paths,
                interval_seconds=interval,
                send_limit_per_account=send_limit,
                max_retries=max_retries,
                retry_wait=retry_wait,
                sent_indices=task_data.get("sent_indices", []),
                start_account_index=start_idx,
                progress_callback=send_progress,
                cancel_flag=lambda: self._cancel_flag,
                volume_sent_callback=volume_sent_cb,
                account_switch_callback=on_account_switch
            )

            if not self._cancel_flag:
                self._update_progress(100)
                self._log("\n🎉 断点续传全部完成！")
                config_manager.clear_active_task()
                self.root.after(0, lambda: self._ask_cleanup(temp_dir))

        except Exception as e:
            self._log(f"\n❌ 发生错误: {str(e)}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            self._running = False
            self.root.after(0, lambda: self.start_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.cancel_btn.config(state=DISABLED))

    def _build_ui(self):
        """构建完整的 GUI 界面"""
        # 主滚动容器
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=BOTH, expand=True)

        # ====== 标题 ======
        title_label = ttk.Label(
            main_frame,
            text="📦 文件分卷压缩 & 邮件自动发送",
            font=("Microsoft YaHei UI", 16, "bold"),
            bootstyle=INFO
        )
        title_label.pack(pady=(0, 15))

        # ====== ① 文件选择区 ======
        self._build_file_section(main_frame)

        # ====== ② 压缩设置区 ======
        self._build_compress_section(main_frame)

        # ====== ③ 邮件设置区 ======
        self._build_email_section(main_frame)

        # ====== ④ 操作区 ======
        self._build_action_section(main_frame)

    def _build_file_section(self, parent):
        """构建文件选择区域"""
        frame = ttk.LabelFrame(parent, text="📁 文件选择")
        frame.pack(fill=X, pady=5, padx=5)

        # 按钮行
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=X, padx=10, pady=(10, 5))

        ttk.Button(
            btn_row, text="📄 选择文件", bootstyle=PRIMARY,
            command=self._select_files
        ).pack(side=LEFT, padx=(0, 5))

        ttk.Button(
            btn_row, text="📁 选择文件夹", bootstyle=PRIMARY,
            command=self._select_folder
        ).pack(side=LEFT, padx=(0, 5))

        ttk.Button(
            btn_row, text="🗑 清空列表", bootstyle=SECONDARY,
            command=self._clear_files
        ).pack(side=RIGHT)

        # 已选文件列表
        self.file_listbox = tk.Listbox(
            frame, height=4, font=("Microsoft YaHei UI", 9),
            selectmode=tk.EXTENDED, bg="#2b2b2b", fg="white"
        )
        self.file_listbox.pack(fill=X, padx=10, pady=(0, 5))

        # 删除选中按钮
        ttk.Button(
            frame, text="移除选中项", bootstyle=(SECONDARY, OUTLINE),
            command=self._remove_selected_files
        ).pack(anchor=E, padx=10, pady=(0, 10))

    def _build_compress_section(self, parent):
        """构建压缩设置区域"""
        frame = ttk.LabelFrame(parent, text="🔒 压缩设置")
        frame.pack(fill=X, pady=5, padx=5)

        # 解压密码
        pwd_row = ttk.Frame(frame)
        pwd_row.pack(fill=X, padx=10, pady=(10, 2))
        ttk.Label(pwd_row, text="解压密码:", width=12).pack(side=LEFT)
        self.compress_pwd_var = ttk.StringVar()
        self.compress_pwd_entry = ttk.Entry(
            pwd_row, textvariable=self.compress_pwd_var, show="●"
        )
        self.compress_pwd_entry.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.show_compress_pwd_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(
            pwd_row, text="显示",
            variable=self.show_compress_pwd_var,
            command=lambda: self.compress_pwd_entry.config(
                show="" if self.show_compress_pwd_var.get() else "●"
            )
        ).pack(side=LEFT)

        # 分卷大小
        vol_row = ttk.Frame(frame)
        vol_row.pack(fill=X, padx=10, pady=(2, 10))
        ttk.Label(vol_row, text="分卷大小:", width=12).pack(side=LEFT)
        self.volume_var = ttk.StringVar(value="10")
        vol_combo = ttk.Combobox(
            vol_row, textvariable=self.volume_var,
            values=["5", "10", "15", "20", "25", "50"],
            width=8
        )
        vol_combo.pack(side=LEFT, padx=5)
        ttk.Label(vol_row, text="MB").pack(side=LEFT)

    def _build_email_section(self, parent):
        """构建邮件设置区域"""
        frame = ttk.LabelFrame(parent, text="✉️ 邮件设置")
        frame.pack(fill=X, pady=5, padx=5)

        # 发件账号选择行
        acc_row = ttk.Frame(frame)
        acc_row.pack(fill=X, padx=10, pady=(10, 2))
        ttk.Label(acc_row, text="发件账号:", width=12).pack(side=LEFT)
        self.account_var = ttk.StringVar()
        self.account_combo = ttk.Combobox(
            acc_row, textvariable=self.account_var,
            state="readonly", width=25
        )
        self.account_combo.pack(side=LEFT, padx=5)
        ttk.Button(
            acc_row, text="➕ 添加", bootstyle=SUCCESS,
            command=self._add_sender_account
        ).pack(side=LEFT, padx=(0, 5))
        ttk.Button(
            acc_row, text="⚙ 管理账号", bootstyle=(INFO, OUTLINE),
            command=self._manage_accounts
        ).pack(side=LEFT, padx=5)
        ttk.Button(
            acc_row, text="🔄 刷新", bootstyle=(SECONDARY, OUTLINE),
            command=self._refresh_accounts
        ).pack(side=LEFT)
        ttk.Button(
            acc_row, text="🗑 还原设置", bootstyle=(WARNING, OUTLINE),
            command=self._reset_settings
        ).pack(side=LEFT, padx=5)

        # 已添加的发件账号列表
        sender_list_row = ttk.Frame(frame)
        sender_list_row.pack(fill=X, padx=10, pady=2)
        ttk.Label(sender_list_row, text="", width=12).pack(side=LEFT)
        sender_container = ttk.Frame(sender_list_row)
        sender_container.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.sender_listbox = tk.Listbox(
            sender_container, height=3, font=("Microsoft YaHei UI", 9),
            bg="#2b2b2b", fg="white", selectbackground="#375a7f"
        )
        self.sender_listbox.pack(fill=X, side=LEFT, expand=True)
        ttk.Button(
            sender_list_row, text="🗑 移除", bootstyle=(DANGER, OUTLINE),
            command=self._remove_sender_account
        ).pack(side=LEFT, padx=(5, 0))

        # 收件人输入
        rcpt_input_row = ttk.Frame(frame)
        rcpt_input_row.pack(fill=X, padx=10, pady=2)
        ttk.Label(rcpt_input_row, text="收件人:", width=12).pack(side=LEFT)
        self.recipient_var = ttk.StringVar()
        self.recipient_combo = ttk.Combobox(
            rcpt_input_row, textvariable=self.recipient_var
        )
        self.recipient_combo.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.recipient_combo.bind("<Return>", lambda e: self._add_recipient())
        ttk.Button(
            rcpt_input_row, text="➕ 添加", bootstyle=SUCCESS,
            command=self._add_recipient
        ).pack(side=LEFT, padx=(0, 5))

        # 收件人列表
        rcpt_list_row = ttk.Frame(frame)
        rcpt_list_row.pack(fill=X, padx=10, pady=2)
        ttk.Label(rcpt_list_row, text="", width=12).pack(side=LEFT)
        list_container = ttk.Frame(rcpt_list_row)
        list_container.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.recipient_listbox = tk.Listbox(
            list_container, height=3, font=("Microsoft YaHei UI", 9),
            bg="#2b2b2b", fg="white", selectbackground="#375a7f"
        )
        self.recipient_listbox.pack(fill=X, side=LEFT, expand=True)
        ttk.Button(
            rcpt_list_row, text="🗑 移除", bootstyle=(DANGER, OUTLINE),
            command=self._remove_recipient
        ).pack(side=LEFT, padx=(5, 0))

        # 邮件主题
        subj_row = ttk.Frame(frame)
        subj_row.pack(fill=X, padx=10, pady=2)
        ttk.Label(subj_row, text="邮件主题:", width=12).pack(side=LEFT)
        self.subject_var = ttk.StringVar(value="")
        ttk.Entry(subj_row, textvariable=self.subject_var).pack(
            side=LEFT, fill=X, expand=True, padx=5
        )

        # 发送间隔与重试设置
        interval_row = ttk.Frame(frame)
        interval_row.pack(fill=X, padx=10, pady=2)
        ttk.Label(interval_row, text="发送间隔:", width=12).pack(side=LEFT)
        self.interval_var = ttk.IntVar(value=30)
        ttk.Spinbox(
            interval_row, textvariable=self.interval_var,
            from_=5, to=300, increment=5, width=5
        ).pack(side=LEFT, padx=5)
        ttk.Label(interval_row, text="秒").pack(side=LEFT, padx=(0, 15))
        ttk.Label(interval_row, text="失败重试:").pack(side=LEFT)
        self.max_retries_var = ttk.IntVar(value=10)
        ttk.Spinbox(
            interval_row, textvariable=self.max_retries_var,
            from_=0, to=999, increment=1, width=3
        ).pack(side=LEFT, padx=5)
        ttk.Label(interval_row, text="次").pack(side=LEFT, padx=(0, 15))
        ttk.Label(interval_row, text="重试等待:").pack(side=LEFT)
        self.retry_wait_var = ttk.IntVar(value=15)
        ttk.Spinbox(
            interval_row, textvariable=self.retry_wait_var,
            from_=5, to=300, increment=5, width=4
        ).pack(side=LEFT, padx=5)
        ttk.Label(interval_row, text="秒").pack(side=LEFT)

        # 每账号发件上限
        limit_row = ttk.Frame(frame)
        limit_row.pack(fill=X, padx=10, pady=(2, 10))
        ttk.Label(limit_row, text="每号上限:", width=12).pack(side=LEFT)
        self.send_limit_var = ttk.IntVar(value=0)
        ttk.Spinbox(
            limit_row, textvariable=self.send_limit_var,
            from_=0, to=999, increment=1, width=5
        ).pack(side=LEFT, padx=5)
        ttk.Label(limit_row, text="封 (0=不限制，达到上限自动切换下一个发件账号)").pack(side=LEFT)

    def _build_action_section(self, parent):
        """构建操作区域（按钮 + 进度条 + 日志）"""
        frame = ttk.LabelFrame(parent, text="🚀 操作")
        frame.pack(fill=BOTH, expand=True, pady=5, padx=5)

        # 按钮行
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=X, padx=10, pady=(10, 5))

        self.start_btn = ttk.Button(
            btn_row, text="▶ 开始压缩并发送", bootstyle=SUCCESS,
            command=self._start_task
        )
        self.start_btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))

        self.cancel_btn = ttk.Button(
            btn_row, text="⏹ 取消", bootstyle=DANGER,
            command=self._cancel_task, state=DISABLED, width=12
        )
        self.cancel_btn.pack(side=LEFT)

        # 进度条
        self.progress_var = ttk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            frame, variable=self.progress_var,
            bootstyle=SUCCESS, length=200
        )
        self.progress_bar.pack(fill=X, padx=10, pady=5)

        # 日志输出
        ttk.Label(frame, text="运行日志:").pack(anchor=W, padx=10)

        log_container = ttk.Frame(frame)
        log_container.pack(fill=BOTH, expand=True, padx=10, pady=(2, 10))

        scrollbar = ttk.Scrollbar(log_container)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.log_text = tk.Text(
            log_container, height=8, font=("Consolas", 9),
            state=DISABLED, wrap=tk.WORD,
            yscrollcommand=scrollbar.set
        )
        self.log_text.pack(fill=BOTH, expand=True, side=LEFT)
        scrollbar.config(command=self.log_text.yview)

    # ==================== 文件操作 ====================

    def _select_files(self):
        """打开文件选择对话框（支持多选）"""
        paths = filedialog.askopenfilenames(title="选择文件")
        if paths:
            for p in paths:
                if p not in self.selected_paths:
                    self.selected_paths.append(p)
            self._refresh_file_list()
            self._update_default_subject()

    def _select_folder(self):
        """打开文件夹选择对话框"""
        path = filedialog.askdirectory(title="选择文件夹")
        if path and path not in self.selected_paths:
            self.selected_paths.append(path)
            self._refresh_file_list()
            self._update_default_subject()

    def _clear_files(self):
        """清空已选文件列表"""
        self.selected_paths.clear()
        self._refresh_file_list()
        self.subject_var.set("")

    def _remove_selected_files(self):
        """移除列表中选中的文件"""
        selection = self.file_listbox.curselection()
        # 从后往前删除，避免索引偏移
        for idx in reversed(selection):
            if idx < len(self.selected_paths):
                self.selected_paths.pop(idx)
        self._refresh_file_list()

    def _refresh_file_list(self):
        """刷新文件列表显示"""
        self.file_listbox.delete(0, tk.END)
        for p in self.selected_paths:
            # 显示文件类型图标 + 路径
            icon = "📁" if os.path.isdir(p) else "📄"
            self.file_listbox.insert(tk.END, f"{icon} {p}")

    def _update_default_subject(self):
        """根据选中的文件/文件夹自动更新默认邮件主题"""
        if self.selected_paths:
            first_path = self.selected_paths[0]
            base_name = os.path.basename(first_path)
            name = os.path.splitext(base_name)[0] if os.path.isfile(first_path) else base_name
            self.subject_var.set(name)

    # ==================== 收件人操作 ====================

    def _add_recipient(self):
        """添加收件人到列表（支持分号分隔的多个地址）"""
        text = self.recipient_var.get().strip()
        if not text:
            return

        # 支持分号、逗号分隔多个地址
        addresses = [
            addr.strip() for addr in text.replace(",", ";").split(";")
            if addr.strip()
        ]

        for addr in addresses:
            if "@" not in addr:
                messagebox.showwarning("提示", f"邮箱地址格式无效: {addr}")
                continue
            if addr not in self.recipients:
                self.recipients.append(addr)

        self.recipient_var.set("")
        self._refresh_recipient_list()

    def _remove_recipient(self):
        """移除选中的收件人"""
        selection = self.recipient_listbox.curselection()
        for idx in reversed(selection):
            if idx < len(self.recipients):
                self.recipients.pop(idx)
        self._refresh_recipient_list()

    def _refresh_recipient_list(self):
        """刷新收件人列表显示"""
        self.recipient_listbox.delete(0, tk.END)
        for addr in self.recipients:
            self.recipient_listbox.insert(tk.END, addr)

    # ==================== 发件账号操作 ====================

    def _add_sender_account(self):
        """将下拉框中选择的账号添加到发件列表"""
        selected_index = self.account_combo.current()
        if selected_index < 0:
            messagebox.showwarning("提示", "请先从下拉框中选择一个账号")
            return
        if selected_index in self.sender_account_indices:
            messagebox.showinfo("提示", "该账号已在发件列表中")
            return
        self.sender_account_indices.append(selected_index)
        self._refresh_sender_listbox()

    def _remove_sender_account(self):
        """移除发件列表中选中的账号"""
        selection = self.sender_listbox.curselection()
        for idx in reversed(selection):
            if idx < len(self.sender_account_indices):
                self.sender_account_indices.pop(idx)
        self._refresh_sender_listbox()

    def _refresh_sender_listbox(self):
        """刷新发件账号列表显示"""
        self.sender_listbox.delete(0, tk.END)
        accounts = config_manager.load_accounts()
        for i, acc_idx in enumerate(self.sender_account_indices):
            if acc_idx < len(accounts):
                acc = accounts[acc_idx]
                display = f"[{i+1}] {acc['name']} ({acc['email']})"
                self.sender_listbox.insert(tk.END, display)

    def _manage_accounts(self):
        """打开账号管理弹窗"""
        dialog = AccountManagerDialog(self.root)
        self.root.wait_window(dialog)
        self._refresh_accounts()
        
    # ==================== 配置持久化 ====================

    def _load_settings(self):
        """加载已保存的设置并回充到 UI 控件"""
        settings = config_manager.load_settings()
        
        # 恢复解压密码与分卷设置
        if "compress_pwd" in settings:
            self.compress_pwd_var.set(settings["compress_pwd"])
        if "volume_size" in settings:
            self.volume_var.set(settings["volume_size"])
            
        if "interval" in settings:
            self.interval_var.set(settings["interval"])
        if "max_retries" in settings:
            self.max_retries_var.set(settings["max_retries"])
        if "retry_wait" in settings:
            self.retry_wait_var.set(settings["retry_wait"])
        if "send_limit" in settings:
            self.send_limit_var.set(settings["send_limit"])
            
        # 恢复已添加的发件账号列表
        saved_indices = settings.get("sender_account_indices", [])
        accounts = config_manager.load_accounts()
        self.sender_account_indices = [i for i in saved_indices if i < len(accounts)]
        self._refresh_sender_listbox()
            
        # 恢复收件人列表与下拉历史
        history = settings.get("recipient_history", [])
        self.recipient_combo['values'] = history
        
        last_recipients = settings.get("last_recipients", [])
        for rcpt in last_recipients:
            if rcpt not in self.recipients:
                self.recipients.append(rcpt)
        self._refresh_recipient_list()

    def _save_settings(self):
        """将当前核心设置保存到文件"""
        history = list(self.recipient_combo['values'])
        for r in self.recipients:
            if r not in history:
                history.append(r)
                
        settings = {
            "compress_pwd": self.compress_pwd_var.get(),
            "volume_size": self.volume_var.get(),
            "interval": self.interval_var.get(),
            "max_retries": self.max_retries_var.get(),
            "retry_wait": self.retry_wait_var.get(),
            "send_limit": self.send_limit_var.get(),
            "sender_account_indices": self.sender_account_indices,
            "last_recipients": self.recipients,
            "recipient_history": history[-20:]
        }
        config_manager.save_settings(settings)
        self.recipient_combo['values'] = settings["recipient_history"]
        
    def _reset_settings(self):
        """清除持久化配置，重置UI输入项"""
        if messagebox.askyesno("确认", "确定要清除历史设置、收件人列表和发件配置吗？\n（账户列表不会被删除）"):
            config_manager.clear_settings()
            self.compress_pwd_var.set("")
            self.volume_var.set("10")
            self.interval_var.set("30")
            self.max_retries_var.set(10)
            self.retry_wait_var.set(15)
            self.send_limit_var.set(0)
            self.recipients.clear()
            self.sender_account_indices.clear()
            self._refresh_recipient_list()
            self._refresh_sender_listbox()
            self.recipient_combo['values'] = []
            self.recipient_var.set("")
            self._log("🔄 设置和历史记录已重置。")

    def _refresh_accounts(self):
        """刷新发件账号下拉列表"""
        display_list = config_manager.get_account_display_list()
        self.account_combo['values'] = display_list
        if display_list and not self.account_var.get():
            self.account_var.set(display_list[0])

    # ==================== 日志操作 ====================

    def _log(self, message: str):
        """向日志文本框追加一行消息（线程安全）"""
        def _append():
            self.log_text.config(state=NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=DISABLED)

        self.root.after(0, _append)

    def _update_progress(self, value: float):
        """更新进度条（线程安全）"""
        self.root.after(0, lambda: self.progress_var.set(value))

    # ==================== 核心任务 ====================

    def _validate_inputs(self) -> bool:
        """验证用户输入是否完整有效"""
        if not self.selected_paths:
            messagebox.showwarning("提示", "请先选择要压缩的文件或文件夹")
            return False

        if not self.sender_account_indices:
            messagebox.showwarning("提示", "请先添加至少一个发件账号到发件列表")
            return False

        if not self.recipients:
            messagebox.showwarning("提示", "请至少添加一个收件人")
            return False

        try:
            vol_size = int(self.volume_var.get())
            if vol_size <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "分卷大小必须是正整数")
            return False

        return True

    def _start_task(self):
        """开始压缩并发送任务"""
        if self._running:
            return

        if not self._validate_inputs():
            return

        # 保存当前设置为下次默认值
        self._save_settings()

        # 冻结界面控件
        self._running = True
        self._cancel_flag = False
        self.start_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)
        self.progress_var.set(0)

        # 清空日志
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=DISABLED)

        # 在后台线程执行耗时操作
        thread = threading.Thread(target=self._run_task, daemon=True)
        thread.start()

    def _cancel_task(self):
        """取消当前任务"""
        self._cancel_flag = True
        self._log("⚠️ 正在取消...")

    def _run_task(self):
        """后台线程：执行压缩 + 发送的完整流程"""
        try:
            # ---- 第一步：构建发件账号池 ----
            sender_accounts = self._build_sender_accounts_list()
            if not sender_accounts:
                self._log("❌ 错误：未找到有效的发件账号")
                return

            password = self.compress_pwd_var.get() or None
            volume_size = int(self.volume_var.get())
            interval = self.interval_var.get()
            send_limit = self.send_limit_var.get()

            # ---- 第二步：压缩 ----
            self._log("📦 开始压缩文件...")
            self._update_progress(5)

            temp_dir = tempfile.mkdtemp(prefix="file_mailer_")

            first_path = self.selected_paths[0]
            base_name = os.path.basename(first_path)
            archive_name = os.path.splitext(base_name)[0] if os.path.isfile(first_path) else base_name

            subject_prefix = self.subject_var.get() or archive_name

            if self._cancel_flag:
                self._log("⚠️ 已取消。")
                return

            def compress_progress(msg, pct):
                self._log(f"  {msg}")
                if pct >= 0:
                    self._update_progress(5 + pct * 0.4)

            volume_paths = compressor.compress_and_split(
                source_paths=self.selected_paths,
                output_dir=temp_dir,
                archive_name=archive_name,
                password=password,
                volume_size_mb=volume_size,
                progress_callback=compress_progress
            )

            self._log(f"✅ 压缩完成，共 {len(volume_paths)} 个分卷文件")
            self._update_progress(50)

            if self._cancel_flag:
                self._log("⚠️ 已取消。")
                return

            # 建立断点任务快照
            task_data = {
                "temp_dir": temp_dir,
                "subject_prefix": subject_prefix,
                "volume_paths": volume_paths,
                "recipients": self.recipients,
                "sent_indices": []
            }
            config_manager.save_active_task(task_data)

            addrs = [a['from_addr'] for a in sender_accounts]
            self._log(f"\n📧 开始发送邮件...")
            self._log(f"   发件账号池: {', '.join(addrs)}")
            self._log(f"   收件人: {', '.join(self.recipients)}")
            self._log(f"   发送间隔: {interval} 秒")
            if send_limit > 0:
                self._log(f"   每账号上限: {send_limit} 封")
            self._log("")

            def send_progress(msg, current, total):
                self._log(f"  {msg}")
                if total > 0:
                    pct = 50 + (current / total) * 50
                    self._update_progress(pct)

            def volume_sent_cb(index: int, path: str):
                task_data["sent_indices"].append(index)
                config_manager.save_active_task(task_data)

            def on_account_switch(new_idx: int, new_addr: str):
                self._log(f"  📌 已切换到发件账号: {new_addr}")

            mailer.send_volumes(
                sender_accounts=sender_accounts,
                to_addrs=self.recipients,
                subject_prefix=subject_prefix,
                volume_paths=volume_paths,
                interval_seconds=interval,
                send_limit_per_account=send_limit,
                max_retries=self.max_retries_var.get(),
                retry_wait=self.retry_wait_var.get(),
                sent_indices=[],
                progress_callback=send_progress,
                cancel_flag=lambda: self._cancel_flag,
                volume_sent_callback=volume_sent_cb,
                account_switch_callback=on_account_switch
            )

            if not self._cancel_flag:
                self._update_progress(100)
                self._log("\n🎉 全部完成！所有分卷文件已发送。")
                config_manager.clear_active_task()

                # 提示用户是否清理临时文件
                self.root.after(0, lambda: self._ask_cleanup(temp_dir))

        except Exception as e:
            self._log(f"\n❌ 发生错误: {str(e)}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            # 恢复界面控件
            self._running = False
            self.root.after(0, lambda: self.start_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.cancel_btn.config(state=DISABLED))

    def _ask_cleanup(self, temp_dir: str):
        """询问用户是否清理临时分卷文件"""
        if messagebox.askyesno(
            "清理临时文件",
            f"发送完成！是否删除临时分卷文件？\n\n目录: {temp_dir}"
        ):
            import shutil
            try:
                shutil.rmtree(temp_dir)
                self._log("🧹 临时文件已清理。")
            except Exception as e:
                self._log(f"⚠️ 清理失败: {e}")
        else:
            self._log(f"📂 临时文件保留在: {temp_dir}")

    def run(self):
        """启动 GUI 主循环"""
        self.root.mainloop()
