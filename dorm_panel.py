"""Independent dormitory UI and worker lifecycle; no network-agent commands."""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import queue
import threading
import time

from dorm_api import SwuApi
from dorm_checkin import CheckinError, Engine, Result, Settings, Store, now, parse_time
from dorm_location import locate
from dorm_login import login


class DormController:
    def __init__(self, store=None, engine=None):
        self.store = store or Store()
        self.api = SwuApi()
        self.engine = engine or Engine(self.store, self.api, self._locate)
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self._gate = threading.Lock()
        self._poll_at = 0.0
        self.latest = Result('idle', '尚未查询今日任务')

    def _locate(self):
        return locate(self.store.settings().location_source, self.store.root / 'location-sample.json')

    def start(self, action):
        if action not in ('query', 'submit', 'login', 'logout', 'automatic'):
            return False
        with self._gate:
            if self.closed or self.busy:
                return False
            self.busy = True
            self.engine.cancel.clear()

        def work():
            try:
                if action == 'login':
                    login(self.store, self.api, self.engine.cancel)
                    result = Result('logged_in', '登录成功，凭据已在本机加密保存')
                elif action == 'logout':
                    settings = self.store.settings()
                    self.store.save_settings(dataclasses.replace(settings, enabled=False))
                    self.store.clear_token()
                    result = Result('login_required', '登录凭据已清除，自动打卡已关闭')
                elif action == 'automatic':
                    result = self.engine.tick()
                else:
                    result = self.engine.run(submit=action == 'submit')
                if result is not None and result.state not in ('disabled', 'waiting'):
                    self.events.put(result)
                elif result is not None and action != 'automatic':
                    self.events.put(result)
            except CheckinError as exc:
                self.events.put(Result(exc.state, str(exc)))
            except Exception:
                self.events.put(Result('error', '打卡操作未完成，请检查配置和网络后重试'))
            finally:
                with self._gate:
                    self.busy = False

        threading.Thread(target=work, name='youziauth-dorm-' + action, daemon=True).start()
        return True

    def poll(self):
        if self.closed or time.monotonic() < self._poll_at:
            return
        self._poll_at = time.monotonic() + 5
        try:
            settings = self.store.settings()
            if settings.enabled:
                self.start('automatic')
        except Exception:
            if self.latest.state != 'error':
                self.events.put(Result('error', '打卡设置无法读取，请打开寝室打卡设置重新保存'))

    def drain(self):
        values = []
        while True:
            try:
                self.latest = self.events.get_nowait()
                values.append(self.latest)
            except queue.Empty:
                return values

    def save(self, settings):
        with self._gate:
            if self.closed or self.busy:
                raise RuntimeError('请等待当前打卡操作完成后再保存设置。')
            self.store.save_settings(settings)
            self.engine.next_tick = 0
            self._poll_at = 0

    def cancel(self):
        self.engine.cancel.set()

    def close(self):
        with self._gate:
            self.closed = True
            self.cancel()

    def schedule_text(self):
        try:
            s = self.store.settings()
            if not s.enabled:
                return '自动打卡：关闭'
            current = now()
            start, end = parse_time(s.start), parse_time(s.end)
            if current.time() < start:
                due = current.replace(hour=start.hour, minute=start.minute, second=0)
            elif current.time() >= end:
                due = (current + dt.timedelta(days=1)).replace(hour=start.hour, minute=start.minute, second=0)
            else:
                due = current + dt.timedelta(seconds=max(0, self.engine.next_tick-time.monotonic()))
                if due.time() >= end:
                    due = (current + dt.timedelta(days=1)).replace(hour=start.hour, minute=start.minute, second=0)
            return f'自动打卡：开启 · 下次检查 {due:%m-%d %H:%M}（北京时间）'
        except Exception:
            return '自动打卡：配置无效'


