"""Opt-in runtime smoke tests and local-only location sample replay."""
import argparse
import json
import math
import tempfile
import threading
from pathlib import Path


def run(output, online_login=False):
    result = {'ok': False, 'scope': 'offline runtime smoke; no school login/location/submission'}
    root = None
    controller = None
    try:
        import tkinter as tk
        from dorm_checkin import Store
        from dorm_panel import DormController, DormPanel
        from playwright.sync_api import sync_playwright
        from dorm_login import owned_browser, open_login_page
        with tempfile.TemporaryDirectory(prefix='youziauth-selftest-') as directory:
            store = Store(Path(directory))
            store.save_token('offline-selftest-only')
            if store.token() != 'offline-selftest-only':
                raise RuntimeError('Credential roundtrip failed')
            store.clear_token()
            result['dpapi'] = True
            root = tk.Tk()
            root.withdraw()
            controller = DormController(store)
            panel = DormPanel(root, controller)
            root.update()
            if not all(button.winfo_ismapped() for button in panel.buttons):
                raise RuntimeError('Dormitory controls not visible')
            panel.window.withdraw()
            result['tk'] = True
            with sync_playwright() as p, owned_browser(p, threading.Event()) as browser:
                    context = browser.contexts[0]
                    if online_login:
                        result['scope'] = 'school login page navigation only; no credentials or submission'
                        page = context.pages[0]
                        open_login_page(page, threading.Event())
                        page.locator('#loginName').wait_for(state='visible', timeout=10000)
                        page.locator('#password').wait_for(state='visible', timeout=10000)
                        result['school_login_page'] = True
                    context.route('**/*', lambda route: route.abort())
                    page = context.new_page()
                    page.set_content('<title>youziauth offline self-test</title><p>Ready</p>')
                    if page.title() != 'youziauth offline self-test':
                        raise RuntimeError('Browser driver failed')
                    result['playwright'] = True
            result['ok'] = True
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)[:400]
    finally:
        if controller is not None:
            controller.close()
        if root is not None:
            root.destroy()
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def location_main(argv=None):
    from dorm_checkin import CheckinError
    from dorm_location import simulate_location
    parser = argparse.ArgumentParser(description='仅在本地回放定位样本，不修改系统定位或调用学校接口。')
    parser.add_argument('sample', type=Path, help='原始 WGS84 定位样本 JSON 文件')
    parser.add_argument('--latitude', type=float, help='模拟的 WGS84 纬度')
    parser.add_argument('--longitude', type=float, help='模拟的 WGS84 经度')
    parser.add_argument('--accuracy', type=float, help='模拟精度（米），默认保留采样精度')
    parser.add_argument('--timestamp', type=float, help='模拟 Unix 时间戳（秒），默认使用当前时间')
    args = parser.parse_args(argv)
    result = {'ok': False, 'scope': 'local-only simulation'}
    try:
        sample = json.loads(args.sample.read_text(encoding='utf-8'))
        captured_at = float(sample['timestamp'])
        if not math.isfinite(captured_at):
            raise ValueError('Invalid capture timestamp')
        position = simulate_location(sample, latitude=args.latitude, longitude=args.longitude,
                                     accuracy=args.accuracy, timestamp=args.timestamp)
        result.update(ok=True, captured_at=captured_at, source=sample['source'], position=position)
    except CheckinError as exc:
        result.update(state=getattr(exc, 'code', exc.state), message=str(exc))
    except (OSError, ValueError, TypeError, KeyError):
        result.update(state='invalid_sample', message='无法读取有效的本地定位样本，请检查文件及字段。')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(location_main())
