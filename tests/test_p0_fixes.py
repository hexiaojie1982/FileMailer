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
            attachment_path=None, attachment_name=None, message_id=None,
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


class _FakeIMAP:
    """imaplib.IMAP4_SSL 的替身：模拟已发送文件夹内容。"""

    instances = []

    def __init__(self, *args, **kwargs):
        _FakeIMAP.instances.append(self)
        self.logged_in = None
        self.selected = None
        self.list_result = ('OK', [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'])
        self.search_uids = b'1'
        self.fetch_headers = {}

    def login(self, user, password):
        self.logged_in = (user, password)

    def list(self):
        if isinstance(self.list_result, Exception):
            raise self.list_result
        return self.list_result

    def select(self, box, readonly=False):
        self.selected = box
        return ('OK', [b'1'])

    def uid(self, cmd, *args):
        if cmd == 'search':
            return ('OK', [self.search_uids])
        if cmd == 'fetch':
            uid = args[0]
            key = uid if isinstance(uid, bytes) else str(uid).encode()
            raw = self.fetch_headers.get(
                key, b'Subject: unrelated\r\nDate: Thu, 01 Jan 1970 00:00:00 +0000\r\n\r\n')
            return ('OK', [(b'1 (BODY[HEADER.FIELDS (SUBJECT DATE)] {%d})' % len(raw), raw)])
        raise AssertionError(cmd)

    def logout(self):
        pass


def _recent_date_header():
    import datetime
    from email.utils import format_datetime
    return format_datetime(datetime.datetime.now(datetime.timezone.utc))


class MailerImapDedupTests(unittest.TestCase):
    def setUp(self):
        _FakeIMAP.instances.clear()

    def _gmail_config(self):
        return {
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "password": "encrypted",
            "use_ssl": False,
        }

    def test_imap_hit_within_window_returns_true(self):
        imap = _FakeIMAP()
        imap.fetch_headers = {
            b'1': ('Subject: lumen-0.3.80 [1/1]\r\nDate: %s\r\n\r\n'
                   % _recent_date_header()).encode('utf-8'),
        }
        with mock.patch.object(mailer, "decrypt_password", return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL", return_value=imap):
            hit = mailer._imap_sent_has_subject(
                self._gmail_config(), "sender@gmail.com", "lumen-0.3.80 [1/1]")
        self.assertTrue(hit)
        self.assertEqual(imap.logged_in[0], "sender@gmail.com")
        self.assertEqual(imap.selected, '"[Gmail]/Sent Mail"')

    def test_imap_miss_returns_false(self):
        imap = _FakeIMAP()  # 默认 fetch 返回不相关主题
        with mock.patch.object(mailer, "decrypt_password", return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL", return_value=imap):
            hit = mailer._imap_sent_has_subject(
                self._gmail_config(), "sender@gmail.com", "lumen-0.3.80 [1/1]")
        self.assertFalse(hit)

    def test_imap_error_is_fail_open(self):
        imap = _FakeIMAP()
        imap.list_result = OSError("imap unreachable")
        with mock.patch.object(mailer, "decrypt_password", return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL", return_value=imap):
            hit = mailer._imap_sent_has_subject(
                self._gmail_config(), "sender@gmail.com", "lumen-0.3.80 [1/1]")
        self.assertFalse(hit)

    def test_non_gmail_skips_imap(self):
        config = dict(self._gmail_config(), smtp_server="smtp.example.com")
        with mock.patch.object(mailer.imaplib, "IMAP4_SSL") as imap_cls:
            hit = mailer._imap_sent_has_subject(
                config, "sender@example.com", "subject")
        self.assertFalse(hit)
        imap_cls.assert_not_called()

    def test_send_volumes_skips_retry_when_imap_hit(self):
        calls = []

        def fake_send_email(
            smtp_config, from_addr, to_addrs, subject, body,
            attachment_path=None, attachment_name=None, message_id=None,
        ):
            calls.append(subject)
            raise TimeoutError("read timed out")  # 服务器已收下但回包超时

        sent = []
        with tempfile.TemporaryDirectory() as temp_dir:
            volume_path = os.path.join(temp_dir, "archive.7z.001")
            with open(volume_path, "wb") as volume:
                volume.write(b"test volume")

            with mock.patch.object(mailer, "send_email", side_effect=fake_send_email), \
                 mock.patch.object(mailer, "_imap_sent_has_subject", return_value=True), \
                 mock.patch.object(mailer.time, "sleep"):
                mailer.send_volumes(
                    sender_accounts=[{
                        "smtp_config": self._gmail_config(),
                        "from_addr": "sender@gmail.com",
                    }],
                    to_addrs=["recipient@example.com"],
                    subject_prefix="archive",
                    volume_paths=[volume_path],
                    interval_seconds=0,
                    max_retries=3,
                    retry_wait=0,
                    volume_sent_callback=lambda i, p: sent.append(i),
                )

        # 只尝试发送一次：第二次尝试前 IMAP 命中，直接视为成功，不再重发
        self.assertEqual(calls, ["archive [1/1]"])
        self.assertEqual(sent, [1])


if __name__ == "__main__":
    unittest.main()
