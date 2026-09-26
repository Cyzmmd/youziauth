# 寝室打卡集成验证记录

## 1.6.6 登录判据分层：不再「第一次成功后又被强制跳走、再登一次」（2026-09-26）

**现象**（用户报告）：自动登录启动后，**第一次登录其实已经成功**，但浏览器里的
学校登录页随即被强行跳走/替换（窗口还在），程序**又自己登了一次**（用户没点任何
按钮），原先那条登录跳转逻辑被打断。

**根因**（两个缺陷叠加，都在「失败的定义」上）：

1. 成功判据只有一条：响应头 `X-AuthErrorCode == "0"`，其余一律算失败。
   判据一旦缺失（响应头被中间层吞掉、连接在响应回来前断掉），**服务端已经认过的
   登录会被判成失败**；
2. 重试策略是「只要不是密码错就重走 SSO 换新会话再试一次」。于是失败判定立刻
   触发 `fresh_session()`：正在走的登录页被 `page.goto(INIT_URL)` 导航掉
   （用户看到的「登录页被跳走」），紧接着又发一次登录 POST
   （用户看到的「又登了一次」）。实测还发现 `submit_error`（POST 抛异常）被算成
   「未提交、可以原地重试」，同样会踩掉一次可能已成功的登录。

**修复**：

* `idm_http.judge_response()` 按证据强弱分层：`X-AuthErrorCode == "0"` → 通过
  （判据 `header`）；3xx 且落点等于表单自带的 `goto`（页面声明的成功落点）→ 通过
  （判据 `redirect`）；落回登录页 / `gotoOnFail` / 非学校域名 → 明确失败；
  **其余（响应丢失、无文案的 4xx-5xx 含 WAF 400 空白页、落点不认识）→ 未确认**。
* `SubmitOutcome` 新增 `sent` / `location` / `accepted_by`，并把
  `retryable` 收窄成「**只有**服务端明说验证码错才值得换新会话重试」。
* 未确认时**不再自动重登**：把会话 cookie 与下一跳交回 `dorm_login`，
  先接上会话、走完跳转链等 `exchange-token` 定论；拿不到 token 才回退人工。
* 认证成功后**顺着服务端给的下一跳走**（`dorm_login.resume_target`：只放行学校域名，
  http 同主机升级为 https），不再从 CAS 初始页重走一遍；接不上才回退整条链路。
  已在收尾落点/已拿到 token 时**一次导航都不做**。
* 交换成功后的身份核验（`api.user`）抖动不再算登录失败：先重试，
  仍不行则保住这次登录的会话（`dorm_login.keep_session`），
  报 transient 而不是 `login_required`。
* 新增诊断日志 `<LOCALAPPDATA>\youziauth\dorm\login.log`：每次尝试只记受控字段
  （第几次、验证码与置信度、HTTP 状态、`X-AuthErrorCode`、判定与决策），
  不含密码、token 或响应原文 —— 这次事故在本地零痕迹，只能靠读代码推，不能再这样。

**验证**：`python -m unittest discover -s tests -q` → **528 passed, 1 skipped**。
新增回归覆盖：302 无响应头但落点=成功落点算通过、落回登录页/失败落点/校外域名算失败、
响应丢失与空白页归「未确认且不可重试」、只有验证码错才重试、未确认时不得触发第二次登录、
认证成功后沿服务端下一跳走（不再回到 CAS 初始页）、身份核验抖动保住会话、
交回人工前刷新页面（且非交互/未确认时**不**刷新、**用户已开始手动输入时不刷新**）、
诊断日志受控且写不动也不报错。

**同轮澄清的前提**（用户提示「上游版本手动登录一直可用」后实测）：
新增 `tools/captcha/probe_browser_submit.py`，照上游 `legacy/browser_login.py` 的
`login_once()` 手法（清残留 `#tishi` → 拟人逐字输入 110–260ms/字符 → 等验证码图
`complete && naturalWidth>0` → **截元素**（不额外请求验证码）→ 输入 → blur → 真实点击
`#button`），用假账号实测：

