# -*- coding: utf-8 -*-
import argparse
import os
import sys
import tempfile
import unittest
from unittest import mock

# 本组测试只验证邮件发送和 CLI 状态处理；开发环境未安装可选压缩依赖时，
# 使用替身让 cli_runner 可以导入，compress_and_split 会在测试中另行 mock。
try:
    import py7zr  # noqa: F401
except ModuleNotFoundError:
    sys.modules["py7zr"] = mock.MagicMock()

import cli_runner
import mailer


class _FakeSMTP:
    refused = {}

    def __init__(self, *args, **kwargs):
        self.login_args = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def login(self, from_addr, password):
        self.login_args = (from_addr, password)

    def sendmail(self, from_addr, to_addrs, message):
        return self.refused


class MailerP0Tests(unittest.TestCase):
    def test_send_email_raises_when_only_some_recipients_are_accepted(self):
        _FakeSMTP.refused = {
            "rejected@example.com": (550, b"mailbox unavailable")
        }
        config = {
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "password": "encrypted",
            "use_ssl": True,
        }

        with mock.patch.object(mailer, "decrypt_password", return_value="secret"), \
             mock.patch.object(mailer.smtplib, "SMTP_SSL", _FakeSMTP):
            with self.assertRaises(mailer.PartialRecipientsRefused) as raised:
                mailer.send_email(
                    config,
                    "sender@example.com",
                    ["accepted@example.com", "rejected@example.com"],
                    "subject",
                    "body",
                )

        self.assertEqual(
            raised.exception.accepted_recipients,
            ["accepted@example.com"],
        )
        self.assertEqual(
            raised.exception.refused_recipients,
            ["rejected@example.com"],
        )

    def test_send_volumes_retries_only_rejected_recipients(self):
        calls = []

        def fake_send_email(
            smtp_config, from_addr, to_addrs, subject, body,
            attachment_path=None, attachment_name=None,
        ):
            calls.append(list(to_addrs))
            if len(calls) == 1:
                raise mailer.PartialRecipientsRefused(
                    {"rejected@example.com": (550, b"rejected")},
                    to_addrs,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            volume_path = os.path.join(temp_dir, "archive.7z.001")
            with open(volume_path, "wb") as volume:
                volume.write(b"test volume")

            with mock.patch.object(mailer, "send_email", side_effect=fake_send_email), \
                 mock.patch.object(mailer.time, "sleep"):
                mailer.send_volumes(
                    sender_accounts=[{
                        "smtp_config": {},
                        "from_addr": "sender@example.com",
                    }],
                    to_addrs=["accepted@example.com", "rejected@example.com"],
                    subject_prefix="archive",
                    volume_paths=[volume_path],
                    interval_seconds=0,
                    max_retries=1,
                    retry_wait=0,
                )

        self.assertEqual(
            calls,
            [
                ["accepted@example.com", "rejected@example.com"],
                ["rejected@example.com"],
            ],
        )


class CliP0Tests(unittest.TestCase):
    def test_failed_task_keeps_snapshot_and_temp_files_and_returns_error(self):
        args = argparse.Namespace(
            resume=False,
            files=["source.txt"],
            to_addrs="recipient@example.com",
            account="sender",
            subject="subject",
            volume=10,
            password="",
            interval=0,
            send_limit=0,
            max_retries=0,
            retry_wait=0,
            prompt_timeout=0,
        )

        account = {
            "smtp_config": {},
            "from_addr": "sender@example.com",
        }

        with mock.patch.object(cli_runner.config_manager, "load_active_task", return_value=None), \
             mock.patch.object(cli_runner, "build_sender_accounts", return_value=[account]), \
             mock.patch.object(cli_runner, "setup_logging", return_value=None), \
             mock.patch.object(cli_runner.tempfile, "mkdtemp", return_value="task-temp"), \
             mock.patch.object(
                 cli_runner.compressor,
                 "compress_and_split",
                 return_value=["task-temp/archive.7z.001"],
             ), \
             mock.patch.object(cli_runner.config_manager, "save_active_task"), \
             mock.patch.object(
                 cli_runner.mailer,
                 "send_volumes",
                 side_effect=OSError("network unavailable"),
             ), \
             mock.patch.object(cli_runner, "timed_input", return_value="N"), \
             mock.patch.object(cli_runner.config_manager, "clear_active_task") as clear_task, \
             mock.patch.object(cli_runner.shutil, "rmtree") as remove_temp:
            with self.assertRaises(SystemExit) as raised:
                cli_runner.run_cli(args)

        self.assertEqual(raised.exception.code, 2)
        clear_task.assert_not_called()
        remove_temp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
