"""Render integrated UI with synthetic data; never logs in, locates, or submits."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import ctypes
import tkinter as tk
from unittest.mock import patch

from PIL import ImageGrab
from dorm_checkin import Result, Store, Task
from campus_auth_gui import CampusAuthGui


def capture(widget, path):
    widget.update_idletasks()
    widget.lift()
    widget.update()
    hwnd = ctypes.windll.user32.GetParent(widget.winfo_id())
    ImageGrab.grab(window=hwnd).save(path)


def main():
    output = Path(__file__).resolve().parents[1] / 'build' / 'qa'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='youziauth-preview-') as directory:
        root = tk.Tk()
        with patch('campus_auth_gui.is_startup_enabled', return_value=False), \
             patch('campus_auth_gui.legacy_config_paths', return_value=[]), \
             patch.object(CampusAuthGui, '_start_ui_command_server'), \
             patch.object(CampusAuthGui, '_ensure_tray_icon', return_value=False), \
             patch.object(CampusAuthGui, '_show_notification'):
            app = CampusAuthGui(root, Path(directory)/'config.ini', dorm_store=Store(Path(directory)/'dorm'))
            root.geometry('1080x680+30+30')
            root.update()
            root.after(600, lambda: capture(root, output/'main.png'))

            def panel():
                app.open_dorm()
                window = app.dorm_panel.window
                window.geometry('700x650+100+50')
                app.dorm_panel.update(Result('ready', '今日任务待完成，可提交打卡（演示数据）', Task(
                    'fixture', 'form', 'publish', 'fixture-student', '2026-09-21',
                    '晚间查寝 · 演示任务', '21:00', '23:30', False, '示例宿舍', '800米')))
                root.after(600, lambda: capture(window, output/'dorm-panel.png'))
                root.after(900, validate)

            def validate():
                window = app.dorm_panel.window
                for button in app.dorm_panel.buttons:
                    assert button.winfo_ismapped(), 'Action button hidden'
                    assert button.winfo_rooty()+button.winfo_height() <= window.winfo_rooty()+window.winfo_height()
                app.dorm_panel.window.withdraw()
                assert not app.dorm_controller.closed
                app.quit_application()

            root.after(900, panel)
            root.mainloop()
    print('Synthetic GUI preview passed:', output)


if __name__ == '__main__':
    main()
