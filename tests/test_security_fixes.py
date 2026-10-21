# -*- coding: utf-8 -*-
"""安全审计三条 medium 修复的专项测试。

A1: Fernet 密钥迁出程序目录到每用户目录（迁移/回退/权限）
A2: IMAP 去重按 Message-ID 精确绑定；部分拒收后禁用主题级短路
B2: 空密码拒绝生成未加密归档（显式 allow_unencrypted 才放行）
"""
import datetime
import os
import tempfile
import unittest
from email.utils import format_datetime
from unittest import mock

from cryptography.fernet import Fernet

import compressor
import config_manager
import mailer


class KeyIsolationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = self._tmp.name
        self.new_key = os.path.join(base, "userdir", "secret.key")
        self.legacy_key = os.path.join(base, "progdir", ".secret.key")
        os.makedirs(os.path.dirname(self.legacy_key))
        self._patchers = [
            mock.patch.object(config_manager, "_SECRET_KEY_FILE", self.new_key),
            mock.patch.object(
                config_manager, "_LEGACY_SECRET_KEY_FILE", self.legacy_key),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def _write_legacy(self):
        key = Fernet.generate_key()
        with open(self.legacy_key, "wb") as f:
            f.write(key)
        return key

    def test_legacy_key_migrates_and_decrypts(self):
        key = self._write_legacy()
        cipher = Fernet(key).encrypt("mailbox-password".encode()).decode()
        self.assertEqual(
            config_manager.decrypt_password(cipher), "mailbox-password")
        self.assertTrue(os.path.exists(self.new_key))
        self.assertFalse(os.path.exists(self.legacy_key))
        with open(self.new_key, "rb") as f:
            self.assertEqual(f.read(), key)
        if os.name == "posix":
            self.assertEqual(os.stat(self.new_key).st_mode & 0o777, 0o600)

    def test_fresh_key_created_in_user_dir(self):
        cipher = config_manager.encrypt_password("pw")
        self.assertTrue(os.path.exists(self.new_key))
        self.assertFalse(os.path.exists(self.legacy_key))
        self.assertEqual(config_manager.decrypt_password(cipher), "pw")

    def test_failed_migration_falls_back_to_legacy(self):
        key = self._write_legacy()
        cipher = Fernet(key).encrypt("pw2".encode()).decode()
        blocker = os.path.join(self._tmp.name, "blocker")
        with open(blocker, "w") as f:
            f.write("x")
        bad_new = os.path.join(blocker, "secret.key")  # 父路径是普通文件，不可写
        with mock.patch.object(config_manager, "_SECRET_KEY_FILE", bad_new):
            self.assertEqual(config_manager.decrypt_password(cipher), "pw2")
        self.assertTrue(os.path.exists(self.legacy_key))


class EmptyPasswordBlockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self._tmp.name, "note.txt")
        with open(self.src, "w") as f:
            f.write("hello")
        self.out = os.path.join(self._tmp.name, "out.7z")

    def tearDown(self):
        self._tmp.cleanup()

    def test_compress_to_7z_rejects_empty_password(self):
        for pw in (None, ""):
            with self.assertRaises(ValueError):
                compressor.compress_to_7z([self.src], self.out, password=pw)

    def test_compress_and_split_rejects_empty_password(self):
        with self.assertRaises(ValueError):
            compressor.compress_and_split(
                [self.src], self._tmp.name, "arc", password="")

    def test_explicit_opt_in_allows_unencrypted(self):
        import py7zr
        path = compressor.compress_to_7z(
            [self.src], self.out, password=None, allow_unencrypted=True)
        with py7zr.SevenZipFile(path, "r") as z:
            self.assertIn("note.txt", z.getnames())

    def test_encrypted_archive_needs_password(self):
        import py7zr
        path = compressor.compress_to_7z(
            [self.src], self.out, password="pw123")
        with self.assertRaises(Exception):
            with py7zr.SevenZipFile(path, "r") as z:
                z.getnames()
        with py7zr.SevenZipFile(path, "r", password="pw123") as z:
            self.assertIn("note.txt", z.getnames())


