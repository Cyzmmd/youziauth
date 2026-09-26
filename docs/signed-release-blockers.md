# 自动更新与签名发布的当前阻断项

记录时间为 2026-09-26。本文只登记"卡在哪里、缺什么、按什么顺序补齐"。发布签名与密钥管理的操作说明见 [release-signing.md](release-signing.md)，历史设计背景见 [trusted-release-design.md](trusted-release-design.md)。

## 一句话结论

**自动更新已经不再被证书阻塞，并且已经发出第一个可验证的正式版本 `v1.6.6`。** 客户端内置 Ed25519 公钥、发布时用离线私钥签名安装包，因此不需要 Authenticode 证书即可完成可验证的下载→校验→安装闭环。SignPath Foundation 申请被拒只影响"首次安装的发布者身份"，不影响自动更新。

唯一的剩余动作是一项人工操作：**手动安装一次 `v1.6.6`**。1.6.6 之前的版本里没有新公钥，也不信任未签名安装包，无法自举。

## v1.6.6 发布记录（2026-09-26）

- Git Tag / 提交：`v1.6.6` → `98e8e244107ff54e76ef1a2f47c065f14b2887e1`（`VERSION` = 1.6.6）。
- 工作流 `Signed Windows release` run `36230044960` **全部步骤通过**：
  配置检查 → 安装依赖 → 测试 → Tag/VERSION 一致 → 构建 MSI → 用发布密钥签名 → 验证签名 → 发布 Release。
- 该 Release 同时具备 4 个资产：

  | 资产 | 值 |
  |---|---|
  | `youziauth.msi` | 52,949,816 字节，SHA-256 `82fddcb403518bfbe4e0d71acf2c1dc1f0b9857537b51bb3071de079f1eea6c5` |
  | `SHA256SUMS.txt` | 与上面摘要一致 |
  | `youziauth.msi.ed25519` | `a3d1c80090a66ceeb545444653282603877f6e12657ee98e95d76c3b7d044b2d959a5f7f765d43c08e08c679bf9de185710bd632e662993ef43e9a89d9161e0e` |
  | `release-provenance.json` | 记录提交、Tag、摘要与所用公钥 |

- 下载回来的发布物已独立复核（不需要私钥）：
  - 文件大小与 SHA-256 与 `SHA256SUMS.txt`、`release-provenance.json` 三方一致；
  - `tools/sign_release.py --verify-signature-file` 用内置公钥验证通过；
  - MSI 四项产品属性（ProductName / Manufacturer / ProductVersion / UpgradeCode）与预期一致；
  - `windows_update.verifier_main` 对该发布包返回 0（接受）；
  - 客户端自身的 `app_update.release_assets` / `read_checksum` / `read_signature` 能解析该 Release 的全部资产。

## 本轮变化

| 原编号 | 原现象 | 现状 |
|---|---|---|
| B1 | 发布工作流在 `Submit SignPath request` 失败 | **已消除**：工作流不再依赖 SignPath，改为本地 Ed25519 签名 |
| B2 | SignPath 无可用项目（申请未获批） | **不再是阻断项**：申请被拒，改为自持密钥方案，未获批准不影响发布 |
| B3 | 仓库无 `SIGNPATH_*` 配置项 | **已消除**：不再需要，改为 `YOUZIAUTH_RELEASE_KEY` 一个 Secret |
| B4 | 应用内检查更新必然失败（缺校验资产） | **已消除**：新发布流程产出 4 个资产，客户端要求齐全 |
| B5 | 1.4.4/1.6.6 无法自动升级到签名版 | **降级为一次性操作**：手动安装首个 Ed25519 版本，此后自动更新可用 |

## 仍然需要人工做的一次性动作

