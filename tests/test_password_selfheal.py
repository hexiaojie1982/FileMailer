# -*- coding: utf-8 -*-
"""多副本 Fernet 密钥不一致的自愈及发送中止回归测试。"""
import argparse
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

import cli_runner
import config_manager
import mailer


class PasswordSelfHealTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.path.dirname(__file__))
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.accounts = self.base / 'accounts.json'
        self.current_path = self.base / 'user' / 'secret.key'
        self.legacy_path = self.base / '.secret.key'
        for name, path in (
                ('_ACCOUNTS_FILE', self.accounts),
                ('_SECRET_KEY_FILE', self.current_path),
                ('_LEGACY_SECRET_KEY_FILE', self.legacy_path),
                ('_SETTINGS_FILE', self.base / 'settings.json'),
                ('_ACTIVE_TASK_FILE', self.base / 'active_task.json')):
            patcher = mock.patch.object(config_manager, name, str(path))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.current_key = Fernet.generate_key()
        self.legacy_key = Fernet.generate_key()
        self.current = Fernet(self.current_key)
        self.legacy = Fernet(self.legacy_key)
        self.current_path.parent.mkdir()
        self.current_path.write_bytes(self.current_key)
        self.legacy_path.write_bytes(self.legacy_key)
        self.old_token = self.legacy.encrypt(b'first-password').decode()
        self.data = {
            'accounts': [
                {'name': 'old-one', 'password': self.old_token},
                {'name': 'old-two',
                 'password': self.legacy.encrypt('密码二'.encode()).decode()},
                {'name': 'current',
                 'password': self.current.encrypt(b'current-password').decode()},
            ],
            'metadata': {'keep': True},
        }
        self._save_fixture()

    def _save_fixture(self):
        self.accounts.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=4), encoding='utf-8')
        self.original = self.accounts.read_bytes()

    def _backups(self):
        return list(self.base.glob('accounts.before-selfheal-*.json'))

    def _assert_unchanged(self):
        self.assertEqual(self.accounts.read_bytes(), self.original)
        self.assertEqual(self.current_path.read_bytes(), self.current_key)
        self.assertEqual(self.legacy_path.read_bytes(), self.legacy_key)
        self.assertEqual(list(self.base.glob('.accounts-selfheal-*')), [])

    def test_old_key_decrypts_and_selfheals_all_accounts_with_backup(self):
        self.assertEqual(
            config_manager.decrypt_password(self.old_token), 'first-password')
        backups = self._backups()
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name,
                         r'^accounts\.before-selfheal-\d{8}-\d{6}\.json$')
        self.assertEqual(backups[0].read_bytes(), self.original)
        migrated = json.loads(self.accounts.read_text(encoding='utf-8'))
        self.assertEqual(migrated['metadata'], self.data['metadata'])
        self.assertEqual([a['name'] for a in migrated['accounts']],
                         [a['name'] for a in self.data['accounts']])
        self.assertEqual(migrated['accounts'][2], self.data['accounts'][2])
        self.assertEqual(
            [self.current.decrypt(a['password'].encode()).decode()
             for a in migrated['accounts']],
            ['first-password', '密码二', 'current-password'])
        self.assertEqual(self.current_path.read_bytes(), self.current_key)
        self.assertEqual(self.legacy_path.read_bytes(), self.legacy_key)
        with mock.patch.object(config_manager, '_LEGACY_SECRET_KEY_FILE',
                               str(self.base / 'absent.key')):
            for account, password in zip(
                    migrated['accounts'], ['first-password', '密码二', 'current-password']):
                self.assertEqual(config_manager.decrypt_password(account['password']), password)

    def test_stale_in_memory_token_is_idempotent_after_selfheal(self):
        config_manager.decrypt_password(self.old_token)
        migrated = self.accounts.read_bytes()
        backups = self._backups()
        self.assertEqual(config_manager.decrypt_password(self.old_token), 'first-password')
        self.assertEqual(self.accounts.read_bytes(), migrated)
        self.assertEqual(self._backups(), backups)

    def test_current_key_success_does_not_rewrite_accounts(self):
        token = self.data['accounts'][2]['password']
        self.assertEqual(config_manager.decrypt_password(token), 'current-password')
        self._assert_unchanged()
        self.assertEqual(self._backups(), [])

    def test_neither_key_decrypts_reports_password_error(self):
        token = Fernet(Fernet.generate_key()).encrypt(b'unknown').decode()
        with self.assertRaises(config_manager.PasswordDecryptError) as raised:
            config_manager.decrypt_password(token)
        for phrase in ('账号密码解密失败', '密钥不匹配', '不是网络问题'):
            self.assertIn(phrase, str(raised.exception))
        self._assert_unchanged()
        self.assertEqual(self._backups(), [])

    def test_missing_legacy_key_reports_password_error(self):
        with mock.patch.object(config_manager, '_LEGACY_SECRET_KEY_FILE',
                               str(self.base / 'absent.key')):
            with self.assertRaises(config_manager.PasswordDecryptError):
                config_manager.decrypt_password(self.old_token)
        self._assert_unchanged()

    def test_same_key_does_not_attempt_selfheal(self):
        self.legacy_path.write_bytes(self.current_key)
        with mock.patch.object(config_manager, '_selfheal_accounts') as heal:
            with self.assertRaises(config_manager.PasswordDecryptError):
                config_manager.decrypt_password(self.old_token)
        heal.assert_not_called()
        self.assertEqual(self.accounts.read_bytes(), self.original)

    def test_unrecoverable_other_account_prevents_partial_migration(self):
        self.data['accounts'][1]['password'] = Fernet(
            Fernet.generate_key()).encrypt(b'unknown').decode()
        self._save_fixture()
        with self.assertRaises(config_manager.PasswordDecryptError):
            config_manager.decrypt_password(self.old_token)
        self._assert_unchanged()

    def test_staged_verification_failure_keeps_original_and_backup(self):
        # 既验证解不开的写入，也验证密文合法但明文被改坏的写入。
        for label, cipher in (
                ('invalid-token', Fernet(Fernet.generate_key()).encrypt(b'bad')),
                ('wrong-plaintext', self.current.encrypt(b'wrong-password'))):
            with self.subTest(cipher_type=label):
                def corrupt_dump(data, stream, **kwargs):
                    damaged = json.loads(json.dumps(data))
                    damaged['accounts'][0]['password'] = cipher.decode()
                    stream.write(json.dumps(damaged))

                with mock.patch.object(config_manager.json, 'dump', side_effect=corrupt_dump):
                    with self.assertRaises(config_manager.PasswordDecryptError):
                        config_manager.decrypt_password(self.old_token)
                self._assert_unchanged()
                self.assertTrue(self._backups())
                for backup in self._backups():
                    self.assertEqual(backup.read_bytes(), self.original)

    def test_selfheal_does_not_touch_other_config_or_keys(self):
        settings = self.base / 'settings.json'
        task = self.base / 'active_task.json'
        settings.write_bytes(b'{"sentinel": "settings"}')
        task.write_bytes(b'{"sentinel": "task"}')
        config_manager.decrypt_password(self.old_token)
        self.assertEqual(settings.read_bytes(), b'{"sentinel": "settings"}')
        self.assertEqual(task.read_bytes(), b'{"sentinel": "task"}')
        self.assertEqual(self.current_path.read_bytes(), self.current_key)
        self.assertEqual(self.legacy_path.read_bytes(), self.legacy_key)

    def test_corrupt_accounts_json_is_not_overwritten(self):
        self.accounts.write_bytes(b'{invalid-json')
        self.original = self.accounts.read_bytes()
        with self.assertRaises(config_manager.PasswordDecryptError):
            config_manager.decrypt_password(self.old_token)
        self._assert_unchanged()

    def test_same_second_backups_are_never_overwritten(self):
        with mock.patch.object(config_manager, 'datetime') as clock:
            clock.now.return_value.strftime.return_value = '20261023-120000'
            config_manager.decrypt_password(self.old_token)
            first_backup = self._backups()[0]
            original_backup = first_backup.read_bytes()
            self.data['metadata']['second'] = True
            self._save_fixture()
            config_manager.decrypt_password(self.old_token)
        self.assertEqual(len(self._backups()), 2)
        self.assertEqual(first_backup.read_bytes(), original_backup)
        self.assertIn(self.original, [path.read_bytes() for path in self._backups()])

    def test_replace_failure_preserves_original(self):
        with mock.patch.object(config_manager.os, 'replace',
                               side_effect=OSError('write denied')):
            with self.assertRaises(config_manager.PasswordDecryptError):
                config_manager.decrypt_password(self.old_token)
        self._assert_unchanged()
        self.assertEqual(self._backups()[0].read_bytes(), self.original)

    def test_backup_failure_prevents_overwrite(self):
        real_open = open

        def deny_backup(path, *args, **kwargs):
            if 'accounts.before-selfheal-' in str(path):
                raise OSError('backup denied')
            return real_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=deny_backup), \
             mock.patch.object(config_manager.os, 'replace') as replace:
            with self.assertRaises(config_manager.PasswordDecryptError):
                config_manager.decrypt_password(self.old_token)
        replace.assert_not_called()
        self._assert_unchanged()


