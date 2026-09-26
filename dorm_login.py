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

# 登录过程中被认可的落点：学校认证站点，以及统一认证 CAS 页。
# 补上 uaaap 是因为实际落点就在这里（见 open_login_page 内的说明）。
TRUSTED_AUTH_HOSTS = ('idm.swu.edu.cn', 'of.swu.edu.cn', 'uaaap.swu.edu.cn')


def check_cancel(cancel):
    if cancel.is_set():
        raise CheckinError('cancelled', '登录已取消')


def open_login_page(page, cancel, authenticated=None):
    def navigate(url, stage):
        check_cancel(cancel)
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except Exception:
            raise CheckinError('network_error', f'{stage}未能加载，请检查网络后重试') from None
        if response is not None and response.status >= 400:
            state = 'login_required' if response.status in (401, 403) else 'network_error'
            raise CheckinError(state, f'{stage}返回 HTTP {response.status}，请稍后重试或手动登录')

    def awaiting_exchange():
        # page.url 解析不出来（页面正在被销毁 / 已关闭）时一律当作「还没收尾」：
        # 一个解析失败不该把整条登录流程打断。
        try:
            url = urlsplit(page.url)
            landed = (url.scheme == 'https' and url.hostname == 'of.swu.edu.cn'
                      and url.fragment.split('?')[0] == '/casLogin')
        except Exception:  # noqa: BLE001
            landed = False
        return bool((authenticated and authenticated()) or landed)

    # ★ 已经在收尾落点（应用页）或 token 已到手时**不要再导航**：每一次多余的导航
    # 都可能把正在走的跳转打断 —— 「第一次登录成功后页面被跳走」就是这么来的。
    if awaiting_exchange():
        return
    navigate(INIT_URL, '学校登录初始页')
    if awaiting_exchange():
        return
    navigate(LOGIN_URL, '联邦认证中转页')
    if awaiting_exchange():
        return
    # 落点说明：`navigate(LOGIN_URL)` 会**自动重定向**到统一认证 CAS 页
    # （uaaap.swu.edu.cn/cas/login?service=...），它是「推荐登录」选择页，
    # 上面有「统一认证登录 / 钉钉扫码登录 / 特定账号登录」三个按钮。
    # 真正的账号密码表单在 idm.swu.edu.cn，需要点第一个按钮才进入
    # —— 这一步见 enter_idm_login()。
    #
    # 这里**不做**任何重复导航：实测对已落地的 CAS URL 用 page.goto 再导航一次，
    # 无论是否追加 federalEnable=true，都会被站点动态防护判为异常并返回 HTTP 400，
    # 导致登录失败并在自动打卡中被反复重试（表现为「连续登录」）。
    final = urlsplit(page.url)
    if final.scheme != 'https' or final.hostname not in TRUSTED_AUTH_HOSTS:
        raise CheckinError('login_required', '未能进入学校统一认证登录页，请重新登录')
    check_cancel(cancel)


def resume_target(value):
    """把服务端给的「下一跳」规整成浏览器可以安全走的 URL；不可信就返回 ''。

    提交是纯 HTTP 客户端做的，浏览器没跟着 POST 的 302 走；服务端在响应里给的
    下一跳（Location，正是表单 `goto` 声明的 oauth2/authorize）才是原来那条跳转链的
    延续。只允许走学校域名；表单里的 `goto` 是 **http**（见 .tools/silent_flow_report.txt），
    同一主机的 https 端点等价且不把会话放在明文里，所以只改 scheme、路径与查询串原样保留。
    """
    try:
        url = urlsplit(value)
    except (TypeError, ValueError):
        return ''
    if url.scheme not in ('http', 'https') or url.hostname not in TRUSTED_AUTH_HOSTS:
        return ''
    if (url.path or '').rstrip('/').endswith('/am/UI/Login'):
        return ''                      # 又指回登录表单：那不是「继续」，别走
    if url.scheme == 'http':
        url = url._replace(scheme='https')
    return urlunsplit(url)


