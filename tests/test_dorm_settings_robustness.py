"""Store._read 的健壮性回归测试。

背景（真实踩到的坑）：用 Windows PowerShell 5.1 的 `Set-Content -Encoding utf8`
改 settings.json，会写入 BOM。`json.loads('\ufeff{...}')` 抛 JSONDecodeError，
而旧实现只捕获 FileNotFoundError，异常一路冒到界面同步循环，
整个面板显示「界面暂时无法连接后台」—— 一个字节的 BOM 打挂整个 UI。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dorm_checkin import Store  # noqa: E402


def make_store(root: Path) -> Store:
    return Store(root, protector=Mock(protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]))


class SettingsFileRobustness(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = make_store(self.root)

    def write(self, payload: bytes):
        (self.root / 'settings.json').write_bytes(payload)

    def test_plain_utf8_is_read(self):
        self.write(json.dumps({'enabled': True, 'interval': 600}).encode('utf-8'))
        settings = self.store.settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.interval, 600)

    def test_utf8_bom_is_tolerated(self):
        """BOM 不该让设置读取失败（这是打挂界面的直接原因）。"""
        payload = json.dumps({'enabled': True, 'interval': 600}).encode('utf-8')
        self.write(b'\xef\xbb\xbf' + payload)
        settings = self.store.settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.interval, 600)

    def test_corrupt_file_still_fails_closed(self):
        """真正损坏的 JSON 必须继续 fail-closed 并显式报错，不得静默降级。

        静默降级成默认值等于悄悄关掉自动打卡却不告诉用户，
        所以「容忍 BOM」与「容忍损坏」是两件事，这里守住后者。
        （界面不受影响由 desktop_bridge.snapshot 的边界隔离保证。）
        """
        self.write(b'{ this is not json')
        with self.assertRaises(ValueError):
            self.store.settings()

    def test_empty_file_still_fails_closed(self):
        self.write(b'')
        with self.assertRaises(ValueError):
            self.store.settings()

    def test_missing_file_still_uses_defaults(self):
        self.assertFalse(self.store.settings().enabled)

    def test_history_log_with_bom_still_reads(self):
        """history.log 也可能被外部工具加上 BOM。"""
        (self.root / 'history.log').write_bytes(
            b'\xef\xbb\xbf2026-09-24T23:27:50+08:00 [login_required] x\n')
        text = self.store.history()
        self.assertIn('login_required', text)

    def test_written_settings_have_no_bom(self):
        """应用自己写出的文件必须无 BOM（save/load 往返一致）。"""
        from dorm_checkin import Settings

        self.store.save_settings(Settings(enabled=True, interval=600))
        raw = (self.root / 'settings.json').read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(self.store.settings().enabled)


if __name__ == '__main__':
    unittest.main()
