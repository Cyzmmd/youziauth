"""User-session dormitory workflow, with conservative submission recovery."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

from windows_credentials import CredentialStore, DpapiProtector, atomic_write_bytes

SHANGHAI = dt.timezone(dt.timedelta(hours=8))


def now() -> dt.datetime:
    return dt.datetime.now(SHANGHAI)


class CheckinError(RuntimeError):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def parse_time(value: str) -> dt.time:
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ValueError('时间请填写 HH:MM，例如 21:00')
    return dt.time.fromisoformat(value)


@dataclasses.dataclass(frozen=True)
class Settings:
    enabled: bool = False
    start: str = '21:00'
    end: str = '23:30'
    interval: int = 300

    def validate(self):
        if type(self.enabled) is not bool:
            raise ValueError('自动打卡开关无效')
        if parse_time(self.start) >= parse_time(self.end):
            raise ValueError('检查结束时间必须晚于开始时间，不支持跨日时段')
        if type(self.interval) is not int or not 60 <= self.interval <= 3600:
            raise ValueError('检查间隔应为 60–3600 秒')
        return self


@dataclasses.dataclass(frozen=True)
class Task:
    id: str
    form_id: str
    publish_id: str
    student: str
    date: str
    title: str
    start: str
    end: str
    signed: bool
    address: str
    radius: str
    dorm_form: str = ''

    @property
    def key(self):
        return json.dumps([self.student, self.date, self.id, self.form_id], separators=(',', ':'))

    def phase(self, current: dt.datetime) -> str:
        if self.date != current.date().isoformat():
            raise CheckinError('error', '任务日期不是今天，已停止')
        try:
            start, end = parse_time(self.start), parse_time(self.end)
        except ValueError:
            raise CheckinError('error', '服务器任务时段不明确，已停止') from None
        if end <= start:
            raise CheckinError('error', '暂不支持跨日任务，已停止')
        if current.time() < start:
            return 'waiting'
        if current.time() >= end:
            return 'expired'
        return 'ready'


@dataclasses.dataclass(frozen=True)
class Result:
    state: str
    message: str
    task: Task | None = None
    at: str = ''


class Store:
    def __init__(self, root: Path | None = None, protector=None):
        self.root = Path(root) if root is not None else Path(
            os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
        ) / 'youziauth' / 'dorm'
        self._protector = protector

    def _credentials(self):
        return CredentialStore(self.root / 'session', self._protector or DpapiProtector(machine_scope=False))

    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default

    def _write(self, name, data):
        atomic_write_bytes(self.root / name, json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def settings(self):
        return Settings(**self._read('settings.json', {})).validate()

    def save_settings(self, settings):
        self._write('settings.json', dataclasses.asdict(settings.validate()))

    def token(self):
        if not (self.root / 'session' / 'credential.dat').exists():
            return ''
        try:
            return self._credentials().load_password()
        except Exception:
            raise CheckinError('login_required', '登录凭据无法解密，请重新登录') from None

    def save_token(self, token):
        if not isinstance(token, str) or not token.strip() or any(c in token for c in '\r\n'):
            raise ValueError('登录凭据格式无效')
        self._credentials().save_password(token)

    def clear_token(self):
        (self.root / 'session' / 'credential.dat').unlink(missing_ok=True)

    def _pending_keys(self):
        value = self._read('pending.json', [])
        if isinstance(value, str):
            value = [value] if value else []
        if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
            raise ValueError('提交恢复记录损坏')
        return value

    def pending(self, key=None):
        keys = self._pending_keys()
        return key in keys if key is not None else (keys[-1] if keys else '')

    def set_pending(self, key):
        keys = self._pending_keys()
        if key not in keys:
            keys.append(key)
        self._write('pending.json', keys)

    def clear_pending(self, key):
        self._write('pending.json', [k for k in self._pending_keys() if k != key])

    def record(self, result):
        self._write('status.json', dataclasses.asdict(result))
        # Only controlled messages; no raw exceptions, token, response bodies or coordinates.
        old = self.history().splitlines()[-99:]
        old.append(f'{result.at} [{result.state}] {result.message}')
        atomic_write_bytes(self.root / 'history.log', ('\n'.join(old) + '\n').encode('utf-8'))

    def history(self):
        try:
            return (self.root / 'history.log').read_text(encoding='utf-8')
        except FileNotFoundError:
            return ''


class Engine:
    def __init__(self, store, api, location, clock=now):
        self.store, self.api, self.location, self.clock = store, api, location, clock
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.next_tick = 0.0

    def tick(self):
        if time.monotonic() < self.next_tick:
            return None
        self.next_tick = time.monotonic() + 60
        return self.run(automatic=True, submit=True)

    def run(self, *, automatic=False, submit=False):
        if not self.lock.acquire(blocking=False):
            return Result('busy', '打卡操作正在进行')
        task = None
        try:
            settings = self.store.settings()
            self.next_tick = time.monotonic() + settings.interval
            current = self.clock()
            if self.cancel.is_set():
                return self._result('cancelled', '操作已取消')
            if automatic and not settings.enabled:
                return Result('disabled', '自动打卡未启用')
            if automatic and not parse_time(settings.start) <= current.time() < parse_time(settings.end):
                return Result('waiting', f'等待检查时段 {settings.start}–{settings.end}')
            token = self.store.token()
            if not token:
                return self._result('login_required', '请先登录统一身份认证')
            student = self.api.user(token)
            task = self.api.today(token, student, current)
            if task is None:
                return self._result('no_task', '今天暂无查寝任务')
            if task.student != student:
                raise CheckinError('error', '任务账号与登录账号不一致，已停止')
            phase = task.phase(current)
            if task.signed:
                if self.store.pending(task.key):
                    self.store.clear_pending(task.key)
                return self._result('signed', '服务器已确认今日任务完成', task)
            if self.store.pending(task.key):
                return self._readback(token, task)
            if phase != 'ready':
                return self._result(phase, '任务尚未开始' if phase == 'waiting' else '任务已过期，未提交', task)
            if not submit:
                return self._result('ready', '今日任务待完成，可提交打卡', task)
            position = self.location()
            self._check_cancel()
            self.api.verify(token, task, position)
            self._check_cancel()
            if task.phase(self.clock()) != 'ready':
                return self._result('expired', '定位期间任务时段已结束，未提交', task)
            if automatic and not self.store.settings().enabled:
                return self._result('disabled', '自动打卡已关闭，未提交', task)
            # Write-ahead marker: if the process dies or the response is lost, only query next time.
            self.store.set_pending(task.key)
            try:
                # Disk persistence may block; recheck immediately before issuing the POST.
                self._check_cancel()
                current = self.clock()
                phase = task.phase(current)
                if phase != 'ready':
                    raise CheckinError(phase, '任务已离开有效时段，未提交')
                if automatic:
                    latest = self.store.settings()
                    if not latest.enabled:
                        raise CheckinError('disabled', '自动打卡已关闭，未提交')
                    if not parse_time(latest.start) <= current.time() < parse_time(latest.end):
                        raise CheckinError('waiting', '已离开自动检查时段，未提交')
            except Exception:
                # No POST has been issued, so this marker is safe to remove.
                self.store.clear_pending(task.key)
                raise
            try:
                self.api.submit(token, task, position)
            except Exception:
                pass  # Submission outcome is unknown until read-back; never retry the POST blindly.
            return self._readback(token, task)
        except CheckinError as exc:
            return self._result(exc.state, str(exc), task)
        except Exception:
            return self._result('error', '操作未完成，请检查网络、配置或稍后重试', task)
        finally:
            self.lock.release()

    def _check_cancel(self):
        if self.cancel.is_set():
            raise CheckinError('cancelled', '操作已取消，未提交')

    def _readback(self, token, task):
        try:
            signed = self.api.is_signed(token, task)
        except Exception:
            signed = False
        if signed:
            self.store.clear_pending(task.key)
            return self._result('signed', '服务器已确认今日任务完成', task)
        return self._result('uncertain', '提交结果待确认；将仅回查，请在学校页面核实', task)

    def _result(self, state, message, task=None):
        result = Result(state, message, task, self.clock().isoformat(timespec='seconds'))
        try:
            self.store.record(result)
        except OSError:
            if state != 'signed':
                return Result('error', '无法写入打卡状态，请检查本地目录权限', task)
        return result
