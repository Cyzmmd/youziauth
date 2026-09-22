# SignPath Foundation 申请与仓库配置

## 申请资料

在 [SignPath Foundation 申请页面](https://signpath.org/apply.html) 提交以下项目信息：

| 项目 | 值 |
|---|---|
| Project | `youziauth` |
| Repository | `https://github.com/Cyzmmd/youziauth` |
| License | `GPL-3.0-only` |
| Maintainer / publisher display name | `yoouzic` |
| Release artifact | `youziauth.msi` |
| Signing scope | MSI 内的 `youziauth.exe`、`youziauth-agent.exe`，以及外层 MSI |

申请前确认仓库公开、已有可下载版本、README 准确说明程序行为，并符合 [SignPath Foundation 开源项目条件](https://signpath.org/terms.html)。免费证书的 Authenticode 发布者通常显示为 SignPath Foundation，而项目身份由签名请求的仓库来源证明关联。

## SignPath 项目配置

1. 创建或获批项目 `youziauth`，仓库 URL 填写 `https://github.com/Cyzmmd/youziauth`。
2. 创建 artifact configuration，slug 使用 `windows-msi-deep-signing`，内容以仓库的 `.signpath/artifact-configuration.xml` 为准。
3. 创建 signing policy，slug 使用 `release-signing`，开启 trusted build system verification 与 origin verification。
4. 将预定义的 GitHub.com Trusted Build System 链接到项目。
5. 按 [SignPath GitHub 集成说明](https://docs.signpath.io/trusted-build-systems/github)安装 SignPath GitHub App，并只授权本仓库。
6. 确认仓库默认分支包含 `.signpath/policies/youziauth/release-signing.yml`；该策略仅允许 GitHub 托管 runner，且禁止重跑旧构建后签名。

## GitHub 仓库配置

在 GitHub 仓库设置中配置：

- Repository variable：`SIGNPATH_ORGANIZATION_ID`
- Repository secret：`SIGNPATH_API_TOKEN`
- Environment：`signing`，条件允许时启用人工审批

令牌只能保存在 GitHub Secret 中，不得写入源文件、文档、构建产物或日志。首次发布前先确认 `.github/workflows/release.yml` 中的 project、policy 和 artifact configuration slug 与 SignPath 控制台一致。

## 首次签名验收

合并经审查的代码后再创建与 `VERSION` 完全一致的标签。工作流必须先构建并上传 GitHub artifact，由 SignPath 返回深度签名的 MSI，再运行 `packaging/verify_release.ps1`。只有外层 MSI、两个内嵌 EXE 的签名、时间戳和版本都通过时，工作流才会创建 Release。

在 SignPath 审批、仓库变量、Secret 和 GitHub App 都配置完成之前，不要创建生产发布标签，也不要把 CI 生成的 unsigned artifact 当作正式安装包分发。
