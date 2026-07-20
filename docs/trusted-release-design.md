# youziauth 可信发布与系统代理恢复设计

## 背景与问题定义

Microsoft Defender 在 2026-07-19 13:47 将 `youziauth-agent.exe`、正在运行的 SYSTEM 进程和 `\youziauth\SystemAgent` 计划任务识别为 `Trojan:Win32/Wacatac.C!ml` 并隔离。隔离时间与代理日志及 `runtime.json` 停止更新时间一致。后续重装恢复了可执行文件，但没有恢复 SYSTEM 任务；Tray 任务仍存在，因此界面继续显示“开机自启动已启用”，实际认证、IPC 和日志均已停止。

当前发布物未进行 Authenticode 签名，PE 文件版本元数据为空。项目已经以 `GPL-3.0-only` 发布在公开 GitHub 仓库，适合优先申请 SignPath Foundation 的免费开源代码签名。

## 目标

- 恢复当前电脑上的 SYSTEM Agent，并在不关闭 Defender、不添加目录级白名单的前提下完成实机验收。
- 建立由固定 Git 提交产生、可追溯、签名并带可信时间戳的 Windows 发布流程。
- 向 Microsoft 提交当前误报样本，减少现有版本在其他电脑上的隔离风险。
- 修正 Tray 任务存在但 Agent 已失效时的错误健康判断，明确显示降级状态和修复入口。
- 让用户能够验证 MSI、两个 EXE、源码提交和 SHA-256 之间的关系。

## 非目标与安全边界

- 不关闭 Defender 实时保护，不添加 `C:\Program Files (x86)\youziauth` 目录级排除项，不指导普通用户全局忽略 `Wacatac` 检测。
- 不保证签名后二进制永不被任何安全产品误报；签名用于建立发布者身份和信誉，误报仍通过供应商复核处理。
- 不把校园网账号、密码、DPAPI 密文、真实认证 URL、日志或本机路径上传到 GitHub、SignPath 或 Microsoft 误报材料。
- 不把签名密钥保存到仓库、GitHub Secret 或开发电脑；签名密钥由 SignPath 的 HSM 管理。
- 不自动发布未经签名和验证的 GitHub Release。

## 方案选择

采用“Microsoft 误报申诉 + SignPath Foundation 免费开源签名 + 运行时健康加固”的组合方案。

商业 OV 证书作为 SignPath 申请未获批准时的回退方案。仅提交误报只能处理当前文件哈希，不能为后续版本建立稳定发布者身份；仅重建任务则可能再次触发隔离，因此两者都不单独作为正式方案。

## 当前电脑恢复流程

1. 再次核对已安装 `youziauth-agent.exe` 的 SHA-256 与当前受审计构建产物一致。
2. 在 Windows 安全中心仅对本次已确认的检测执行“允许在设备上”，不创建文件夹排除项。
3. 通过现有管理员配置入口重新注册 `\youziauth\SystemAgent` 和 `\youziauth\Tray`，确保任务参数包含当前用户 SID。
4. 验证 SYSTEM Agent 进程、`youziauth-agent` 命名管道、`runtime.json` 新鲜度和日志增长。
5. 重启后再次验证 Agent 在 Explorer 之前启动、Tray 隐藏启动、认证状态更新且 Defender 没有新增检测。

若 Defender 在允许当前检测后仍隔离相同哈希，则停止重复恢复，不扩大排除范围，先完成 Microsoft 样本复核或改用已签名构建。

## Microsoft 误报申诉

以 Software developer 身份分别提交以下内容：

- `youziauth-agent.exe`：本次 `Wacatac.C!ml` 的直接检测对象。
- `youziauth.exe`：曾出现独立机器学习检测记录的交互端。
- `youziauth.msi`：真实分发入口，用于关联安装上下文。

提交说明包含公开仓库 URL、对应 Git 提交、产品用途、GPL 许可证、构建工具版本、文件 SHA-256、Defender 检测名称及事件时间。提交包不得包含用户配置、凭据或日志。Microsoft 返回分析结论和提交编号后，将编号记录到私有发布记录；只有可公开的结论摘要进入仓库文档。

## 可执行文件元数据

为 `youziauth.exe` 和 `youziauth-agent.exe` 生成并嵌入独立的 Windows 版本资源，字段至少包含：

- ProductName：`youziauth`
- FileDescription：分别为交互式 Tray/设置端和 SYSTEM 校园网认证代理
- CompanyName：`yoouzic`
- LegalCopyright：`Copyright (C) 2026 yoouzic`
- FileVersion 与 ProductVersion：与 WiX `Product Version` 使用同一发布版本
- OriginalFilename：对应 EXE 文件名

版本值由单一发布版本源生成，PyInstaller 与 WiX 不再分别硬编码版本，避免 MSI、EXE 和 Git Tag 漂移。

## 签名与构建流水线

正式发布从形如 `v1.1.4` 的受保护 Git Tag 触发，流程如下：