```
页面预校验图标 tishi = '/am/bjcaportal/theme/default/img/login/code_error.png'
浏览器发出的 /am/** POST: [('/am/validatecode/verify.do?ZUY2FAwZ=...', 400)]
页面前 200 字: '…提示 !验证码输入不正确 确定'
导航轨迹: [of.swu CAS, uaaap CAS, idm表单页]      ← 点击后没有任何提交 POST
```

结论：被 WAF 拦的是**脚本**发起的提交 —— 连页面自己附加的动态令牌都带上了，
预校验仍 400，页面据此置错、点登录只换图不提交（这就是上游 PR 描述的「验证码死循环」）。
**真人键盘输入不受此限**（上游默认「人工手动登录」模式即靠这一点，用户亦确认可用）。
因此：脚本路径继续走纯 HTTP 客户端绕行；人工路径是可靠后备，程序在交互模式下
把失败后的页面刷新成新表单（新会话 + 当前验证码）再交回人工 —— 不刷新的话，
失败 POST 已让会话失效、我们取过的验证码也作废，用户照图输入必然失败。

### 1.6.6 续修（同日 15:27–15:33 实机联调）：三层「cookie 被动态防护判死」

实机复现「第一次登录成功后卡在 `idm.swu.edu.cn/am/oauth2/authorize`、程序又登一次」后，
用三组对照实验定位到**同一类根因的三种表现**。判据全部是可复现的状态码：

| # | 现象 | 实测 | 根因 | 修法 |
|---|---|---|---|---|
| ① | 取验证码 400（`captcha_http_400`） | urllib 带浏览器全部 cookie → 400；**不带 cookie** → 200；换 UA 无效 | `login()` 把上次登录保存的旧学校 cookie 灌进新浏览器，新旧两代并存 | 旧会话接不上就 `clear_cookies()` 清干净再重走（`login()`） |
| ② | 取验证码/提交一律 400 | 只带 HttpOnly 会话 cookie → **200 且 POST 到达服务端**（`X-AuthErrorCode=-1`+「用户名或密码错误」）；带 JS 可见的 → 400 | 动态防护自己 JS 要读的 cookie（出现在 `document.cookie`）被「非页面 JS」的请求带上 = 判重放 | `idm_http.browser_cookies(context, page)` 剔掉 JS 可见的那几枚，只带 HttpOnly |
| ③ | 认证成功后授权跳转 400，拿不到 token | 认证已 `X-AuthErrorCode=0`，随即 `am/oauth2/authorize` 400 | `adopt_idm_cookies` 直接 add，与浏览器主机域同名 cookie **并代** | 灌会话前先 `clear_cookies()` 再 add |

复核脚本：`tools/probe_session_cookies.py`、`tools/probe_cookie_bisect.py`、
`tools/probe_ua_match.py`、`tools/probe_submit_subsets.py`、`tools/probe_http_chain.py`、
`tools/inspect_login_browser.py`（报告在 `.tools/`，均只读或仅用假账号）。

**端到端成功记录**（2026-09-26 15:32–15:33，真实账号，交互登录一次）：
```
15:32:54 旧会话未接上：清空学校 cookie，改用干净会话重走链路
15:32:57 第 1 次：用会话 cookie 提交（1 枚；站点动态防护那几枚已剔除…）
15:32:59 第 1 次：认证通过（判据=header，验证码 9888，置信度 1.000，HTTP 302，X-AuthErrorCode=0）
15:32:59 静默登录通过：判据=header，下一跳=有
15:33:05 登录完成：会话已保存（学号 222025321102104，cookie 11 个）
```
一次通过、零重试、窗口自动关闭、会话轮换（`session/credential.dat` 3784 字节）。
本阶段的判据与重试策略已并入正文，不再单独列。

**本轮全量测试**：`python -m unittest discover -s tests -q` → **542 passed, 1 skipped**
（既有 `test_windows_update` 在全量跑里偶发失败一次：`test_worker_rejects_invalid_signature_result_and_timestamp`
的某个 subTest 报了另一个错误文案。事后单独跑 10 遍、全量跑 3 遍（含构建高负载）均通过，
未能复现，暂记为**低频偶发**、与本轮改动无关 —— 复现时先看它的 worker 退出码与 `_ERRORS` 映射。）

