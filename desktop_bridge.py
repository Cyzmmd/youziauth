"""Narrow desktop UI bridge. School/network operations remain in their existing engines."""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import os
import threading
from pathlib import Path

import agent_ipc
import campus_auth
import campus_auth_gui as gui
import windows_notifications
from dorm_checkin import Settings
from dorm_panel import DormController
from dorm_location import probe_location


def network_settings(payload, previous):
    username = str(payload.get('username', '')).strip()
    if not username or username == 'YOUR_STUDENT_ID':
        raise ValueError('请填写你的校园网账号')
    interval = int(payload.get('interval', 60))
    if not 5 <= interval <= 3600:
        raise ValueError('检测间隔应为 5–3600 秒')
    if type(payload.get('startup', False)) is not bool:
        raise ValueError('开机自启动设置无效')
    return gui.GuiSettings(username, str(payload.get('password', '')), interval, previous.log_file)


def dorm_settings(payload):
    return Settings(payload.get('enabled', False), str(payload.get('start', '')),
                    str(payload.get('end', '')), int(payload.get('interval', 300))).validate()


class LocationProbe:
    def _init_location(self):
        self._ui_dispatch = None
        self._location_gate = threading.Lock()
        self._location_worker = None
        self._location_state = {'state':'idle', 'message':'点击下方按钮请求系统授权并检测实时定位，不会提交打卡。',
                                'accuracy':None, 'checked':''}

    def _location_snapshot(self):
        return dict(self._location_state, busy=self._location_gate.locked())

    def _start_location_probe(self):
        if self._ui_dispatch is None:
            raise RuntimeError('请在桌面主窗口中使用定位授权功能。')
        if not self._location_gate.acquire(blocking=False):
            raise RuntimeError('正在等待授权或定位结果，请稍候。')
        self._location_state = {'state':'checking','message':'请在系统提示中选择允许，随后等待实时定位（约 30 秒内）。',
                                'accuracy':None,'checked':''}
        def work():
            try:
                self._location_state = probe_location(ui_dispatch=self._ui_dispatch)
            except Exception:
                self._location_state = {'state':'error','message':'定位检测未完成，请保持主窗口在前台后重试。',
                                        'accuracy':None,'checked':''}
            finally:
                self._location_gate.release()
        self._location_worker = threading.Thread(target=work,daemon=True,name='youziauth-location-probe')
        self._location_worker.start()
        return '正在请求系统定位授权；本次只检测定位，不会提交打卡。'


