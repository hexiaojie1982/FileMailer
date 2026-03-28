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
import json
from typing import List, Dict, Optional
from cryptography.fernet import Fernet


# 常量：配置文件和密钥文件的默认路径（与脚本同目录）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ACCOUNTS_FILE = os.path.join(_BASE_DIR, "accounts.json")
_SECRET_KEY_FILE = os.path.join(_BASE_DIR, ".secret.key")
_SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")

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


def _get_or_create_key() -> bytes:
    """
    获取或创建 Fernet 加密密钥。

    首次调用时自动生成密钥并保存到 .secret.key 文件。
    后续调用直接从文件读取。

    Returns:
        Fernet 密钥字节串
    """
    if os.path.exists(_SECRET_KEY_FILE):
        with open(_SECRET_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        # 生成新的 Fernet 密钥
        key = Fernet.generate_key()
        with open(_SECRET_KEY_FILE, 'wb') as f:
            f.write(key)
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