**构建与打包验证**（VERSION 已 bump 到 1.6.6）：
- `.\build_msi.ps1 -InstallDependencies -VerifyPayload` → 成功；
  `WiX manifest covers all 1759 bundle files` + `MSI payload verified: 1759 files match`；
- `dist\youziauth.msi`：71,436,084 字节，ProductVersion **1.6.6**（直接查 MSI 数据库确认）；
- SHA-256：`B8CE52BB06CEBCAF167BC93C8C54005856D4367B163E10C5CB1C7EF1CCD46D12`
  （同时写入 `dist\youziauth-1.6.6.sha256`，沿用 `hash *file` 格式）；
- 打包版离线自检 `dist\youziauth\youziauth.exe --dorm-self-test` → `ok: true`
  （`dpapi` / `tk` / `playwright` 全通过，未做任何登录或学校请求）。

## 1.2.1 登录迁移修复（2026-09-21）

用户报告并截图：浏览器停在 `uaaap.swu.edu.cn/cas/login` 空白页。对照上游发现 1.2.0 遗漏 CAS 初始页和联邦认证后续跳转，并将普通 Chrome 会话改成 Playwright 直接启动 Edge。真实未登录页面探测复现了中转页停留/HTTP 400；恢复普通浏览器会话、完整跳转流程后，源代码及打包版都到达 IDM 用户名密码页。

- 采用独立临时 Chrome 会话（Edge 回退），仅绑定本机地址的明确非零临时 CDP 端口，不接管用户现有浏览器。
- 原样保留中转 URL 的编码参数，再添加 `federalEnable=true`；跳转的 HTTP 错误显示阶段信息，不再吞掉后空等 Token。
- 新增跳转顺序、编码参数保留、HTTP 错误、取消、非学校重定向、原生浏览器创建和清理测试。全套 **182 tests passed**。
- 源码真实页面检查：`build/qa/verify_login_fix.py`，IDM 账号框和密码框可见，截图 `build/qa/login-ready.png`。
- 打包真实页面检查：`youziauth.exe --dorm-login-probe <report-path>`，结果 `build/qa/packaged-login-1.2.1.json` 为 `ok: true`、`school_login_page: true`，同时通过 Tk/DPAPI/浏览器驱动检查。
- 没有输入真实账号密码、提交登录或提交打卡，故不宣称完整账号登录/Token 获取和打卡端到端成功。
- WiX MSI ProductVersion：**1.2.1**；SHA-256：`1C4EC1691125448F264E4379BA42A0B0F5479E6E479C51BF5A4C7D9C619D397C`。

## 1.6.2 跳转失败根因：浏览器 POST 被瑞数 WAF 拦成 400 空白页

**现象**（用户截图）：登录信息填完后浏览器停在**空白的** `idm.swu.edu.cn/am/UI/Login`，
加载一会后窗口自动关闭；看日志像是"登录成功但跳转出错"。

**根因**（参考上游 `dan-cun/swu-daka` 提交 `29957ff` 的实测结论，本机复现一致）：
`idm.swu.edu.cn` 前挂了一层**瑞数（RiverSecurity）类动态 WAF**，它把
**浏览器内一切「带 body 的 POST 到 `/am/`」直接拦成 HTTP 400**，响应体只有
`\r\n\r\n\r\n` —— 就是那个空白页。被拦的包括页面自己的验证码预校验
`POST /am/validatecode/verify.do` 与登录表单提交 `POST /am/UI/Login`
（`form.submit()` / `fetch` 一样）。

也就是说：**验证码识别一直是对的，只是这个 POST 根本没到服务器。**
1.6.0 / 1.6.1 里那套「填表 → 点登录 → 用是否离开 idm 主机判定成功」的写法
注定失败 —— 它永远等不到跳转，只会被判失败并重试。

**修复**：职责拆开。
* **浏览器**只走 SSO 链路、持有会话、导出 `forms['Login']` 的全部隐藏字段；
* 新增 `idm_http.py`（标准库 urllib，无新依赖）：`GET /am/validate.code` 取图 →
  本地模型识别 → `POST /am/UI/Login` 提交。纯 HTTP 客户端**不被这层 WAF 拦截**；