class DesktopBridge(LocationProbe):
    def __init__(self, config_path=None, dorm=None, startup_mode=False):
        self._init_location()
        self._config = gui.ensure_user_config(config_path or gui.DEFAULT_CONFIG_PATH)
        self._dorm = dorm or DormController()
        self._lock = threading.RLock()
        self._mutation = threading.Lock()
        self._network_gate = threading.Lock()
        self._stop = threading.Event()
        self._closed = threading.Event()
        self._worker = None
        self._monitoring = False
        self._startup_mode = startup_mode
        self._agent = gui.is_startup_enabled()
        self._state = 'stopped'
        self._message = '等待检测校园网连接'
        self._checked = ''
        self._notice = None
        self._notification_tracker = windows_notifications.NotificationTracker()
        self._last_agent = None
        self._window_action = lambda action: None

    def snapshot(self):
        """Read only. Never expose passwords, tokens, coordinates or raw school payloads."""
        with self._lock:
            settings = gui.load_gui_settings(self._config)
            ds = self._dorm.store.settings()
            result = self._dorm.latest
            task = result.task
            return {
                'preview': False,
                'location': self._location_snapshot(),
                'network': {
                    'username': '' if settings.username == 'YOUR_STUDENT_ID' else settings.username,
                    'interval': settings.check_interval_seconds, 'startup': self._agent,
                    'monitoring': self._monitoring or self._agent,
                    'busy': self._network_gate.locked(), 'state': self._state,
                    'message': self._message, 'checked': self._checked,
                    'has_password': (self._config.parent / 'credential.dat').exists(),
                },
                'dorm': {
                    'state': result.state, 'message': result.message,
                    'busy': self._dorm.busy, 'settings': dataclasses.asdict(ds),
                    'schedule': self._dorm.schedule_text(),
                    'task': ({k: getattr(task, k) for k in
                              ('title', 'date', 'start', 'end', 'address', 'signed')} if task else None),
                },
                'logs': {
                    'network': gui.tail_log(gui.resolve_log_path(self._config, settings.log_file)),
                    'dorm': self._dorm.store.history(),
                },
            }

    def dispatch(self, action, payload=None):
        if not isinstance(payload, (dict, type(None))):
            return {'ok': False, 'message': '设置格式无效'}
        if self._closed.is_set():
            return {'ok': False, 'message': '程序正在退出'}
        with self._mutation:
            try:
                message = self._dispatch(action, payload or {})
                return {'ok': True, 'message': message or '操作已完成'}
            except (ValueError, TypeError):
                return {'ok': False, 'message': '请检查填写内容：账号不能为空，时间须为 HH:MM，间隔须在允许范围内。'}
            except RuntimeError as exc:
                return {'ok': False, 'message': str(exc)}
            except Exception:
                # Do not send raw OS/network exceptions across the JS bridge.
                return {'ok': False, 'message': '操作未完成，请检查本机权限、配置和网络后重试。'}

    def _dispatch(self, action, payload):
        if action == 'location_authorize':
            return self._start_location_probe()
        if action == 'network_save':
            settings = network_settings(payload, gui.load_gui_settings(self._config))
            startup = payload.get('startup', False)
            if startup != self._agent and (self._monitoring or self._network_gate.locked()):
                raise RuntimeError('请先停止本地后台检测并等待当前检测完成，再更改开机自启动。')
            gui.save_gui_settings(self._config, settings)
            if startup != self._agent:
                gui.set_startup_enabled(startup)
                self._agent = gui.is_startup_enabled()
                if self._agent != startup:
                    raise RuntimeError('账号已保存；开机自启动未变更，请完成 Windows 管理员授权后重试。')
            if self._agent:
                self._agent_command('reload-config')
            return '校园网设置已保存'
        if action in ('network_check', 'network_start'):
            if self._agent:
                self._agent_command('retry' if action == 'network_check' else 'reload-config')
                return '已请求系统认证代理执行'
            if self._monitoring or not self._network_gate.acquire(blocking=False):
                raise RuntimeError('校园网检测正在运行，请等待完成或先停止后台检测。')
            try:
                config = gui.build_auth_config(self._config)
                if not config.username or config.username == 'YOUR_STUDENT_ID':
                    raise ValueError('请先保存校园网账号')
            except Exception:
                self._network_gate.release()
                raise
            self._stop.clear()
            self._monitoring = action == 'network_start'
            self._state, self._message = 'checking', '正在检测校园网连接…'
            self._worker = threading.Thread(target=self._network_work,
                                            args=(config, self._monitoring), daemon=True)
            self._worker.start()
            return '已开始后台检测' if self._monitoring else '正在检测，请稍候'
        if action == 'network_stop':
            if self._agent:
                raise RuntimeError('系统代理由开机自启动管理；如需关闭，请取消开机自启动并保存。')
            self._stop.set()
            return '正在停止，当前网络请求结束后生效'
        if action in ('dorm_login', 'dorm_query', 'dorm_submit', 'dorm_logout'):
            if not self._dorm.start(action.removeprefix('dorm_')):
                raise RuntimeError('打卡操作正在进行，请等待完成或取消当前操作。')
            return '操作已开始，请查看任务状态'
        if action == 'dorm_cancel':
            self._dorm.cancel()
            return '已请求取消；已发出的提交不能撤回，自动打卡设置不会改变。'
        if action == 'dorm_save':
            if self._dorm.busy:
                raise RuntimeError('请等待当前打卡操作完成后再保存设置。')
            self._dorm.save(dorm_settings(payload))
            return '自动打卡设置已保存'
        if action == 'location_settings':
            os.startfile('ms-settings:privacy-location')
            return '已打开 Windows 定位设置'
        if action in ('hide', 'quit'):
            self._window_action(action)
            return '已隐藏到托盘' if action == 'hide' else '正在退出'
        raise RuntimeError('不支持的操作')

    def _agent_command(self, command):
        agent_ipc.send_command('youziauth-agent', agent_ipc.AgentCommand(command), timeout_ms=3000)

    def _network_work(self, config, monitor):
        attempt = 0
        try:
            while not self._closed.is_set():
                logger = campus_auth.configure_logging(config.log_file, verbose=False)
                ok = campus_auth.run_once(campus_auth.CampusAuthClient(config, logger), logger)
                with self._lock:
                    self._state = 'online' if ok else 'offline'
                    self._message = '网络连接正常' if ok else '认证未成功，请检查账号与网络'
                    self._checked = dt.datetime.now().strftime('%H:%M:%S')
                if not monitor:
                    break
                delay = gui.next_monitor_delay(ok, config.check_interval_seconds, attempt, self._startup_mode)
                attempt = 0 if ok else attempt + 1
                if self._stop.wait(delay):
                    break
                config = gui.build_auth_config(self._config)
        except Exception:
            with self._lock:
                self._state, self._message = 'error', '网络检测未完成，请检查设置后重试'
        finally:
            with self._lock:
                self._monitoring = False
                if self._stop.is_set():
                    self._state, self._message = 'stopped', '后台检测已停止'
            self._network_gate.release()

    def _tick(self):
        self._dorm.poll()
        for result in self._dorm.drain():
            notice = (result.at[:10], result.task.key if result.task else '', result.state)
            if result.state in ('signed', 'login_required', 'location_required', 'uncertain', 'error', 'network_error') and notice != self._notice:
                self._notice = notice
                windows_notifications.show_toast(windows_notifications.build_dorm_toast(result.message))
        if self._agent:
            try:
                snapshot = agent_ipc.read_snapshot(self._config.parent / 'runtime.json')
                state = {'online_external':'online', 'online_campus':'online',
                         'waiting_for_network':'checking', 'auth_failed':'offline'}.get(snapshot.state, 'error')
                with self._lock:
                    self._state, self._message = state, snapshot.detail or snapshot.state
                    self._last_agent = snapshot
                toast = self._notification_tracker.evaluate(snapshot)
                if toast:
                    windows_notifications.show_toast(toast)
            except (OSError, ValueError):
                self._state, self._message = 'checking', '等待系统认证代理…'

    def _close(self):
        self._closed.set()
        self._stop.set()
        self._dorm.close()


