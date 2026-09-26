# 自动更新与签名发布的当前阻断项

记录时间为 2026-09-26。本文只登记"卡在哪里、缺什么、按什么顺序补齐"。发布签名与密钥管理的操作说明见 [release-signing.md](release-signing.md)，历史设计背景见 [trusted-release-design.md](trusted-release-design.md)。

## 一句话结论

**自动更新已经不再被证书阻塞。** 客户端改为内置 Ed25519 公钥、发布时用离线私钥签名安装包，因此不需要 Authenticode 证书即可完成可验证的下载→校验→安装闭环。SignPath Foundation 申请被拒只影响"首次安装的发布者身份"，不影响自动更新。

仍然存在的唯一硬约束：**首个 Ed25519 版本必须手动安装一次**，因为 1.6.6 及更早版本里没有新公钥，也不信任未签名安装包。

## 本轮变化

| 原编号 | 原现象 | 现状 |
|---|---|---|
| B1 | 发布工作流在 `Submit SignPath request` 失败 | **已消除**：工作流不再依赖 SignPath，改为本地 Ed25519 签名 |
| B2 | SignPath 无可用项目（申请未获批） | **不再是阻断项**：申请被拒，改为自持密钥方案，未获批准不影响发布 |
| B3 | 仓库无 `SIGNPATH_*` 配置项 | **已消除**：不再需要，改为 `YOUZIAUTH_RELEASE_KEY` 一个 Secret |
| B4 | 应用内检查更新必然失败（缺校验资产） | **已消除**：新发布流程产出 4 个资产，客户端要求齐全 |
| B5 | 1.4.4/1.6.6 无法自动升级到签名版 | **降级为一次性操作**：手动安装首个 Ed25519 版本，此后自动更新可用 |

## 仍然需要人工做的一次性动作

1. **配置 `YOUZIAUTH_RELEASE_KEY` Secret**（见下）。
2. **离线备份私钥**，至少两份。丢失私钥意味着无法再发布可自动更新的版本。
3. **手动安装一次首个 Ed25519 版本**（跨过 B5）。
4. 可选：`VERSION` 递增后打标签触发发布。

## 密钥与 Secret

- 公钥已内置：`windows_update.PUBLIC_KEY_B64` = `/sxOMzShO28Jx4h/qwna3KrN9kTqy3x6DthzsyPf1O4=`。
- 私钥当前存放在本机仓库目录下的 `.release-key/ed25519-release.key`（已被 `.gitignore` 忽略）。**发布前请把它移到仓库之外的安全位置**，仓库目录里的副本只应视为临时产物。
- GitHub Actions Secret 名：`YOUZIAUTH_RELEASE_KEY`，内容为私钥种子的 base64（即密钥文件里那一行）。
- 签名工具会在签名前校验私钥与内置公钥是否匹配，不匹配直接失败，不会产出用户无法安装的版本。

## 本机现状

- 已安装版本：1.6.6，未签名（`Get-AuthenticodeSignature` → `NotSigned`）。
- 本地构建产物：`dist/youziauth.msi`，MSI `ProductVersion` = **1.6.6**（与 `VERSION` 一致），
  SHA-256 `b8ce52bb06cebcaf167bc93c8c54005856d4367b163e10c5cb1c7ef1ccd46d12`。
  已用本轮密钥签名并通过验签，可作为首个 Ed25519 版本的候选。
- 寝室模块的 `location_source` 仍为 `simulation`，自动打卡窗口内提交的是已保存的示例坐标。
  要用真实定位需先切回真实来源。

## 已完成的验证

除 566 项单元测试外，本轮在真实环境里跑通了整条链路（不需要私钥即可复现验签部分）：

| 验证 | 结果 |
|---|---|
| 真机对 `dist/youziauth.msi`（71 MB）签名 | 产出 `SHA256SUMS.txt`、`youziauth.msi.ed25519`、`release-provenance.json` |
| `tools/sign_release.py --verify-signature-file` | 通过（只用公钥） |
| 真机 `_msi_properties` 读取 MSI 属性 | 四项全部匹配（只读 COM，无 Authenticode） |
| 篡改签名 / 换密钥 / 改版本 / **降级到 1.6.6** / 改安装包字节 | 分别以退出码 12/12/12/12/15 拒绝 |
| 真实 PowerShell worker 全链路（仅把 msiexec 目标换成空操作） | 通过：独占文件锁 → 重算摘要 → 产品属性 → Ed25519 验签 → 发布 `launch.json` → 交接安装向导，回报取消码 1602 |

最后一项是关键证据：worker 在**持锁**状态下完成了全部校验，并把安装交给安装向导；
由于交接过程使用的是空操作目标，本机没有被安装任何东西。

## 补齐顺序

1. 把私钥移出仓库目录并离线备份。
2. 在 GitHub 仓库设置里添加 Actions Secret `YOUZIAUTH_RELEASE_KEY`。
3. 递增 `VERSION`，打 `v*.*.*` 标签，确认工作流绿色且 Release 含 4 个资产。
4. 手动安装一次该版本（跨过 B5）。
5. 再发一个补丁版本，用应用内「检查更新」验证完整自动更新链路。

## 完成判据

- `python -m unittest discover -s tests` 全部通过（当前 561 项）。
- 发布工作流绿色，Release 同时具备 `youziauth.msi`、`SHA256SUMS.txt`、`youziauth.msi.ed25519`、`release-provenance.json`。
- `packaging/verify_release.ps1` 通过：两个 EXE 的版本元数据正确，发布签名确实覆盖安装包字节。
- 安装首个 Ed25519 版本后，应用内「检查更新」能解析到新版本，并在应用内完成下载、校验与安装。

## 若以后仍想解决首次安装的警告

证书只影响 Windows 对首次安装的态度（UAC 发布者名称、SmartScreen、Smart App Control），与自动更新无关。可选路径与成本见 [release-signing.md](release-signing.md) 的「证书（可选，不阻塞更新）」一节。

需要特别注意：

- SignPath Foundation 的门槛是**声誉**而非资质。被拒理由是需要外部第三方信号（文章、讨论、收录、下载量）。条件积累后可以重新申请，Microsoft 官方文档也推荐该项目。
- Azure Trusted Signing（现名 Azure Artifact Signing）对中国大陆个人**不可用**：个人身份验证仅限美国与加拿大。
- EV 证书自 2024 年起不再享有 SmartScreen 特权，不必为此多付费。
- 任何新证书都不改变本仓库的更新信任模型——客户端只认内置公钥。