def wait_for_exchange(page, cancel, authenticated=None, timeout=0.0):
    """等 exchange-token 被上层 capture 回调捕获；返回是否已经拿到 token。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        check_cancel(cancel)
        if authenticated is not None and authenticated():
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(200)


def continue_login(page, cancel, target='', authenticated=None, settle=10.0):
    """认证成功后把会话接回浏览器，并**顺着服务端给的下一跳**走完 SSO 收尾。

    ★ 为什么要先走 target，而不是直接从 CAS 初始页重走（2026-09-26 修复）
    ------------------------------------------------------------------
    提交是纯 HTTP 客户端做的，浏览器并没有跟着 POST 的 302 走。服务端在响应里给了
    下一跳（Location），顺着它走才是「接着原来的跳转链」，也最省事、最不容易被站点
    动态防护盯上。旧代码直接 `open_login_page()` 从 CAS 初始页重走一遍：对已经认证
    成功的会话，这等于把原跳转打断（用户看到的就是「学校登录页被跳走/替换」），
    还可能因为链路对不上而落回登录页、看起来像「又要重新登录一次」。

    接不上（不可信 / 被拒 / 超时）就回退 `open_login_page()` 重走整条链路，人工登录照旧。
    返回 True 表示已经等到 exchange-token（token 由调用方的 capture 回调捕获）。
    """
    destination = resume_target(target)
    if destination:
        check_cancel(cancel)
        landed = False
        try:
            response = page.goto(destination, wait_until='domcontentloaded', timeout=30000)
            landed = response is None or response.status < 400
        except CheckinError:
            raise
        except Exception:  # noqa: BLE001
            landed = False
        if landed:
            if wait_for_exchange(page, cancel, authenticated, settle):
                return True
            if on_app_page(page):
                # 已经落在应用页：链路已放行，只差 SPA 触发 exchange-token。
                # 交给上层等待循环，**绝不再导航**（否则又是一次「跳转被打断」）。
                return False
    open_login_page(page, cancel, authenticated=authenticated)
    return wait_for_exchange(page, cancel, authenticated)


def on_app_page(page):
    try:
        url = urlsplit(page.url)
    except Exception:  # noqa: BLE001
        return False
    return url.scheme == 'https' and url.hostname == 'of.swu.edu.cn'


# 「统一认证登录」按钮的锚点只能靠 onclick —— 按钮文字是画在 img/unified_button.png
# 里的，DOM 里没有任何文本节点（实测：全文档搜「统一认证登录」命中 0 次）。
JS_PROBE_LOGIN_STAGE = """() => {
  if (document.querySelector('#loginName') && document.querySelector('#password')) return 'idm';
  if (document.querySelector('div[onclick*="_goLogin"]')) return 'cas';
  if (typeof _goLogin === 'function') return 'cas';
  return 'other';
}"""

JS_CLICK_UNIFIED_LOGIN = """() => {
  const box = document.querySelector('div[onclick*="_goLogin"]');
  if (box) { box.click(); return true; }
  if (typeof _goLogin === 'function') { _goLogin(); return true; }
  return false;
}"""


def enter_idm_login(page, cancel, timeout=15.0):
    """在 CAS「推荐登录」页点「统一认证登录」，进入 IDM 账号密码表单页。

    为什么要“点击”而不是“重新导航”（这是「连续登录」的真正根因）：
      该按钮的 onclick 是 _goLogin()，它给当前 URL 追加 federalEnable=true，
      由**浏览器自身**发起同源导航，最终落到
      idm.swu.edu.cn/am/UI/Login（#loginName / #password / #validateCode /
      #kaptchaImage —— 即 4 位数字验证码那张表单）。
      实测 page.goto 重新导航同一 URL（原样或带 federalEnable）都会 HTTP 400，
      而真实点击正常返回 200 并落到 IDM 表单页。

    仅在配置了静默登录凭据时调用：这个落地页是「三选一」的选择页，
    纯人工登录时应当把选择权留给用户，不要替他点。

    返回 True 表示已停在 IDM 表单页；False 表示当前不是 CAS 选择页（无从点起）。
    """
    try:
        stage = page.evaluate(JS_PROBE_LOGIN_STAGE)
    except Exception:
        return False
    if stage == 'idm':
        return True
    if stage != 'cas':
        return False
    check_cancel(cancel)
    try:
        page.evaluate(JS_CLICK_UNIFIED_LOGIN)
    except Exception:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_cancel(cancel)
        page.wait_for_timeout(200)
        try:
            if page.evaluate(JS_PROBE_LOGIN_STAGE) == 'idm':
                return True
        except Exception:
            # 跳转进行中时执行上下文会被销毁，page.evaluate 必然抛错。
            # 这是这一跳的**正常中间状态**（uaaap → idm 之间还有若干次重定向），
            # 必须继续等而不是放弃 —— 早先在这里直接 return False，
            # 导致刚点完按钮就判定"没进 IDM 页"。
            continue
    return False


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
def owned_browser(playwright, cancel, *, headless=False):
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
            f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
            *(['--headless=new'] if headless else []), 'about:blank',
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


def is_token_exchange(value):
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.hostname == 'of.swu.edu.cn' and url.port in (None, 443)
                and url.path.startswith('/gateway/') and 'exchange-token' in url.path)
    except ValueError:
        return False


def is_authorize_hop(value):
    """认证成功后由服务端发起的那一跳：idm 的 oauth2 授权端点。

    它返回 4xx 就意味着拿不到 code、链路已死（实测：cookie 新旧混代时被动态防护判 400）。
    单独识别它，是为了**别让人干等满 5 分钟**才看到一句含糊的"登录等待超时"。
    """
    try:
        url = urlsplit(value)
        return (url.hostname == 'idm.swu.edu.cn' and url.path.startswith('/am/oauth2/authorize'))
    except ValueError:
        return False


def extract_token(response):
    try:
        if not is_token_exchange(response.url) or response.status != 200:
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


def school_cookies(cookies):
    domains = {'swu.edu.cn', 'of.swu.edu.cn', 'uaaap.swu.edu.cn', 'idm.swu.edu.cn'}
    current = time.time()
    return [cookie for cookie in cookies
            if cookie.get('domain', '').lstrip('.') in domains
            and (cookie.get('expires', -1) == -1 or cookie['expires'] > current)]


def adopt_idm_cookies(context, cookies):
    """把纯 HTTP 登录会话拿到的 cookie 灌回浏览器，让浏览器接着走完 SSO 收尾。

    提交是纯 HTTP 客户端做的（绕开站点 WAF 对浏览器 POST 的 400 拦截，见 idm_http.py），
    因此认证后的新会话只存在于那个 HTTP 会话里，必须搬回浏览器，
    浏览器才能把 CAS → 联邦认证链路走完并触发 exchange-token。

    ★ 必须**先清掉浏览器自己那套学校 cookie 再灌**（2026-09-26 实测）：
    直接 `add_cookies` 会与浏览器原有的主机域 cookie（`idm.swu.edu.cn`）
    **同名并存**（我们统一设成 `.swu.edu.cn`）。站点动态防护一看到同名两代 cookie
    就判重放，**认证之后那一跳** `idm.swu.edu.cn/am/oauth2/authorize` 一律回 400
    —— 表现就是日志里「第 1 次：认证通过（…X-AuthErrorCode=0）」之后
    拿不到 token、界面卡住。清干净再灌，浏览器手里就只剩这一套已认证的会话。

    域统一设成 `.swu.edu.cn`：认证后的会话 cookie 需要在 idm / uaaap / of 三个
    主机之间可见。任何异常都返回 False —— 这只是让浏览器接上会话，
    失败就静默退回人工流程。
    """
    if not cookies:
        return False
    try:
        context.clear_cookies()
    except Exception:  # noqa: BLE001
        pass                      # 清不掉也要继续灌，至少不比以前更差
    try:
        context.add_cookies([
            {'name': name, 'value': value, 'domain': '.swu.edu.cn', 'path': '/'}
            for name, value in cookies.items()
        ])
        return True
    except Exception:
        return False


def resolve_idm_credentials(explicit=None):
    """获取 IDM 静默登录所需的凭据；未配置时返回 None（行为回退到纯人工登录）。

    explicit 可传 IdmCredentials 直接指定；否则尝试从本机加密存储读取。
    任何异常都视为"没有可用凭据"，绝不让凭据问题破坏原有登录流程。
    """
    if explicit is not None:
        return explicit
    try:
        from idm_credentials import IdmCredentialStore  # noqa: PLC0415

        return IdmCredentialStore().load()
    except Exception:
        return None


# 交换成功之后的身份核验：短暂重试几次再下结论。
IDENTITY_ATTEMPTS = 3
IDENTITY_RETRY_SECONDS = 1.0


def identify_student(api, token, cancel, attempts=IDENTITY_ATTEMPTS, delay=None):
    """核验这次登录拿到的身份；**只把「服务端说 token 无效」当作登录失败**。

    ★ 为什么不能一有异常就判登录失败（2026-09-26 修复）
    --------------------------------------------------
    exchange-token 已经成功，就说明**这次登录是成功的**；紧接着的这次调用只是在
    确认「你是谁」。把它的偶发网络抖动也算成登录失败，会把刚拿到的 token/cookie
    一起丢掉，上层接着还会再登一次 —— 表现就是「第一次登录成功却重登」。
    因此这里：transient（网络/服务端错误）先短暂重试；仍然不行也不丢会话
    （由调用方 keep_session 保住），最终按 transient 报出去，让人稍后再试。
    """
    if delay is None:
        delay = IDENTITY_RETRY_SECONDS
    last = None
    for attempt in range(1, max(1, attempts) + 1):
        check_cancel(cancel)
        try:
            return api.user(token)
        except CheckinError as exc:
            if exc.state == 'login_required':
                raise                      # token 确实无效：这才是登录失败
            last = exc
        except Exception as exc:           # noqa: BLE001
            last = exc
        if attempt < attempts:
            cancel.wait(delay)
    # 只报受控文案（异常原文可能含响应内容），且**不能**报成 login_required ——
    # 那会让上层把刚拿到的好会话清掉。学校接口说格式不对时保留 error 以提示去核实。
    state = 'error' if isinstance(last, CheckinError) and last.state == 'error' else 'network_error'
    raise CheckinError(state, '登录已完成，但未能确认登录身份，请稍后重试查询')


def keep_session(store, token, context, session):
    """交换成功、身份却没核验出来时，**保住**这次登录的成果。

    学号优先用本次会话已绑定的那个（非交互续期一定存在）；连它都没有（纯人工首次
    登录）就退回「只有 token」的记录 —— 该状态代码本来支持（见 Store._login_record），
    下一次运行会照常向学校接口核验身份。保不住也不影响要报的错。
    """
    try:
        cookies = school_cookies(context.cookies())
        bound = (session or {}).get('student') or ''
        if bound:
            store.save_browser_session(token, bound, cookies)
        else:
            store.save_token(token)
    except Exception:  # noqa: BLE001
        pass


def form_in_use(page):
    """页面上是否已经有人开始输入（= 用户正在这个窗口里手动登录）。

    用途：避免「为了交回人工而刷新页面」把用户刚敲进去的内容冲掉 —— 那正是
    「程序打断了我的登录」这类体验问题的来源。`is True` 是刻意的：JS 返回布尔值，
    替身/异常一律当作「没人输入」，绝不因为读不到状态就放弃刷新。
    """
    try:
        return page.evaluate(
            "() => ['loginName', 'password', 'validateCode'].some("
            "id => ((document.getElementById(id) || {}).value || '').length > 0)") is True
    except Exception:  # noqa: BLE001
        return False


def attempt_log(store):
    """静默登录的逐步诊断记录（`<store.root>/login.log`），失败时退化为空实现。

    为什么要有它：静默登录此前完全不落日志，2026-09-26 那次「第一次登录成功、
    页面却被跳走、程序又登了一次」在本机没有任何痕迹可查，只能靠读代码推。
    **只记受控字段**（第几次、验证码与置信度、HTTP 状态、X-AuthErrorCode、判定与
    决策），绝不含密码、token 或服务端原文 —— 与 history.log 的纪律一致。
    """
    root = getattr(store, 'root', None)

    def log(message):
        if root is None:
            return
        try:
            path = Path(root) / 'login.log'
            lines = path.read_text(encoding='utf-8').splitlines()[-199:] if path.exists() else []
            lines.append(time.strftime('%Y-%m-%dT%H:%M:%S ') + str(message))
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass                      # 诊断日志写不动，绝不能影响登录本身

    return log


def login(store, api, cancel, timeout=300, *, interactive=True, idm_credentials=None):
    check_cancel(cancel)
    try:
        session = store.browser_session(store.token())
    except CheckinError:
        if not interactive:
            raise
        session = None
    cookies = school_cookies(session['cookies']) if session else []
    if not interactive and not cookies:
        raise CheckinError('login_required', '没有可恢复的学校会话，请手动登录一次')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CheckinError('error', '缺少登录组件，请安装 requirements-dorm.txt 中的依赖或使用完整安装包') from None
    try:
        with sync_playwright() as playwright, owned_browser(playwright, cancel, headless=not interactive) as browser:
            context = browser.contexts[0]
            if cookies:
                context.add_cookies(cookies)
            token, failures = [], []

            def capture(response):
                value = extract_token(response)
                if value and not token:
                    token.append(value)
                elif not value and is_token_exchange(response.url):
                    code = str(response.status)
                    if response.status == 200:
                        try:
                            data = response.json()
                            code = str(data.get('code')) if isinstance(data, dict) else 'invalid'
                        except Exception:
                            code = 'invalid'
                    if response.status >= 400 or (response.status == 200 and code != '200'):
                        state = 'login_required' if code in ('401', '403') else 'network_error'
                        failures.append(CheckinError(state, '学校登录交换未成功，请稍后重试或手动登录'))
                elif response.status >= 400 and is_authorize_hop(response.url):
                    # 认证成功之后的那一跳（idm.swu.edu.cn/am/oauth2/authorize）被服务端拒了。
                    # 实测（2026-09-26）：会话 cookie 新旧混代时动态防护对它一律回 400 空白页，
                    # 而这一跳拿不到 code 就永远换不到 token —— 与其干等满 5 分钟，
                    # 不如立刻把这件事报出来（用户当时看到的就是"既没成功也没失败地卡住"）。
                    failures.append(CheckinError(
                        'network_error',
                        '认证已通过，但后续授权跳转被服务端拒绝（HTTP 400）；请稍后重试'))

            context.on('response', capture)
            page = context.pages[0] if context.pages else context.new_page()
            log = attempt_log(store)
            credentials = resolve_idm_credentials(idm_credentials)
            silent_result = None
            open_login_page(page, cancel, authenticated=lambda: bool(token))

            # ★ 旧会话没接上 → **先清干净再开始新登录**（2026-09-26 实机根因）。
            #
            # 上面 `context.add_cookies(cookies)` 把上一次登录保存的学校 cookie 灌了进来。
            # 那次会话若已作废，浏览器里就会新旧两代 cookie 混在一起，而站点的动态防护
            # 对这类不一致状态一律回 HTTP 400 空白页（`\r\n\r\n\r\n`）—— 实测：
            #   同一批 cookie 请求 `GET /am/validate.code` → 400（6 字节空白）
            #   全新会话请求同一 URL              → 200（正常 JPEG，模型识别 1.0000）
            # 后果是整条链死掉：验证码取不到（`captcha_http_400`），认证成功后的授权跳转
            # `idm.swu.edu.cn/am/oauth2/authorize` 也被 400，界面就停在那一页不动，
            # 用户看到「既没成功也没失败地卡住」。清干净后重走一遍即恢复正常。
            if cookies and not token:
                log('旧会话未接上：清空学校 cookie，改用干净会话重走链路')
                try:
                    context.clear_cookies()
                except Exception:  # noqa: BLE001
                    pass                      # 清不掉也不要紧，下面的流程照常尝试
                open_login_page(page, cancel, authenticated=lambda: bool(token))

            # 静默登录：只要配置了 IDM 凭据就尝试（交互与非交互模式都需要 ——
            # 后者用于后台自动打卡时无人值守地续期会话）。
            # 失败后**不抛错**，继续走原有的人工等待流程 —— 保证不降低可用性。

            def fresh_session():
                """换全新会话：重走 SSO 链路，落到一张新的 IDM 表单页。

                **只在两处调用**，两处都不是「可能已经成功」的情形：
                  1. 服务端明确说「验证码错」时，重试前换会话（见 idm_login 的重试策略）；
                  2. 交互模式下自动尝试**已明确失败**、要把登录交回人工之前 ——
                     失败的那个 POST 会让这个会话失效，而我们取过的每一张验证码都会让
                     页面上显示的那张作废（服务端只认最后一张）。把人留在这种页面上，
                     他照着图输入也必然失败（实测：`docs/captcha-integration.md`）。
                """
                log('重走 SSO 链路换取新会话')
                open_login_page(page, cancel, authenticated=lambda: bool(token))
                enter_idm_login(page, cancel)

            if credentials is not None and not token:
                try:
                    import idm_login  # noqa: PLC0415

                    # 先点「统一认证登录」，把 CAS 选择页推进到 IDM 账号密码表单页。
                    # 没有这一步，on_idm_login_page 永远不成立，静默登录形同虚设
                    # （实测：落点一直在 uaaap 的 CAS 选择页，从未到达 idm.swu.edu.cn）。
                    enter_idm_login(page, cancel)
                    silent = idm_login.attempt_silent_login(
                        page, credentials.username, credentials.password,
                        cancel=cancel, fresh_session=fresh_session, log=log,
                    )
                    silent_result = silent
                    if silent.ok:
                        # 认证已通过：把纯 HTTP 会话拿到的 cookie 灌回浏览器，并
                        # **顺着服务端给的下一跳**走完 SSO 收尾（token 由 capture 捕获）。
                        log(f'静默登录通过：判据={silent.accepted_by or "header"}，'
                            f'下一跳={"有" if silent.resume_url else "无"}')
                        adopt_idm_cookies(context, silent.cookies)
                        continue_login(page, cancel, silent.resume_url,
                                       authenticated=lambda: bool(token))
                    elif silent.reason == 'unconfirmed':
                        # ★ 服务端**可能已经认证成功**，只是没拿到判据（响应头缺失 /
                        # 响应丢失 / 落点不认识）。这里绝不再登一次：先把会话接上、
                        # 让浏览器走完跳转链去等 exchange-token，成功与否由 token 定论。
                        log('提交结果未确认（可能已认证）：先接上会话走完 SSO 验证，不再自动重登')
                        adopt_idm_cookies(context, silent.cookies)
                        continue_login(page, cancel, silent.resume_url,
                                       authenticated=lambda: bool(token))
                    else:
                        log(f'静默登录未通过：reason={silent.reason}；'
                            '不再自动重登，回退人工/上层流程')
                    # 无论成功与否都不在此处抛错：
                    # 成功则由下方 token 轮询自然捕获；失败则回退人工等待。
                except Exception:
                    # 静默登录属于增强能力，任何异常都不应影响原有流程
                    pass

            # ★ 交回人工前，把页面刷新成一张**可用**的登录表单（只限交互模式 + 明确失败）。
            #
            # 为什么必须刷新：失败的那个 POST 已经让会话失效，而且我们取验证码用的
            # 是同一个会话（服务端只认最后取的那一张），页面上显示的那张已经不是当前
            # 有效的了 —— 人照着图输入也一定失败，只会以为"程序把登录弄坏了"。
            # 刷新给出的是新会话 + 当前验证码，这时**真人手动登录是可行的**：
            # 实测（tools/captcha/probe_browser_submit.py）浏览器里被拦的只是**脚本**发起的
            # POST（验证码预校验 verify.do 被 WAF 判 400，页面因此只换图不提交），
            # 真人键盘输入不受此限；上游 dan-cun/swu-daka 的默认「人工手动登录」模式
            # 也正是靠这一点稳定工作。
            # 刷新之后我们不再碰这个页面（也绝不再取验证码），把登录交给用户。
            if (interactive and not token and silent_result is not None
                    and not silent_result.ok and silent_result.reason != 'unconfirmed'):
                if form_in_use(page):
                    # 用户已经在这个窗口里开始手动输入了：**不要动页面**。
                    # （他若发现验证码对不上，点一下验证码图就能换一张。）
                    log('检测到用户已开始手动输入：不刷新页面以免打断他，直接交回人工')
                else:
                    try:
                        log('自动尝试已明确失败：刷新登录页（新会话 + 当前验证码）后交回人工')
                        fresh_session()
                    except Exception:
                        pass                  # 刷不动就把当前页面留给用户，不额外报错

            deadline = time.monotonic() + (timeout if interactive else min(timeout, 30))
            while not token and not failures and time.monotonic() < deadline:
                check_cancel(cancel)
                if not context.pages:
                    raise CheckinError('cancelled', '登录窗口已关闭')
                context.pages[0].wait_for_timeout(200)
            check_cancel(cancel)
            if not token:
                # 每条失败路径都写一行诊断日志：以前只能靠界面上那行字，事后无从查。
                if failures:
                    log(f'登录未完成：{failures[-1]}')
                    raise failures[-1]
                if interactive:
                    # 静默登录若被服务端明确判为「用户名或密码错误」，就把这个事实
                    # 报出去 —— 否则用户只会看到含糊的"登录等待超时"，无从下手。
                    if silent_result is not None and silent_result.reason == 'bad_credentials':
                        message = '统一认证提示「用户名或密码错误」：请在面板中更新已保存的凭据后重试'
                    elif silent_result is not None and silent_result.reason == 'unconfirmed':
                        message = '本次登录结果未确认（没拿到登录令牌），已放弃自动重登；请重试或手动完成登录'
                    else:
                        message = '登录等待超时，请重试'
                    log(f'登录未完成：{message}')
                    raise CheckinError('login_required', message)
                challenge = page.locator('input[type="password"], input[autocomplete="one-time-code"], '
                                         'input[name*="captcha" i]').first.is_visible()
                check_cancel(cancel)
                if challenge:
                    log('登录未完成：学校会话已过期或需要验证码')
                    raise CheckinError('login_required', '学校会话已过期或需要验证码，请手动登录')
                log('登录未完成：学校登录交换未完成（已保留会话）')
                raise CheckinError('network_error', '学校登录交换未完成，已保留会话，请稍后重试')
            try:
                student = identify_student(api, token[0], cancel)
            except CheckinError:
                # 交换已经成功 = 这次登录是成功的；身份没核验出来时把成果保住，
                # 别让上层以为要重新登录（那正是「第一次成功却重登」的另一半原因）。
                keep_session(store, token[0], context, session)
                raise
            check_cancel(cancel)
            if not interactive and student != session['student']:
                raise CheckinError('login_required', '恢复的登录账号不一致，请手动登录')
            cookies = school_cookies(context.cookies())
            check_cancel(cancel)
            store.save_browser_session(token[0], student, cookies)
            log(f'登录完成：会话已保存（学号 {student}，cookie {len(cookies)} 个）')
            return student
    except CheckinError as exc:
        if not interactive and exc.state == 'login_required':
            store.clear_browser_session()
        raise
    except Exception:
        if not interactive:
            raise CheckinError('error', '登录会话恢复未完成，请稍后重试或手动登录') from None
        raise CheckinError('login_required', '登录未完成，请重新打开登录窗口') from None