* 成功判据换成服务端明示的响应头 **`X-AuthErrorCode == "0"`**，不再靠间接推断；
* 认证后把 HTTP 会话的 cookie 灌回浏览器（域 `.swu.edu.cn`），再重走 SSO 收尾，
  由 `exchange-token` 响应头拿到 `fighter-auth-token`。

三条踩过的坑（均已被上游实测确认，本次一并修正）：
1. **账号密码必须明文提交**；页面那套 `strEnc` 提交**反而会被判「用户名或密码错误」**。
2. **提交失败后会话即失效**，同会话重试无意义，必须重走 SSO；
   而**直接 reload 登录页会被 WAF 打成 400 空白页** —— 原先的
   `_reload_fresh_session` 正是这件被禁的事，已删除。
3. 表单隐藏字段（`goto` / `SunQueryParamsString` / `encoded`）随会话变化，
   **必须实时从页面导出，不能硬编码**。

**判定逻辑的修正（用户问的「是不是判定有缺陷」——确实有）**：
`X-AuthErrorCode` 对下面两种失败**都是 `-1`**，但正文文案不同：

| 情形 | 文案 |
|---|---|
| 验证码错（提交 `0000`） | `UAMS 验证失败。动态口令验证失败` |
| 验证码对、账号密码错 | `UAMS 验证失败。用户名或密码错误。` |

所以现在**密码错时立即停止**，不再消耗第 2 次登录尝试（重试必然再错，
只会白烧一次尝试、逼近锁定阈值），并把「请在面板中更新已保存的凭据」
直接报给用户，而不是含糊的"登录等待超时"。
（旧文档写的「两者提示完全相同、无法区分」是错的 —— 那次比的是
「验证码错误」与「验证码为空」，两者都是验证码问题。）

**验证**（真实环境，假账号）：
```
第 1 次: 验证码 3711 (置信度 0.9841) → HTTP 200, X-AuthErrorCode=-1
         UAMS 验证失败。用户名或密码错误。 返回至登录页面
第 2 次: 验证码 1170 (置信度 1.0000) → HTTP 200, X-AuthErrorCode=-1
✓ 两次 POST 都真正到达服务端；换会话后验证码确实变了
```
修复前这里只会是空白页、连错误码都拿不到。

打包产物自检（`--dorm-login-probe`，只取图不提交）：
`http_client: {status: 200, bytes: 2015, form_fields: 9, cookies: 4}` ——
证明冻结环境里的纯 HTTP 客户端能取到验证码、导出全部表单字段。

新增单元测试 `tests/test_idm_http.py`（15 项）：失败文案分类、
明文提交（并对 SSO 字段保真）、低置信不提交、密码错立即停手、
验证码错重走 SSO 重试。全量 **426 passed**。

- WiX MSI ProductVersion：**1.6.2**；SHA-256：`8A6674347A7F6B08F73E4AB4E1F78618BA9B15E1C604290F617CC58A9886AC70`。

## 1.6.1 静默登录修复：`federalEnable` 那一步必须“点击”而非“重新导航”

1.6.0 曾把上面 1.2.1 的「原样保留中转 URL 的编码参数，再添加 `federalEnable=true`」
当成多余动作**删掉**，依据是探针里 `page.goto(同一 URL + federalEnable=true)`
返回 **HTTP 400**。事后查明该结论是**错的**，原因有两层：

1. 那个探针用的是 `playwright.chromium.launch()`，会被站点的动态防护
   （瑞数信息，页面内可见 `$_ts.cd` / `$_ts.nsd` 与随机名 Cookie）
   **确定性**识别并返回 400/412。同一 URL 用 `urllib` 或程序实际使用的
   `owned_browser`（普通 Chrome + CDP）反复请求 **15/15 全部 200**。
   那些 400 不是服务端故障，是自动化指纹触发的拦截。
2. 即便用对了浏览器，**重新导航**与**真实点击**仍不等价：按钮
   `onclick=_goLogin()` 是由浏览器自身发起的同源导航，才能通过站点校验；
   `page.goto` 同一个 URL（带或不带 `federalEnable`）会被拒。