1. ~~配置 `YOUZIAUTH_RELEASE_KEY` Secret~~ **已完成**（2026-09-26 由 `gh secret set` 写入）。
2. ~~离线备份私钥~~ **已完成**（2026-09-26）：私钥已移出仓库，落在
   `%USERPROFILE%\.youziauth-release-key\` 与 `%LOCALAPPDATA%\youziauth-release-key-backup\`，
   两份均校验为同一 SHA-256，ACL 已收紧为仅当前用户。**建议再存一份到本机之外的介质。**
3. **手动安装一次 `v1.6.6`**（跨过 B5）——从 Release 页面下载并按提示安装。
   安装前可先核对：MSI 大小 52,949,816 字节，SHA-256
   `82fddcb403518bfbe4e0d71acf2c1dc1f0b9857537b51bb3071de079f1eea6c5`。
4. ~~递增 `VERSION`、打标签触发发布~~ **已完成**：`v1.6.6` 由
   `98e8e244107ff54e76ef1a2f47c065f14b2887e1` 构建并签出。

## 密钥与 Secret

- 公钥已内置：`windows_update.PUBLIC_KEY_B64` = `/sxOMzShO28Jx4h/qwna3KrN9kTqy3x6DthzsyPf1O4=`。
- 私钥已在仓库之外，仓库里不再保留任何私钥材料（`.release-key/` 已删除）：
  - 主位置：`%USERPROFILE%\.youziauth-release-key\ed25519-release.key`
  - 备份：`%LOCALAPPDATA%\youziauth-release-key-backup\ed25519-release.key`
  - 密钥文件 SHA-256：`E8A62C0FD7531398B1686FAF0FC431B9DBF4782642C35E1E5CB4CCF90D9A4111`
  - 两份均只授权当前用户读写（`icacls /inheritance:r`）。
  - **仍未做**：一份离开本机的离线备份。
- GitHub Actions Secret `YOUZIAUTH_RELEASE_KEY` 已配置，`v1.6.6` 的签名即由它完成。
- 签名工具会在签名前校验私钥与内置公钥是否匹配，不匹配直接失败，不会产出用户无法安装的版本。

## 本机现状

- 本机已安装版本仍是 1.6.6，但那是**旧的未签名构建**（`Get-AuthenticodeSignature` → `NotSigned`），
  不含内置公钥，因此它检查更新只会得到"请使用官方安装版"的提示。需要手动安装新发布的 `v1.6.6`。
- 旧候选产物 `dist/youziauth.msi`（71,436,084 字节）与正式 Release 的 MSI（52,949,816 字节）不同：
  正式版由 CI 在干净环境构建，体积更小。以 Release 附件为准。
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

1. **把私钥移出仓库目录并离线备份**（唯一未完成事项）。
2. ~~配置 Actions Secret~~ 已完成。
3. ~~递增 `VERSION` 并打标签发布~~ 已完成（`v1.6.6`）。
4. **手动安装 `v1.6.6`**（跨过 B5）。
5. 再发一个补丁版本，用应用内「检查更新」验证一次完整的自动更新。

## 完成判据

- `python -m unittest discover -s tests` 全部通过（当前 566 项）。**已完成**
- 发布工作流绿色，Release 同时具备 `youziauth.msi`、`SHA256SUMS.txt`、`youziauth.msi.ed25519`、`release-provenance.json`。**已完成**
- `packaging/verify_release.ps1` 通过：两个 EXE 的版本元数据正确，发布签名确实覆盖安装包字节。**已完成**
- 发布物可由客户端独立验签（大小/SHA-256/签名/产品属性四项一致，`verifier_main` 返回 0）。**已完成**
- 安装 `v1.6.6` 后，应用内「检查更新」能解析到新版本，并在应用内完成下载、校验与安装。**待完成**（需要第 4、5 步）

## 若以后仍想解决首次安装的警告

证书只影响 Windows 对首次安装的态度（UAC 发布者名称、SmartScreen、Smart App Control），与自动更新无关。可选路径与成本见 [release-signing.md](release-signing.md) 的「证书（可选，不阻塞更新）」一节。

需要特别注意：

- SignPath Foundation 的门槛是**声誉**而非资质。被拒理由是需要外部第三方信号（文章、讨论、收录、下载量）。条件积累后可以重新申请，Microsoft 官方文档也推荐该项目。
- Azure Trusted Signing（现名 Azure Artifact Signing）对中国大陆个人**不可用**：个人身份验证仅限美国与加拿大。
- EV 证书自 2024 年起不再享有 SmartScreen 特权，不必为此多付费。
- 任何新证书都不改变本仓库的更新信任模型——客户端只认内置公钥。