class _FakeIMAP2:
    """最小 IMAP 替身：单封已发送邮件，内容由 fetch_headers 给定。"""

    def __init__(self, *args, **kwargs):
        self.fetch_headers = {}

    def login(self, user, password):
        pass

    def list(self):
        return ("OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'])

    def select(self, box, readonly=False):
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "search":
            return ("OK", [b"1"])
        if cmd == "fetch":
            raw = self.fetch_headers.get(
                b"1", b"Subject: unrelated\r\n\r\n")
            return ("OK", [(b"1 (BODY[HEADER.FIELDS])", raw)])
        raise AssertionError(cmd)

    def logout(self):
        pass


class DedupBindingTests(unittest.TestCase):
    CFG = {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "password": "encrypted",
        "use_ssl": False,
    }

    def _check(self, imap, subject="arc [1/1]", **kwargs):
        with mock.patch.object(mailer, "decrypt_password", return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL", return_value=imap):
            return mailer._imap_sent_has_subject(
                self.CFG, "sender@gmail.com", subject, **kwargs)

    def test_same_subject_different_message_id_is_not_a_hit(self):
        imap = _FakeIMAP2()
        imap.fetch_headers = {
            b"1": b"Subject: arc [1/1]\r\nMessage-ID: <other@x>\r\n\r\n"}
        self.assertFalse(self._check(imap, message_id="<mine@x>"))

    def test_matching_message_id_is_a_hit(self):
        imap = _FakeIMAP2()
        imap.fetch_headers = {
            b"1": b"Subject: something else\r\nMessage-ID: <mine@x>\r\n\r\n"}
        self.assertTrue(self._check(imap, message_id="<mine@x>"))

    def test_subject_fallback_preserved_without_message_id(self):
        recent = format_datetime(
            datetime.datetime.now(datetime.timezone.utc))
        imap = _FakeIMAP2()
        imap.fetch_headers = {
            b"1": ("Subject: arc [1/1]\r\nDate: %s\r\n\r\n" % recent).encode()}
        self.assertTrue(self._check(imap))

    def _one_volume(self, td):
        vp = os.path.join(td, "a.7z.001")
        with open(vp, "wb") as f:
            f.write(b"x")
        return vp

    def test_no_imap_shortcut_after_partial_refusal(self):
        calls = []

        def fake_send_email(cfg, frm, to, subj, body, ap=None, an=None,
                            message_id=None):
            calls.append((list(to), message_id))
            if len(calls) == 1:
                raise mailer.PartialRecipientsRefused(
                    {"rejected@example.com": (550, b"no")}, to)

        imap_check = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as td:
            vp = self._one_volume(td)
            with mock.patch.object(mailer, "send_email",
                                   side_effect=fake_send_email), \
                 mock.patch.object(mailer, "_imap_sent_has_subject", imap_check), \
                 mock.patch.object(mailer.time, "sleep"):
                mailer.send_volumes(
                    [{"smtp_config": self.CFG, "from_addr": "sender@gmail.com"}],
                    ["accepted@example.com", "rejected@example.com"],
                    "arc", [vp],
                    interval_seconds=0, max_retries=3, retry_wait=0)
        imap_check.assert_not_called()
        self.assertEqual(
            [c[0] for c in calls],
            [["accepted@example.com", "rejected@example.com"],
             ["rejected@example.com"]])
        self.assertTrue(all(c[1] for c in calls))
        self.assertEqual(calls[0][1], calls[1][1])  # 同卷重试共用同一 ID

    def test_imap_hit_still_shortcuts_plain_timeout(self):
        calls = []

        def fake_send_email(cfg, frm, to, subj, body, ap=None, an=None,
                            message_id=None):
            calls.append(message_id)
            raise TimeoutError("read timed out")

        imap_check = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as td:
            vp = self._one_volume(td)
            with mock.patch.object(mailer, "send_email",
                                   side_effect=fake_send_email), \
                 mock.patch.object(mailer, "_imap_sent_has_subject", imap_check), \
                 mock.patch.object(mailer.time, "sleep"):
                mailer.send_volumes(
                    [{"smtp_config": self.CFG, "from_addr": "sender@gmail.com"}],
                    ["recipient@example.com"], "arc", [vp],
                    interval_seconds=0, max_retries=3, retry_wait=0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(imap_check.call_args.kwargs.get("message_id"))


if __name__ == "__main__":
    unittest.main()
