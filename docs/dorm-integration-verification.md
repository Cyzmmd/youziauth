# 寝室打卡集成验证记录

## 1.2.1 登录迁移修复（2026-09-21）

用户报告并截图：浏览器停在 `uaaap.swu.edu.cn/cas/login` 空白页。对照上游发现 1.2.0 遗漏 CAS 初始页和联邦认证后续跳转，并将普通 Chrome 会话改成 Playwright 直接启动 Edge。真实未登录页面探测复现了中转页停留/HTTP 400；恢复普通浏览器会话、完整跳转流程后，源代码及打包版都到达 IDM 用户名密码页。

- 采用独立临时 Chrome 会话（Edge 回退），仅绑定本机地址的明确非零临时 CDP 端口，不接管用户现有浏览器。
- 原样保留中转 URL 的编码参数，再添加 `federalEnable=true`；跳转的 HTTP 错误显示阶段信息，不再吞掉后空等 Token。
- 新增跳转顺序、编码参数保留、HTTP 错误、取消、非学校重定向、原生浏览器创建和清理测试。全套 **182 tests passed**。
- 源码真实页面检查：`build/qa/verify_login_fix.py`，IDM 账号框和密码框可见，截图 `build/qa/login-ready.png`。
- 打包真实页面检查：`youziauth.exe --dorm-login-probe <report-path>`，结果 `build/qa/packaged-login-1.2.1.json` 为 `ok: true`、`school_login_page: true`，同时通过 Tk/DPAPI/浏览器驱动检查。
- 没有输入真实账号密码、提交登录或提交打卡，故不宣称完整账号登录/Token 获取和打卡端到端成功。
- WiX MSI ProductVersion：**1.2.1**；SHA-256：`1C4EC1691125448F264E4379BA42A0B0F5479E6E479C51BF5A4C7D9C619D397C`。

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
