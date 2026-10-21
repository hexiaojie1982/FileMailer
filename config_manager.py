# -*- coding: utf-8 -*-
"""
配置管理模块 - 管理邮箱账号的增删改查和加密存储。

架构说明:
    - 使用 Fernet 对称加密算法保护邮箱密码
    - 密钥文件 (.secret.key) 在首次运行时自动生成
    - 配置文件 (accounts.json) 中密码字段为加密密文

安全性审计:
    - 密码使用 AES-128-CBC (Fernet) 加密，非明文或可逆编码
    - 密钥文件独立保存，建议设置仅当前用户可读权限
    - 解密仅在需要使用密码时进行，不在内存中长期持有明文
"""

import os
import sys
import json
from typing import List, Dict, Optional
from cryptography.fernet import Fernet


# 常量：配置文件和密钥文件的默认路径（与脚本或exe同目录）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后的运行环境，此时 sys.executable 的路径即为 .exe 所在目录
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    # 正常 Python 源码运行环境
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ACCOUNTS_FILE = os.path.join(_BASE_DIR, "accounts.json")
_LEGACY_SECRET_KEY_FILE = os.path.join(_BASE_DIR, ".secret.key")
_SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")
_ACTIVE_TASK_FILE = os.path.join(_BASE_DIR, "active_task.json")

def _user_key_dir() -> str:
    """每用户密钥目录：与程序/项目目录分离存放（安全审计 A1）。

    密钥不再与密文 accounts.json 同目录，避免"读到项目目录即可全量解密"。
    Windows: %LOCALAPPDATA%\\FileMailer；POSIX: $XDG_CONFIG_HOME/filemailer
    或 ~/.config/filemailer。
    """
    if sys.platform == "win32":
        base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                or os.path.expanduser("~"))
        return os.path.join(base, "FileMailer")
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "filemailer")


_SECRET_KEY_FILE = os.path.join(_user_key_dir(), "secret.key")


# 预设的常见邮箱 SMTP 配置
SMTP_PRESETS = {
    "QQ邮箱": {
        "smtp_server": "smtp.qq.com",
        "smtp_port": 465,
        "use_ssl": True
    },
    "163邮箱": {
        "smtp_server": "smtp.163.com",
        "smtp_port": 465,
        "use_ssl": True
    },
    "126邮箱": {
        "smtp_server": "smtp.126.com",
        "smtp_port": 465,
        "use_ssl": True
    },
    "Outlook": {
        "smtp_server": "smtp.office365.com",
        "smtp_port": 587,
        "use_ssl": False  # Outlook 使用 STARTTLS
    },
    "Gmail": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "use_ssl": False  # Gmail 使用 STARTTLS
    },
    "自定义": {
        "smtp_server": "",
        "smtp_port": 465,
        "use_ssl": True
    }
}


