"""Opt-in packaged smoke test. Temporary data and a local-only browser page."""
import json
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