class PreviewBridge(LocationProbe):
    """Explicit in-memory demo. No real controllers, credentials, timers or system actions."""
    def __init__(self, real_location=False):
        self._init_location()
        self._real_location = real_location
        self._window_action = lambda action: None
        self._data = {
            'preview': True,
            'network': {'username':'2026000000', 'interval':60, 'startup':False,
                        'monitoring':False, 'busy':False, 'state':'stopped',
                        'message':'尚未检测，点击即可查看连接状态', 'checked':'', 'has_password':True},
            'dorm': {'state':'idle', 'message':'查询今日任务，开始今晚的安排', 'busy':False,
                     'settings':dataclasses.asdict(Settings()), 'schedule':'自动打卡：关闭', 'task':None},
            'logs': {'network':'', 'dorm':''},
        }

    def snapshot(self):
        return dict(copy.deepcopy(self._data), location=self._location_snapshot(),
                    location_diagnostic=self._real_location)

    def dispatch(self, action, payload=None):
        payload = payload or {}
        n, d = self._data['network'], self._data['dorm']
        try:
            if action == 'location_authorize':
                if self._real_location:
                    try:
                        return {'ok':True,'message':self._start_location_probe()}
                    except RuntimeError as exc:
                        return {'ok':False,'message':str(exc)}
                self._location_state = {'state':'ready','message':'定位检测通过（演示）；未读取真实位置。',
                                        'accuracy':50,'checked':'演示'}
            elif action == 'network_save':
                value = network_settings(payload, gui.GuiSettings())
                n.update(username=value.username, interval=value.check_interval_seconds, startup=payload['startup'])
            elif action in ('network_check', 'network_start'):
                n.update(state='online', message='网络连接正常（演示）', checked=dt.datetime.now().strftime('%H:%M:%S'))
                n['monitoring'] = action == 'network_start'
                self._data['logs']['network'] += '演示 · 校园网连接检测完成\n'
            elif action == 'network_stop':
                n.update(monitoring=False, state='stopped', message='后台检测已停止（演示）')
            elif action == 'dorm_save':
                d['settings'] = dataclasses.asdict(dorm_settings(payload))
                d['schedule'] = '自动打卡：' + ('开启（演示）' if d['settings']['enabled'] else '关闭')
            elif action == 'dorm_login':
                d.update(state='logged_in', message='学校登录成功（演示，不会打开真实认证）')
            elif action in ('dorm_query', 'dorm_submit'):
                signed = action == 'dorm_submit'
                d.update(state='signed' if signed else 'ready', message='今日打卡已完成（演示）' if signed else '今日任务待完成（演示）',
                         task={'title':'晚间寝室打卡', 'date':dt.date.today().isoformat(),
                               'start':'21:00', 'end':'23:30', 'address':'示例宿舍', 'signed':signed})
                self._data['logs']['dorm'] += '演示 · ' + d['message'] + '\n'
            elif action == 'dorm_logout':
                d.update(state='login_required', message='请重新登录学校账号', task=None)
                d['settings']['enabled'] = False
                d['schedule'] = '自动打卡：关闭'
            elif action in ('hide', 'quit'):
                self._window_action(action)
            elif action not in ('dorm_cancel', 'location_settings'):
                return {'ok':False, 'message':'不支持的操作'}
            return {'ok':True, 'message':'演示操作完成，未访问学校服务或修改真实设置'}
        except (ValueError, TypeError):
            return {'ok':False, 'message':'请检查账号、时间和间隔，结束时间须晚于开始时间。'}

    def _tick(self):
        pass

    def _close(self):
        pass