class PasswordDecryptMailerTests(unittest.TestCase):
    CFG = {'smtp_server': 'smtp.gmail.com', 'smtp_port': 465,
           'password': 'encrypted', 'use_ssl': True}

    def test_send_volume_decrypt_failure_never_retries_or_switches_account(self):
        error = config_manager.PasswordDecryptError()
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as td:
            volume = Path(td) / 'a.7z.001'
            volume.write_bytes(b'test')
            progress = mock.Mock()
            switch = mock.Mock()
            sent = mock.Mock()
            with mock.patch.object(mailer, 'send_email', side_effect=error) as send, \
                 mock.patch.object(mailer.time, 'sleep') as sleep, \
                 mock.patch.object(mailer, '_imap_sent_has_subject') as imap:
                with self.assertRaises(config_manager.PasswordDecryptError) as raised:
                    mailer.send_volumes(
                        [{'smtp_config': self.CFG, 'from_addr': address}
                         for address in ('first@example.com', 'second@example.com')],
                        ['recipient@example.com'], 'arc', [str(volume)],
                        max_retries=3, retry_wait=15, progress_callback=progress,
                        volume_sent_callback=sent, account_switch_callback=switch)
            self.assertIs(raised.exception, error)
            self.assertEqual(send.call_count, 1)
            sleep.assert_not_called()
            imap.assert_not_called()
            switch.assert_not_called()
            sent.assert_not_called()
            self.assertEqual(volume.read_bytes(), b'test')
            self.assertTrue(Path(str(volume) + '.zip').exists())
            messages = [call.args[0] for call in progress.call_args_list]
            stops = [msg for msg in messages if '已停止发送' in msg]
            self.assertEqual(len(stops), 1)
            for phrase in ('账号密码解密失败', '密钥不匹配', '不是网络问题'):
                self.assertIn(phrase, stops[0])
            self.assertFalse(any('网络重试' in msg or '全部发送完成' in msg
                                 for msg in messages))

    def test_imap_decrypt_failure_is_propagated_before_connection(self):
        error = config_manager.PasswordDecryptError()
        with mock.patch.object(mailer, 'decrypt_password', side_effect=error), \
             mock.patch.object(mailer.imaplib, 'IMAP4_SSL') as imap:
            with self.assertRaises(config_manager.PasswordDecryptError) as raised:
                mailer._imap_sent_has_subject(self.CFG, 'first@example.com', 'arc')
        self.assertIs(raised.exception, error)
        imap.assert_not_called()

    def test_imap_password_error_stops_retry_loop_with_progress(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as td:
            volume = Path(td) / 'a.7z.001'
            volume.write_bytes(b'test')
            progress = mock.Mock()
            switch = mock.Mock()
            with mock.patch.object(mailer, 'send_email',
                                   side_effect=TimeoutError('read timeout')) as send, \
                 mock.patch.object(mailer, 'decrypt_password',
                                   side_effect=config_manager.PasswordDecryptError()), \
                 mock.patch.object(mailer.imaplib, 'IMAP4_SSL') as imap, \
                 mock.patch.object(mailer.time, 'sleep') as sleep:
                with self.assertRaises(config_manager.PasswordDecryptError):
                    mailer.send_volumes(
                        [{'smtp_config': self.CFG, 'from_addr': 'first@example.com'}],
                        ['recipient@example.com'], 'arc', [str(volume)],
                        max_retries=3, retry_wait=0, progress_callback=progress,
                        account_switch_callback=switch)
            self.assertEqual(send.call_count, 1)
            imap.assert_not_called()
            sleep.assert_not_called()
            switch.assert_not_called()
            self.assertIn('已停止发送', progress.call_args.args[0])

    def test_send_email_password_error_never_opens_smtp(self):
        with mock.patch.object(mailer, 'decrypt_password',
                               side_effect=config_manager.PasswordDecryptError()), \
             mock.patch.object(mailer.smtplib, 'SMTP_SSL') as ssl, \
             mock.patch.object(mailer.smtplib, 'SMTP') as smtp:
            with self.assertRaises(config_manager.PasswordDecryptError):
                mailer.send_email(self.CFG, 'first@example.com',
                                  ['recipient@example.com'], 'arc', 'body')
        ssl.assert_not_called()
        smtp.assert_not_called()


class PasswordDecryptCliTests(unittest.TestCase):
    def test_cli_preserves_message_and_task_without_retry_prompt(self):
        args = argparse.Namespace(resume=True, files=None, account='sender',
                                  to_addrs=None, interval=0, send_limit=0,
                                  max_retries=3, retry_wait=15, prompt_timeout=0)
        error = config_manager.PasswordDecryptError()
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as td:
            task = {'temp_dir': td, 'subject_prefix': 'arc',
                    'volume_paths': [os.path.join(td, 'a.7z.001')],
                    'recipients': ['recipient@example.com'], 'sent_indices': []}
            output = io.StringIO()
            with mock.patch.object(cli_runner, 'configure_console_encoding'), \
                 mock.patch.object(cli_runner, 'setup_logging', return_value=None), \
                 mock.patch.object(config_manager, 'load_active_task', return_value=task), \
                 mock.patch.object(cli_runner, 'build_sender_accounts', return_value=[
                     {'smtp_config': {}, 'from_addr': 'first@example.com'}]), \
                 mock.patch.object(mailer, 'send_volumes', side_effect=error) as send, \
                 mock.patch.object(cli_runner, 'timed_input') as prompt, \
                 mock.patch.object(config_manager, 'clear_active_task') as clear, \
                 mock.patch.object(config_manager, 'save_active_task') as save, \
                 mock.patch.object(cli_runner.shutil, 'rmtree') as remove, \
                 mock.patch('sys.stdout', output):
                with self.assertRaises(SystemExit) as raised:
                    cli_runner.run_cli(args)
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(str(error), output.getvalue())
            self.assertEqual(send.call_count, 1)
            prompt.assert_not_called()
            clear.assert_not_called()
            save.assert_not_called()
            remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