后果：删掉该步后，落点永远停在 `uaaap.swu.edu.cn/cas/login` 的「推荐登录」
三选一选择页，既到不了 IDM 表单页，也让 `on_idm_login_page` 永不成立、
静默登录**从未生效**；在选择页上探测 `#loginName` 等元素自然全部
`visible=False`，于是又被误判成「站点改版 / 验证码换成滑块」。

修复：新增 `dorm_login.enter_idm_login()`，用 `div[onclick*="_goLogin"]` 定位按钮
（按钮文字是画在 `img/unified_button.png` 里的，DOM 中搜「统一认证登录」命中 0 次）
并**点击**它，再轮询等 `idm.swu.edu.cn/am/UI/Login` 的表单出现。
跳转过程中 `page.evaluate` 会因执行上下文销毁而抛错，属正常中间状态，必须继续重试。
该函数**只在配置了静默登录凭据时调用** —— 该页是三选一的选择页，
纯人工登录仍把选择权留给用户。

验证：`tools/captcha/verify_silent_flow.py`（用假账号提交）全链路通过 ——
点击后落到 `idm.swu.edu.cn/am/UI/Login`（标题「统一认证管理系统」），
`#loginName`/`#password`/`#validateCode`/`#kaptchaImage` 均可见，
验证码 100×30，两次识别置信度均 **0.9999**，换会话后验证码确实变为另一张
（`6568` → `5484`）。模型与数据无需任何改动。

以下为 1.2.0 首版的历史验证记录，原产物哈希不再对应当前 `dist/youziauth.msi`。

日期：2026-09-21。基线：youziauth `3216fde5fd860c4ab5627e683c3f42b425e9ea9f`；工作分支：`codex/dorm-checkin`。接口参考：本地 swu-daka `42d8973`。

## 已验证

- `python -m unittest discover -s tests -q`：176 tests，全部通过。基线为 123 tests。
- 模拟本地 HTTP 学校服务器覆盖查询、定位校验、提交、回查、重复跳过及提交响应丢失。未向真实学校接口发请求。
- 状态机覆盖日期、学校时段、取消、自动设置变化、Token 缺失、任务完成、提交待确认、账号切换和重启恢复。
- Python 编译和 `git diff --check` 通过；Git 的 LF/CRLF 提示属于行尾转换提示。
- `python packaging/preview_dorm.py` 实例化完整主窗口及打卡窗口，使用隔离临时配置和合成任务，检查按钮可见性、关闭窗口保留后台控制器。截图位于 `build/qa/main.png` 和 `build/qa/dorm-panel.png`。
- `python campus_auth_gui.py --dorm-self-test build/qa/source-selftest.json`：Tk、当前用户 DPAPI 往返、Playwright 启动本机 Edge 并访问本地内存页面通过。
- `./build_msi.ps1`：PyInstaller 与 WiX 成功生成 MSI；只读检查 MSI ProductVersion 为 `1.2.0`。
- `dist/youziauth/youziauth.exe --dorm-self-test <absolute-output-path>`：打包版 Tk、DPAPI、Playwright/Edge 同样通过。结果在 `build/qa/packaged-selftest.json`。
- 独立审查发现写入待确认标记期间发生取消/时段变化的竞态，已补上提交前最后检查及三项回归测试。审查代理随后因额度中断，因此不宣称完整独立审查通过。

## 产物

- `dist/youziauth.msi`，47,682,476 字节。
- SHA-256：`031024C8A8280405B028B94CBC88E3FA10A689B2F3E6058429023ABB79129BC1`。
- `dist/youziauth/youziauth.exe` 可直接运行，需保留同目录的全部文件。

## 未验证范围

没有实际安装/升级 MSI、创建开机计划任务、登录真实学校账号、读取真实电脑定位或提交真实打卡。尚不能证明学校当前协议、用户任务数据、Windows 定位精度及服务器位置校验兼容。登录过期后需要用户手动登录；程序退出或电脑睡眠时不执行。第一次试用请先查询今日任务，再手动提交确认真实链路，最后启用自动打卡。

源代码改动保留在本地工作分支；未推送、发布 Release 或修改双方项目许可证。