class DormPanel:
    def __init__(self, parent, controller):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.controller = controller
        self.window = tk.Toplevel(parent)
        self.window.title('youziauth · 寝室打卡')
        self.window.geometry('700x650')
        self.window.minsize(660, 630)
        self.window.protocol('WM_DELETE_WINDOW', self.window.withdraw)
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text='寝室打卡', font=('Microsoft YaHei UI', 16, 'bold')).grid(row=0, sticky='w')
        ttk.Label(frame, text='使用学校统一身份认证登录，与校园网账号设置分开。', wraplength=620).grid(row=1, sticky='w', pady=(6, 12))
        self.state = tk.StringVar(value=controller.latest.message)
        self.task_text = tk.StringVar(value='今日任务：尚未查询')
        self.schedule = tk.StringVar()
        ttk.Label(frame, textvariable=self.state, wraplength=620).grid(row=2, sticky='w', pady=4)
        ttk.Label(frame, textvariable=self.task_text, wraplength=620, justify='left').grid(row=3, sticky='w', pady=4)
        ttk.Label(frame, textvariable=self.schedule, wraplength=620).grid(row=4, sticky='w', pady=(4, 12))

        actions = ttk.Frame(frame)
        actions.grid(row=5, sticky='ew')
        self.buttons = []
        for column, (label, action) in enumerate((('登录 / 重新登录', 'login'), ('查询今日任务', 'query'), ('提交今日打卡', 'submit'))):
            button = ttk.Button(actions, text=label, command=lambda a=action: self.act(a))
            button.grid(row=0, column=column, padx=(0, 8), pady=4)
            self.buttons.append(button)
        ttk.Button(actions, text='取消当前操作', command=controller.cancel).grid(row=1, column=0, sticky='w', pady=4)
        logout = ttk.Button(actions, text='清除登录凭据', command=lambda: self.act('logout'))
        logout.grid(row=1, column=1, sticky='w', pady=4)
        self.buttons.append(logout)
        ttk.Button(actions, text='Windows 定位设置', command=self.open_location_settings).grid(row=1, column=2, pady=4)

        options = ttk.LabelFrame(frame, text='自动打卡', padding=12)
        options.grid(row=6, sticky='ew', pady=12)
        self.enabled = tk.BooleanVar(value=False)
        self.start = tk.StringVar(value='21:00')
        self.end = tk.StringVar(value='23:30')
        self.interval = tk.StringVar(value='300')
        ttk.Checkbutton(options, text='启用自动打卡（保存后生效）', variable=self.enabled).grid(row=0, column=0, columnspan=4, sticky='w')
        ttk.Label(options, text='检查时段').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Entry(options, width=8, textvariable=self.start).grid(row=1, column=1, padx=5)
        ttk.Label(options, text='至').grid(row=1, column=2)
        ttk.Entry(options, width=8, textvariable=self.end).grid(row=1, column=3, padx=5)
        ttk.Label(options, text='间隔（秒）').grid(row=2, column=0, sticky='w')
        ttk.Entry(options, width=8, textvariable=self.interval).grid(row=2, column=1, padx=5)
        ttk.Button(options, text='保存打卡设置', command=self.save).grid(row=2, column=3, padx=5)
        self.location_text = tk.StringVar()
        ttk.Label(frame, textvariable=self.location_text,
                  wraplength=620, justify='left').grid(row=7, sticky='w', pady=(0, 10))
        self.history = tk.Text(frame, height=7, wrap='word', state='disabled', font=('Microsoft YaHei UI', 9))
        self.history.grid(row=8, sticky='nsew')
        frame.rowconfigure(8, weight=1)
        self._history_text = None
        self.load_settings()
        self.refresh()

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def load_settings(self):
        try:
            settings = self.controller.store.settings()
            self.enabled.set(settings.enabled)
            self.start.set(settings.start)
            self.end.set(settings.end)
            self.interval.set(str(settings.interval))
        except Exception:
            self.state.set('配置无法读取，请重新保存打卡设置')

    def save(self):
        from tkinter import messagebox
        try:
            try:
                previous = self.controller.store.settings()
            except (ValueError, TypeError, OSError):
                previous = Settings()
            settings = dataclasses.replace(previous, enabled=self.enabled.get(),
                                           start=self.start.get().strip(), end=self.end.get().strip(),
                                           interval=int(self.interval.get()))
            self.controller.save(settings)
            self.state.set('打卡设置已保存')
        except (ValueError, OSError):
            messagebox.showerror('无法保存', '请填写正确时段和 60–3600 秒间隔，并确认目录可写。', parent=self.window)
        except RuntimeError as exc:
            messagebox.showerror('无法保存', str(exc), parent=self.window)
        self.refresh()

    def act(self, action):
        if self.controller.start(action):
            self.state.set('请在新打开的浏览器中完成学校登录（最多等待 5 分钟）' if action == 'login' else '正在处理，请稍候…')
            self.refresh()

    def open_location_settings(self):
        try:
            os.startfile('ms-settings:privacy-location')
        except OSError:
            self.state.set('请手动打开 Windows 设置 → 隐私和安全性 → 位置')

    def update(self, result):
        self.state.set(result.message)
        if result.task:
            t = result.task
            self.task_text.set(f'今日任务：{t.title}\n日期：{t.date} · 学校时段：{t.start}–{t.end}\n地点：{t.address or "学校未提供地址"}')
        elif result.state in ('logged_in', 'login_required', 'no_task'):
            self.task_text.set('今日任务：请查询' if result.state != 'no_task' else '今日任务：暂无')
        if result.state == 'login_required':
            self.load_settings()
        self.refresh()

    def refresh(self):
        self.schedule.set(self.controller.schedule_text())
        try:
            source = self.controller.store.settings().location_source
            description = ('模拟定位（本机样本随机偏移，非实时，并非当前位置）' if source == 'simulation' else
                           'Windows 实时定位（由系统选择来源）')
            self.location_text.set(f'全局定位来源：{description}。检测和提交均沿用此选择，仍须通过学校范围校验。\n'
                                   '自动执行需电脑开机、登录 Windows，并保持软件运行；位置不可用或精度不足时停止。')
        except (ValueError, TypeError, OSError):
            self.location_text.set('定位来源配置不可用，请在主界面重新保存设置。')
        for button in self.buttons:
            button.configure(state='disabled' if self.controller.busy else 'normal')
        try:
            text = self.controller.store.history() or '暂无打卡记录'
        except OSError:
            text = '无法读取打卡记录'
        if text != self._history_text:
            self._history_text = text
            self.history.configure(state='normal')
            self.history.delete('1.0', 'end')
            self.history.insert('1.0', text)
            self.history.configure(state='disabled')
            self.history.see('end')