def _write_key_file(path: str, key: bytes) -> None:
    """写入密钥文件，并尽量收紧权限（目录 0700 / 文件 0600，POSIX）。"""
    key_dir = os.path.dirname(path)
    if key_dir:
        os.makedirs(key_dir, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(key_dir, 0o700)
            except OSError:
                pass
    with open(path, 'wb') as f:
        f.write(key)
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _get_or_create_key() -> bytes:
    """
    获取或创建 Fernet 加密密钥。

    密钥存放在每用户目录（见 _user_key_dir），与 accounts.json 分离。
    若旧位置（程序目录 .secret.key）存在密钥，首次调用时迁移：
    写入新位置 → 读回逐字节校验 → 校验通过才删除旧文件；
    迁移任何一步失败都继续使用旧密钥，绝不中断解密。

    Returns:
        Fernet 密钥字节串
    """
    if os.path.exists(_SECRET_KEY_FILE):
        with open(_SECRET_KEY_FILE, 'rb') as f:
            return f.read()
    if os.path.exists(_LEGACY_SECRET_KEY_FILE):
        with open(_LEGACY_SECRET_KEY_FILE, 'rb') as f:
            key = f.read()
        try:
            _write_key_file(_SECRET_KEY_FILE, key)
            with open(_SECRET_KEY_FILE, 'rb') as f:
                if f.read() != key:
                    raise IOError("密钥迁移读回校验失败")
            os.remove(_LEGACY_SECRET_KEY_FILE)
        except OSError:
            pass  # 迁移失败：继续使用旧位置密钥，下次调用再试
        return key
    key = Fernet.generate_key()
    _write_key_file(_SECRET_KEY_FILE, key)
    return key


def _get_fernet() -> Fernet:
    """获取 Fernet 加密/解密实例"""
    key = _get_or_create_key()
    return Fernet(key)


def encrypt_password(plain_password: str) -> str:
    """
    加密密码。

    Args:
        plain_password: 明文密码

    Returns:
        加密后的密文字符串
    """
    fernet = _get_fernet()
    encrypted = fernet.encrypt(plain_password.encode('utf-8'))
    return encrypted.decode('utf-8')


def decrypt_password(encrypted_password: str) -> str:
    """
    解密密码。

    Args:
        encrypted_password: 加密密文

    Returns:
        解密后的明文密码
    """
    fernet = _get_fernet()
    decrypted = fernet.decrypt(encrypted_password.encode('utf-8'))
    return decrypted.decode('utf-8')


def load_accounts() -> List[Dict]:
    """
    从配置文件加载所有邮箱账号。

    Returns:
        账号字典列表，每个字典包含:
        - name: 账号名称
        - email: 邮箱地址
        - smtp_server: SMTP 服务器
        - smtp_port: SMTP 端口
        - password: 加密后的密码（密文）
        - use_ssl: 是否使用 SSL
    """
    if not os.path.exists(_ACCOUNTS_FILE):
        return []

    try:
        with open(_ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("accounts", [])
    except (json.JSONDecodeError, IOError):
        return []


def save_accounts(accounts: List[Dict]) -> None:
    """
    将账号列表保存到配置文件。

    Args:
        accounts: 账号字典列表
    """
    data = {"accounts": accounts}
    with open(_ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_account(
    name: str,
    email: str,
    smtp_server: str,
    smtp_port: int,
    password: str,
    use_ssl: bool = True
) -> None:
    """
    添加新邮箱账号（密码会被自动加密存储）。

    Args:
        name: 账号名称（如 "工作邮箱"）
        email: 邮箱地址
        smtp_server: SMTP 服务器地址
        smtp_port: SMTP 端口号
        password: 明文密码/授权码
        use_ssl: 是否使用 SSL 连接
    """
    accounts = load_accounts()
    account = {
        "name": name,
        "email": email,
        "smtp_server": smtp_server,
        "smtp_port": smtp_port,
        "password": encrypt_password(password),  # 加密存储
        "use_ssl": use_ssl
    }
    accounts.append(account)
    save_accounts(accounts)


def update_account(index: int, account_data: Dict) -> None:
    """
    更新指定索引的邮箱账号。

    Args:
        index: 账号在列表中的索引
        account_data: 更新后的账号数据（密码字段应为加密密文）
    """
    accounts = load_accounts()
    if 0 <= index < len(accounts):
        accounts[index] = account_data
        save_accounts(accounts)


def delete_account(index: int) -> None:
    """
    删除指定索引的邮箱账号。

    Args:
        index: 账号在列表中的索引
    """
    accounts = load_accounts()
    if 0 <= index < len(accounts):
        accounts.pop(index)
        save_accounts(accounts)


def get_account_display_list() -> List[str]:
    """
    获取账号显示名称列表（用于下拉框显示）。

    Returns:
        格式为 "名称 <邮箱>" 的字符串列表
    """
    accounts = load_accounts()
    return [f"{acc['name']} <{acc['email']}>" for acc in accounts]


# ==========================================
# 普通设置持久化 (Settings Persistence)
# ==========================================

def load_settings() -> Dict:
    """从配置文件加载普通设置"""
    if not os.path.exists(_SETTINGS_FILE):
        return {}
    try:
        with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_settings(settings: Dict) -> None:
    """将普通设置保存到配置文件"""
    with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def clear_settings() -> None:
    """清除普通设置"""
    if os.path.exists(_SETTINGS_FILE):
        os.remove(_SETTINGS_FILE)

# ==========================================
# 断点任务持久化 (Task Recovery)
# ==========================================

def load_active_task() -> Optional[Dict]:
    """尝试加载上次未完成的崩溃/断点任务记录"""
    if not os.path.exists(_ACTIVE_TASK_FILE):
        return None
    try:
        with open(_ACTIVE_TASK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

def save_active_task(task_data: Dict) -> None:
    """覆盖保存当前的发送任务状态"""
    with open(_ACTIVE_TASK_FILE, 'w', encoding='utf-8') as f:
        json.dump(task_data, f, ensure_ascii=False, indent=2)

def clear_active_task() -> None:
    """清除当前活动任务（任务100%发完或被用户主动废弃时调用）"""
    if os.path.exists(_ACTIVE_TASK_FILE):
        os.remove(_ACTIVE_TASK_FILE)
