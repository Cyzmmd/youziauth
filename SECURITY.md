# 安全问题报告

youziauth 是面向西南大学校园网环境的非官方开源工具。项目会处理校园网账号和认证参数，因此安全问题报告必须先清除个人信息。

## 不要公开提交的信息

请勿在 GitHub Issue、讨论区、截图或附件中公开以下内容：

- 学号、密码或密码环境变量的实际值；
- 完整的校园网登录 URL 或 `queryString`；
- MAC 地址、设备 IP、认证 IP、用户索引等网络标识；
- `config.ini`、`campus_auth_password.txt`、`credential.dat`；
- 未经处理的 `campus_auth.log` 或其他运行日志；
- 包含 Windows 用户名或用户目录的本机路径。

## 安全报告方式

如果仓库已启用 GitHub Private Vulnerability Reporting，请优先通过仓库的 **Security → Report a vulnerability** 私下报告。若该入口尚未启用，请只创建不含敏感数据的最小复现说明，并等待维护者提供私下沟通方式。

提交前请把账号替换为 `YOUR_STUDENT_ID`，把密码替换为 `<redacted>`，删除 URL 查询参数，并将 MAC、IP 和本机路径替换为明显的占位值。尽量提供离线测试用例，而不是上传真实配置或日志。

## 支持范围

安全修复以当前最新发布版本和主分支为主。校园网认证协议或学校网络策略发生变化造成的兼容性问题不一定属于安全漏洞。

本项目与西南大学及校园网运营商无隶属、授权或合作关系。