1. 在 GitHub 托管的 Windows runner 上检出 Tag 对应提交。
2. 安装锁定版本的 Python、PyInstaller、WiX 和项目依赖。
3. 运行全部单元测试、Python 编译检查、许可证检查和敏感信息扫描。
4. 生成带版本资源的两个 EXE，并构建未签名 MSI。
5. 将待签名文件及构建来源提交给 SignPath；SignPath 在 HSM 中完成 Authenticode 签名和 RFC 3161 时间戳。
6. 下载签名后的 EXE，重新生成包含已签名文件的 MSI，再对最终 MSI 签名并加时间戳。
7. 使用 `Get-AuthenticodeSignature` 或 `signtool verify /pa /all` 验证三个签名，任何一个不是有效签名都阻止发布。
8. 生成 SHA-256 清单、构建信息和 Git 提交信息。
9. 只有测试、签名验证和发布物审计全部通过后，才允许创建 GitHub Release。

SignPath 组织、项目和签名策略标识通过 GitHub repository variables 配置。SignPath Foundation 尚未批准项目前，工作流允许执行测试和未签名构建审计，但签名门未通过时必须停止，不能发布正式 Release。

## 供应链与发布可追溯性

- 依赖版本进入可审查的锁定文件，构建不得使用无上限的“最新版本”。
- GitHub Actions 使用最小权限：默认只读；发布 Job 单独获得创建 Release 所需权限。
- 第三方 Action 固定到完整提交 SHA，不只固定浮动 Tag。
- Release 同时发布 MSI、SHA-256 清单、签名验证命令、源码 Tag 和简短构建说明。
- MSI 仍不得包含 `config.ini`、`credential.dat`、密码文件或日志。

## Agent 健康模型

“开机自启动已启用”和“系统代理正在工作”拆成两个独立状态：

- `disabled`：Tray 任务不存在。
- `starting`：Tray 任务存在，`runtime.json` 尚未出现或仍处于允许的启动窗口。
- `healthy`：快照格式有效、更新时间未过期，且最近一次 IPC 状态请求成功。
- `degraded`：Tray 任务存在，但快照过期、Agent 管道不可用或 IPC 超时。

快照过期阈值为 `max(3 * check_interval_seconds, 120 秒)`，避免短暂网络请求或系统负载导致误报。GUI 继续每秒读取本地快照，但 IPC 探测放在后台线程并限频，不能阻塞 Tk 主线程。

进入 `degraded` 后：

- 明确显示“系统认证代理未运行”，不再显示旧的 `already authenticated` 状态。
- “检测一次”不得无限等待不存在的管道。
- 保存配置仍可完成本地持久化，但必须报告 Agent 未收到重载命令。
- 提供“修复系统代理”动作，复用管理员任务注册入口重新创建 SYSTEM/Tray 任务；修复后轮询 Agent 管道和新快照，给出成功或失败结果。
- 不自动降级为普通用户后台认证，避免出现两个认证循环或削弱登录前认证语义。

## 错误处理

- UAC 被取消：保留现有任务和配置，显示“未获得管理员批准”，不报告修复成功。
- Defender 再次隔离：停止自动重试，显示检测名称、受影响组件和官方误报提交说明。
- SignPath 不可用或审批未完成：保留可验证的未签名构建产物供内部测试，但禁止正式发布。
- 签名验证失败、时间戳缺失或版本不一致：构建失败，不上传 Release。
- Microsoft 误报申诉未完成：不把“已解决误报”写入公开发布说明。

## 测试设计

### 单元测试

- Tray 任务存在但快照过期时返回 `degraded`。
- 快照在启动宽限期内时返回 `starting`。
- 快照新鲜且 IPC 成功时返回 `healthy`。
- 快照新鲜但 IPC 超时时返回 `degraded`。
- 保存配置在 Agent 不可用时仍保存成功，同时返回明确的重载失败状态。
- 修复动作只调用现有提升权限注册入口，不创建 Defender 目录排除项。
- 单一版本源正确生成两个 PE 版本资源和 WiX 产品版本。
- 发布审计拒绝未签名、签名无效、版本不一致或缺少时间戳的文件。

### 构建验证

- `python -m unittest discover -s tests -v` 全部通过。
- Python 编译检查通过。
- PyInstaller 输出包含两个带正确版本元数据的 EXE。
- WiX 校验通过，MSI 不包含任何运行时敏感文件。
- 三个发布物的 Authenticode 状态均为 `Valid`，签名者和时间戳符合 SignPath 策略。

### 干净机验收

在未配置 Defender 排除项、病毒库已更新的 Windows 环境中执行：

1. 下载并核对 Release SHA-256。
2. 验证 MSI 签名和发布者。
3. 安装、保存凭据并启用系统级启动。
4. 检查 SYSTEM Agent、Tray、命名管道、快照和日志。
5. 重启，确认登录前 Agent 启动及登录后隐藏 Tray。
6. 查询 Defender 检测历史，确认没有新增 youziauth 检测。
7. 人为停止 Agent，确认 GUI 进入 `degraded`，不继续展示陈旧在线状态，并能通过修复动作恢复。

## 完成标准

- 当前电脑在不关闭 Defender 和不添加目录排除项的情况下通过安装、认证、IPC、日志和重启验收。
- Microsoft 已接收当前 EXE/MSI 的软件开发者误报提交，并保留可追踪提交编号。
- SignPath Foundation 申请材料完整；获得批准后，正式 Release 的两个 EXE 和 MSI 均具有有效签名和时间戳。
- GitHub Release 可追溯到唯一 Git Tag、提交、版本和 SHA-256 清单。
- GUI 能正确区分任务启用与 Agent 健康状态，已失效 Agent 不再被显示为正常。
- 全部自动化测试、构建审计和无白名单干净机验收通过后，才可声明发布问题解决。
