# FileMailer CLI Linux 部署指南

本指南以 Ubuntu 24.04 为例，说明如何在无用户图形界面（Headless）的服务器环境中部署并运行 FileMailer 命令行版本。

## 1. 系统环境初始化

连接至 Ubuntu 服务器后，更新软件源并安装基础 Python3 环境及必须的图形底层依赖。

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-tk git
```

> **注：** 即使使用 `--cli` 模式运行，代码架构中仍导入了 `tkinter` 相关模块，因此必须安装 `python3-tk` 以避免执行时报 `ModuleNotFoundError` 错误。

## 2. 获取源码与依赖安装

克隆仓库代码至服务器，并安装相关第三方 Python 包。推荐在 Ubuntu 24 平台通过虚拟环境隔离依赖，或使用 `--break-system-packages` 参数覆盖系统保护机制。

```bash
# 获取源码
git clone https://github.com/您的用户名/FileMailer.git
cd FileMailer

# 安装 Python 依赖包
pip3 install py7zr ttkbootstrap cryptography --break-system-packages
```

## 3. 跨端迁移账户凭证数据

出于安全设计，代码仓库中未包含且不该包含任何账户密码及加密主密钥。在 Linux 服务端执行 CLI 前，必须将本地（Windows）中曾经录入过发件账号配置的数据库及对应凭据迁移至新环境的根目录。

1. 在您的 Windows 主机项目根目录 (`D:\pythonProject\file_mailer`) 中定位以下两个文件：
   - `accounts.json`
   - `.secret.key`
2. 使用 `scp`、WinSCP 或 SFTP 等文件传输工具，将上述文件上传并放置到 Ubuntu 服务器中的 `FileMailer/` 目录下。

## 4. 执行 CLI 发送任务

当账户凭证配置就绪后，即可通过终端执行 `python3 main.py --cli` 调用脚本进行静默发送。

**基本发送命令示例：**
```bash
python3 main.py --cli \
  --files "/var/log/syslog" "/root/backup.tar.gz" \
  --to-addrs "admin@example.com,report@163.com" \
  --account "Gmail2" \
  --volume 50 \
  --prompt-timeout 20
```

**核心参数说明：**
* `--files`: 需打包发送的目标文件或路径序列。
* `--to-addrs`: 目标收件人，如存在多个地址需以半角逗号分隔。
* `--account`: 指定需调用的发信账号（此参数的值必须与本地 GUI 界面设置且存于 `accounts.json` 中的发件人别名保持绝对一致，例如 "Gmail2"）。
* `--volume`: 每份分卷压缩包的切片阀值，以 MB 为单位。
* `--prompt-timeout`: 当发生致命异常且耗尽预设重试次数后，留给终端的倒计时阻塞阀值（秒）。倒计时结束后将默认执行清理并退出进程。

## 5. 高级操作：脱机守护进程发送

若需对超大体积文件执行长时间的无感发送任务，为避免 SSH 会话意外断开导致任务失败，推荐结合 `nohup` 指令将程序放置后台运行。结合引擎自身的断连应对与自动重连机制，可保证任务被长期稳定执行，并通过记录重定向实现输出日志留存。

```bash
nohup python3 main.py --cli --files "/root/data/" --to-addrs "admin@163.com" --account "Gmail2" > mail_log.txt 2>&1 &
```
