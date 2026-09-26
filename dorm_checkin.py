"""User-session dormitory workflow, with conservative submission recovery."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import os
import re
import threading
import time
from pathlib import Path

from windows_credentials import CredentialStore, DpapiProtector, atomic_write_bytes

import dorm_points

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
LOGIN_RECORD_PREFIX = 'youziauth-session-v1\n'

# A submission the school never recorded may be repeated, but only once the read-back says
# the task is still unsigned. That keeps the write-ahead marker meaning what it says - an
# unknown outcome stays readback-only - so a repeat can never duplicate a recorded check-in.
SUBMIT_COOLDOWN_SECONDS = 60
SUBMIT_MAX_ATTEMPTS = 3
SUBMIT_EXHAUSTED_MESSAGE = (f'提交未生效，自动重试已达 {SUBMIT_MAX_ATTEMPTS} 次上限；'
                            '请在学校页面核实或手动打卡')


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
    end: str = '23:15'
    interval: int = 300
    location_source: str = 'windows'

    def validate(self):
        if type(self.enabled) is not bool:
            raise ValueError('自动打卡开关无效')
        if self.location_source not in ('windows', 'simulation'):
            raise ValueError('定位来源无效')
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
    # The school's own check-in point, in the GCJ02 frame its forms use. Published task data,
    # like the address: the map picker centres on it and draws the acceptance radius.
    latitude: str = ''
    longitude: str = ''

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
            # utf-8-sig：容忍别人（记事本 / PowerShell 的 -Encoding utf8）写进来的 BOM。
            # 实测教训：一个 BOM 会让 json.loads 抛 JSONDecodeError，而它不被下面的
            # FileNotFoundError 捕获，异常一路冒到界面同步循环，整个面板显示
            # 「界面暂时无法连接后台」—— 见 test_dorm_settings_robustness.py。
            #
            # 注意这里**只**容忍 BOM，不容忍真正损坏的 JSON：
            # 配置读不动必须继续 fail-closed 并显式报错（见 test_corrupt_settings_fail_closed），
            # 绝不能静默降级成默认值——那等于悄悄关掉自动打卡而不告诉用户。
            return json.loads((self.root / name).read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            return default

    def _write(self, name, data):
        atomic_write_bytes(self.root / name, json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def settings(self):
        return Settings(**self._read('settings.json', {})).validate()

    def save_settings(self, settings):
        self._write('settings.json', dataclasses.asdict(settings.validate()))

    def _login_record(self):
        if not (self.root / 'session' / 'credential.dat').exists():
            return {'token': ''}
        try:
            value = self._credentials().load_password()
            # Legacy tokens cannot contain a newline, so the record prefix is unambiguous.
            if not value.startswith(LOGIN_RECORD_PREFIX):
                return {'token': value}
            session = json.loads(value[len(LOGIN_RECORD_PREFIX):])
            if (not isinstance(session, dict) or not isinstance(session.get('token'), str)
                    or not isinstance(session.get('student'), str) or not session['student']
                    or not isinstance(session.get('cookies'), list)):
                raise ValueError('Invalid login record')
            return session
        except Exception:
            raise CheckinError('login_required', '登录凭据无法解密，请重新登录') from None

    def token(self):
        return self._login_record()['token']

    def save_token(self, token):
        if not isinstance(token, str) or not token.strip() or any(c in token for c in '\r\n'):
            raise ValueError('登录凭据格式无效')
        self._credentials().save_password(token)

    def clear_token(self):
        (self.root / 'session' / 'credential.dat').unlink(missing_ok=True)

    def browser_session(self, token):
        if not token:
            return None
        session = self._login_record()
        return session if session['token'] == token and 'cookies' in session else None

    def save_browser_session(self, token, student, cookies):
        session = dict(token=token, student=student, cookies=cookies)
        self._credentials().save_password(LOGIN_RECORD_PREFIX + json.dumps(session, ensure_ascii=False))

    def clear_browser_session(self):
        token = self.token()
        if token:
            self.save_token(token)

    def signed_record(self):
        """已被服务器确认完成打卡的那一天（返回 {'date','student'}，没有则空 dict）。

        用途：用户要求「一天成功过一次，后续就不用再判定」，见 Engine._already_signed_today。
        """
        value = self._read('signed.json', {})
        return value if isinstance(value, dict) else {}

    def mark_signed(self, date, student=''):
        """记录「这一天已确认完成打卡」。

        不会用空学号覆盖已有的完整记录：免重复判定走的是缓存路径（拿不到 task），
        若无条件覆盖会把学号抹掉，学号保护也就失效了。
        """
        if not isinstance(date, str) or not date:
            return
        current = self.signed_record()
        if current.get('date') == date and current.get('student') and not student:
            return
        self._write('signed.json', {'date': date, 'student': student or ''})

    def daily_attempts(self, date, name):
        """取「某天某个计数器的值」，用于给自动行为设每天的次数上限。"""
        value = self._read('daily.json', {})
        if not isinstance(value, dict):
            return 0
        bucket = value.get(date)
        if not isinstance(bucket, dict):
            return 0
        count = bucket.get(name)
        return count if isinstance(count, int) and count >= 0 else 0

    def bump_daily_attempts(self, date, name):
        """把某天某个计数器 +1 并返回新值。

        只保留 date 当天及更晚的桶，避免文件无界增长。
        """
        value = self._read('daily.json', {})
        if not isinstance(value, dict):
            value = {}
        bucket = value.get(date)
        if not isinstance(bucket, dict):
            bucket = {}
        bucket[name] = self.daily_attempts(date, name) + 1
        keep = {key: item for key, item in value.items() if isinstance(key, str) and key >= date}
        keep[date] = bucket
        self._write('daily.json', keep)
        return bucket[name]

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
        keys = self._pending_keys()
        if key in keys:
            self._write('pending.json', [k for k in keys if k != key])

    def _attempt_records(self):
        value = self._read('submit-attempts.json', {})
        if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(record, dict)
                and isinstance(record.get('count'), int) and isinstance(record.get('at'), (int, float))
                for key, record in value.items()):
            raise ValueError('提交尝试记录损坏')
        return value

    def attempts(self, key):
        record = self._attempt_records().get(key) or {}
        return record.get('count', 0), record.get('at', 0.0)

    def record_attempt(self, key, at):
        # Only today's task is ever retried, so an older entry is dropped instead of kept
        # as a permanent record of every submission this machine has made.
        self._write('submit-attempts.json', {key: {'count': self.attempts(key)[0] + 1, 'at': at}})

    def clear_attempts(self, key):
        if key in self._attempt_records():
            self._write('submit-attempts.json', {})

    def record(self, result):
        self._write('status.json', dataclasses.asdict(result))
        # Only controlled messages; no raw exceptions, token, response bodies or coordinates.
        old = self.history().splitlines()[-99:]
        old.append(f'{result.at} [{result.state}] {result.message}')
        atomic_write_bytes(self.root / 'history.log', ('\n'.join(old) + '\n').encode('utf-8'))

    def history(self):
        try:
            # utf-8-sig：与 _read 同理，容忍外部工具写入的 BOM
            # （BOM 会让第一行带上 \ufeff，虽不影响阅读，但读取失败会连累整个快照）。
            return (self.root / 'history.log').read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            return ''

    def sample(self):
        """The stored simulation point, or None when it is absent or unreadable."""
        try:
            value = self._read('location-sample.json', None)
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def save_sample(self, sample):
        """Point the replay at one sample.

        Nothing is backed up: every candidate position lives in the point list, so switching,
        re-picking or renaming never destroys the previous one.
        """
        self._write('location-sample.json', sample)

    def clear_sample(self):
        (self.root / 'location-sample.json').unlink(missing_ok=True)

    def drop_legacy_backup(self):
        """1.5.2 kept a one-level undo file; the point list replaced it."""
        (self.root / 'location-sample.previous.json').unlink(missing_ok=True)

    def points(self):
        """Named simulation points; an unreadable list degrades to empty, never fatal."""
        try:
            value = self._read('location-points.json', None)
        except (OSError, ValueError):
            return dorm_points.empty()
        return dorm_points.normalize(value)

    def save_points(self, state):
        self._write('location-points.json', dorm_points.normalize(state))


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
            # ★ 当天已确认完成 → 当天不再判定（用户要求：一天成功过一次，后续不用再判）。
            #
            # 位置很关键：必须在**任何网络调用之前**。这样打卡成功的当天既不再打学校接口，
            # 也不会因为会话过期而走自动续期登录 —— 那正是之前每 5 分钟无人值守拉起一次
            # 浏览器、连续数小时、成功率为 0 的那个循环。
            #
            # 只作用于自动检查：人工「查询今日任务」仍然真实查询，方便随时核实。
            if automatic and self._already_signed_today(current):
                return self._result('signed', '今日打卡已完成，当天不再重复检查')
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
                self.store.clear_pending(task.key)
                self.store.clear_attempts(task.key)
                return self._result('signed', '服务器已确认今日任务完成', task)
            if self.store.pending(task.key):
                settled = self._readback(token, task)
                if settled is not None:
                    return settled
                # The school answered for this task and it is still unsigned, so the marker
                # has done its job: nothing was recorded and a repeat cannot duplicate it.
            if phase != 'ready':
                return self._result(phase, '任务尚未开始' if phase == 'waiting' else '任务已过期，未提交', task)
            if not submit:
                return self._result('ready', '今日任务待完成，可提交打卡', task)
            allowed, blocked_state, blocked_message = self._submission_allowed(task, current, automatic)
            if not allowed:
                return self._result(blocked_state, blocked_message, task)
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
            self.store.record_attempt(task.key, int(current.timestamp()))
            reason = None
            try:
                recorded = self.api.submit(token, task, position) is True
            except CheckinError as exc:
                recorded, reason = False, exc
            except Exception:
                # The school's own words never reach the log; this stays a controlled message.
                recorded, reason = False, CheckinError('error', '操作未完成，请检查网络、配置或稍后重试')
            if recorded:
                self.store.clear_pending(task.key)
                self.store.clear_attempts(task.key)
                return self._result('signed', '服务器已确认今日任务完成', task)
            settled = self._readback(token, task)
            if settled is not None:
                return settled
            return self._unrecorded(task, reason)
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
        """Settle a submission whose outcome was lost.

        A Result means the question is answered or still unanswerable; None means the
        school confirms this task is unsigned, so nothing was recorded, the marker is
        cleared and a repeat submission can no longer create a duplicate.
        """
        try:
            signed = self.api.is_signed(token, task)
        except CheckinError as exc:
            if exc.state == 'login_required':
                return self._result('login_required', '登录已失效，提交结果待确认；恢复登录后仅回查', task)
            return self._result('uncertain', '提交结果待确认；将仅回查，请在学校页面核实', task)
        except Exception:
            return self._result('uncertain', '提交结果待确认；将仅回查，请在学校页面核实', task)
        if signed:
            self.store.clear_pending(task.key)
            self.store.clear_attempts(task.key)
            return self._result('signed', '服务器已确认今日任务完成', task)
        self.store.clear_pending(task.key)
        return None

    def _submission_allowed(self, task, current, automatic):
        """Bound unattended repeats. A manual submission is never blocked: the user asked
        for it, and every repeat still follows a read-back that rules out a duplicate."""
        if not automatic:
            return True, '', ''
        count, last = self.store.attempts(task.key)
        if count >= SUBMIT_MAX_ATTEMPTS:
            return False, 'uncertain', SUBMIT_EXHAUSTED_MESSAGE
        remaining = SUBMIT_COOLDOWN_SECONDS - (current.timestamp() - last)
        if last and remaining > 0:
            return False, 'ready', f'上次提交未生效，{math.ceil(remaining)} 秒后可重试；本次未提交'
        return True, '', ''

    def _unrecorded(self, task, reason):
        """A repeatable failure: say what the school said, and that another try is coming."""
        if reason is not None and reason.state in ('login_required', 'location_required'):
            # These need the user, not another POST.
            return self._result(reason.state, f'{reason}；本次提交未记录', task)
        detail = f'{reason}；' if reason is not None else '服务器未记录本次提交；'
        return self._result('ready', f'提交未生效：{detail}将在检查时段内重试', task)

    def _result(self, state, message, task=None):
        result = Result(state, message, task, self.clock().isoformat(timespec='seconds'))
        if state == 'signed':
            self._mark_signed(task)
        try:
            self.store.record(result)
        except OSError:
            if state != 'signed':
                return Result('error', '无法写入打卡状态，请检查本地目录权限', task)
        return result

    def _mark_signed(self, task):
        """记下「今天已完成」，供当天后续的自动检查免重复判定。

        放在 _result 这个**唯一出口**上，保证任何产生 signed 的路径（直接判定、
        提交成功后、以及 readback 回查确认）都不会漏记。
        记录失败不影响本次结果：最坏情况只是后续仍会照常查询。
        """
        try:
            date = task.date if task is not None else self.clock().date().isoformat()
            student = task.student if task is not None else ''
            self.store.mark_signed(date, student)
        except Exception:  # noqa: BLE001
            pass

    def _already_signed_today(self, current):
        """今天是否已被服务器确认完成过打卡。

        学号保护：记录里的学号与会话绑定的学号若都能取到且**不一致**，就不走捷径 ——
        同一台机器换账号后，不能把上一个账号的「今天已完成」误判成本账号的。
        任一侧取不到时无法证伪，沿用记录（宁可少查一次，也不重复打扰学校接口）。
        """
        try:
            record = self.store.signed_record()
        except Exception:  # noqa: BLE001
            return False
        if record.get('date') != current.date().isoformat():
            return False
        recorded = record.get('student') or ''
        if not recorded:
            return True
        try:
            session = self.store.browser_session(self.store.token())
        except Exception:  # noqa: BLE001
            return True
        bound = (session or {}).get('student') or ''
        return not bound or bound == recorded
