# -*- coding: utf-8 -*-
"""重试路径已发送核验（2026-10-22 补丁）的专项测试。

背景：2026-10-14、10-18 两次事故中，SMTP 读超时被误判为发送失败，
重试发出重复邮件。此前核验只认 Message-ID 精确匹配；若 Gmail 改写了
客户端提交的 Message-ID，核验会静默失效。本补丁改为组合判定：
Message-ID 精确命中优先，未命中时以"主题一致 + 时间戳在窗口内"兜底。

本文件验证：
- 组合判定的命中/不命中边界（真实 _imap_sent_has_subject + IMAP 替身）
- 端到端：模拟读超时且邮件已在已发送（Message-ID 被改写）→ 重发次数为零
- 端到端：模拟真实失败（已发送里没有）→ 重试仍正常发生
"""
import datetime
import os
import tempfile
import unittest
from email.utils import format_datetime
from unittest import mock

import mailer


def _recent():
    return format_datetime(datetime.datetime.now(datetime.timezone.utc))


def _old():
    dt = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(hours=3))
    return format_datetime(dt)


class _FakeIMAPMulti:
    """IMAP 替身：已发送箱里可放多封邮件，内容由 headers 列表给定。"""

    def __init__(self, headers_list):
        self.headers_list = headers_list

    def login(self, user, password):
        pass

    def list(self):
        return ("OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'])

    def select(self, box, readonly=False):
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "search":
            uids = b" ".join(
                str(i + 1).encode() for i in range(len(self.headers_list)))
            return ("OK", [uids])
        if cmd == "fetch":
            uid = args[0]
            idx = int(uid) - 1 if isinstance(uid, (bytes, str)) else 0
            raw = self.headers_list[idx]
            return ("OK", [(b"%s (BODY[HEADER.FIELDS])"
                            % str(idx + 1).encode(), raw)])
        raise AssertionError(cmd)

    def logout(self):
        pass


class CombinedMatchingTests(unittest.TestCase):
    CFG = {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "password": "encrypted",
        "use_ssl": False,
    }

    def _check(self, headers_list, subject="arc [1/1]", **kwargs):
        imap = _FakeIMAPMulti(headers_list)
        with mock.patch.object(mailer, "decrypt_password",
                               return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL",
                               return_value=imap):
            return mailer._imap_sent_has_subject(
                self.CFG, "sender@gmail.com", subject, **kwargs)

    def test_exact_message_id_hit(self):
        headers = [b"Subject: other\r\nMessage-ID: <mine@x>\r\n\r\n"]
        self.assertTrue(self._check(headers, message_id="<mine@x>"))

    def test_rewritten_id_rescued_by_subject_and_recent_date(self):
        # Gmail 改写了 Message-ID：组合判定（主题+近期时间戳）应命中
        raw = ("Subject: arc [1/1]\r\nMessage-ID: <rewritten@gmail>\r\n"
               "Date: %s\r\n\r\n" % _recent()).encode()
        self.assertTrue(self._check([raw], message_id="<mine@x>"))

    def test_same_subject_but_old_date_is_not_a_hit(self):
        raw = ("Subject: arc [1/1]\r\nMessage-ID: <rewritten@gmail>\r\n"
               "Date: %s\r\n\r\n" % _old()).encode()
        self.assertFalse(self._check([raw], message_id="<mine@x>"))

    def test_same_subject_but_missing_date_is_not_a_hit(self):
        raw = b"Subject: arc [1/1]\r\nMessage-ID: <rewritten@gmail>\r\n\r\n"
        self.assertFalse(self._check([raw], message_id="<mine@x>"))

    def test_recent_date_but_different_subject_is_not_a_hit(self):
        raw = ("Subject: other package [1/1]\r\n"
               "Message-ID: <rewritten@gmail>\r\n"
               "Date: %s\r\n\r\n" % _recent()).encode()
        self.assertFalse(self._check([raw], message_id="<mine@x>"))

    def test_hit_found_among_multiple_candidates(self):
        miss = ("Subject: something\r\nMessage-ID: <a@b>\r\n"
                "Date: %s\r\n\r\n" % _recent()).encode()
        hit = ("Subject: arc [1/1]\r\nMessage-ID: <rewritten@gmail>\r\n"
               "Date: %s\r\n\r\n" % _recent()).encode()
        self.assertTrue(self._check([miss, hit], message_id="<mine@x>"))


class RetryPathEndToEndTests(unittest.TestCase):
    CFG = {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "password": "encrypted",
        "use_ssl": False,
    }

    def _one_volume(self, td):
        vp = os.path.join(td, "a.7z.001")
        with open(vp, "wb") as f:
            f.write(b"x")
        return vp

    def _run(self, td, fake_send_email, imap):
        vp = self._one_volume(td)
        with mock.patch.object(mailer, "send_email",
                               side_effect=fake_send_email), \
             mock.patch.object(mailer, "decrypt_password",
                               return_value="secret"), \
             mock.patch.object(mailer.imaplib, "IMAP4_SSL",
                               return_value=imap), \
             mock.patch.object(mailer.time, "sleep"):
            mailer.send_volumes(
                [{"smtp_config": self.CFG, "from_addr": "sender@gmail.com"}],
                ["recipient@example.com"], "arc", [vp],
                interval_seconds=0, max_retries=3, retry_wait=0)

    def test_read_timeout_already_sent_means_zero_resends(self):
        calls = []

        def always_timeout(cfg, frm, to, subj, body, ap=None, an=None,
                           message_id=None):
            calls.append(message_id)
            raise TimeoutError("read timed out")

        # 已发送里有一封：主题相同、时间戳为刚刚、Message-ID 已被改写
        raw = ("Subject: arc [1/1]\r\nMessage-ID: <rewritten@gmail>\r\n"
               "Date: %s\r\n\r\n" % _recent()).encode()
        with tempfile.TemporaryDirectory() as td:
            self._run(td, always_timeout, _FakeIMAPMulti([raw]))
        self.assertEqual(len(calls), 1)  # 只发过一次，重发次数为零

    def test_read_timeout_exact_id_means_zero_resends(self):
        calls = []
        captured = {}

        def timeout_once(cfg, frm, to, subj, body, ap=None, an=None,
                         message_id=None):
            calls.append(message_id)
            captured["mid"] = message_id
            raise TimeoutError("read timed out")

        class _DynamicIMAP(_FakeIMAPMulti):
            def uid(self, cmd, *args):
                if cmd == "search":
                    return ("OK", [b"1"])
                if cmd == "fetch":
                    raw = ("Subject: arc [1/1]\r\nMessage-ID: %s\r\n"
                           "Date: %s\r\n\r\n"
                           % (captured["mid"], _recent())).encode()
                    return ("OK", [(b"1 (BODY[HEADER.FIELDS])", raw)])
                return super().uid(cmd, *args)

        with tempfile.TemporaryDirectory() as td:
            self._run(td, timeout_once, _DynamicIMAP([]))
        self.assertEqual(len(calls), 1)

    def test_real_failure_still_retries(self):
        calls = []

        def fail_then_ok(cfg, frm, to, subj, body, ap=None, an=None,
                         message_id=None):
            calls.append(message_id)
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return None

        with tempfile.TemporaryDirectory() as td:
            # 已发送里什么都没有：核验不命中，重试必须照常发生
            self._run(td, fail_then_ok, _FakeIMAPMulti([]))
        self.assertEqual(len(calls), 2)  # 首次失败 + 一次重试成功


if __name__ == "__main__":
    unittest.main()
