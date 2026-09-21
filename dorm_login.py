"""Interactive login in an owned browser, without password storage or OCR."""
import contextlib
import os
import re
import socket
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import quote, urlsplit, urlunsplit

from dorm_checkin import CheckinError

RETURN_URL = ('https://of.swu.edu.cn/gateway/fighter-middle/api/integrate/uaap/cas/'
              'resolve-cas-return?next=' + quote('https://of.swu.edu.cn/#/casLogin?from=%2FappCenter', safe=''))
LOGIN_URL = 'https://of.swu.edu.cn/cas/oauth/login/SWU_CAS2_FEDERAL?service=' + quote(RETURN_URL, safe='')
INIT_URL = 'https://of.swu.edu.cn/cas/login?service=' + quote(RETURN_URL, safe='')


def check_cancel(cancel):
    if cancel.is_set():
        raise CheckinError('cancelled', '登录已取消')


def open_login_page(page, cancel):
    def navigate(url, stage):
        check_cancel(cancel)
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except Exception:
            raise CheckinError('login_required', f'{stage}未能加载，请检查网络后重新登录') from None
        if response is not None and response.status >= 400:
            raise CheckinError('login_required', f'{stage}返回 HTTP {response.status}，请重新登录')

    navigate(INIT_URL, '学校登录初始页')
    navigate(LOGIN_URL, '联邦认证中转页')
    url = urlsplit(page.url)
    if url.scheme == 'https' and url.hostname == 'uaaap.swu.edu.cn' and url.path == '/cas/login':
        # Same action as the school's _goLogin(), retaining all current session parameters.
        # CAS state URLs are opaque: re-encoding their query can invalidate the request.
        if re.search(r'(^|&)federalEnable=', url.query):
            query = re.sub(r'(^|&)federalEnable=[^&]*', r'\1federalEnable=true', url.query)
        else:
            query = url.query + ('&' if url.query else '') + 'federalEnable=true'
        navigate(urlunsplit(url._replace(query=query)), '统一认证登录页')
    final = urlsplit(page.url)
    if final.scheme != 'https' or final.hostname not in ('idm.swu.edu.cn', 'of.swu.edu.cn'):
        raise CheckinError('login_required', '未能进入学校统一认证登录页，请重新登录')
    check_cancel(cancel)


def browser_executable():
    candidates = []
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / 'Google/Chrome/Application/chrome.exe')
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / 'Microsoft/Edge/Application/msedge.exe')
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise CheckinError('error', '请先安装 Google Chrome 或 Microsoft Edge')


@contextlib.contextmanager
def owned_browser(playwright, cancel):
    """Match upstream's native browser session, with an isolated profile/ephemeral CDP port."""
    check_cancel(cancel)
    executable = browser_executable()
    with tempfile.TemporaryDirectory(prefix='youziauth-login-', ignore_cleanup_errors=True) as profile:
        # Use an explicit free port, as in upstream's ordinary Chrome launch.
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        process = subprocess.Popen([
            str(executable), f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1',
            f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check', 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        browser = None
        try:
            deadline = time.monotonic() + 20
            while True:
                check_cancel(cancel)
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise CheckinError('error', '浏览器启动失败，请关闭本次登录窗口后重试')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    pass
                cancel.wait(.1)
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=10000)
            yield browser
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def extract_token(response):
    try:
        url = urlsplit(response.url)
        if (url.scheme != 'https' or url.hostname != 'of.swu.edu.cn' or url.port not in (None, 443)
                or not url.path.startswith('/gateway/') or 'exchange-token' not in url.path
                or response.status != 200):
            return None
        value = response.headers.get('fighter-auth-token')
        if not value:
            body = response.json()
            value = body.get('data') if isinstance(body, dict) and body.get('code') == 200 else None
        if isinstance(value, str) and value.strip() and not any(c in value for c in '\r\n'):
            return value
    except Exception:
        pass
    return None


def login(store, api, cancel, timeout=300):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CheckinError('error', '缺少登录组件，请安装 requirements-dorm.txt 中的依赖或使用完整安装包') from None
    if cancel.is_set():
        raise CheckinError('cancelled', '登录已取消')
    try:
        with sync_playwright() as playwright, owned_browser(playwright, cancel) as browser:
            context = browser.contexts[0]
            token = []

            def capture(response):
                value = extract_token(response)
                if value and not token:
                    token.append(value)

            context.on('response', capture)
            page = context.pages[0] if context.pages else context.new_page()
            open_login_page(page, cancel)
            deadline = time.monotonic() + timeout
            while not token and time.monotonic() < deadline:
                if cancel.is_set():
                    raise CheckinError('cancelled', '登录已取消')
                if not context.pages:
                    raise CheckinError('cancelled', '登录窗口已关闭')
                context.pages[0].wait_for_timeout(200)
            if not token:
                raise CheckinError('login_required', '登录等待超时，请重试')
            student = api.user(token[0])
            if cancel.is_set():
                raise CheckinError('cancelled', '登录已取消')
            store.save_token(token[0])
            return student
    except CheckinError:
        raise
    except Exception:
        raise CheckinError('login_required', '登录未完成，请重新打开登录窗口') from None
