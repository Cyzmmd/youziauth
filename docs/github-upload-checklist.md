# GitHub 开源上传清单

## 应上传到源码仓库

- 根目录 Python 源码：`*.py`
- Windows 启动和安装辅助脚本：`*.ps1`、`*.vbs`
- 离线单元测试：`tests/*.py`
- 打包输入：`packaging/*.py`、`packaging/*.spec`、`packaging/*.wxs`
- 应用图标与项目自有图片：`assets/`
- 示例配置：`config.example.ini`
- 项目文档：`README.md`、`SECURITY.md`、`COPYRIGHT`、`LICENSE`
- 公共验证记录：`docs/release_audit.md`
- 仓库忽略规则：`.gitignore`

## 不得上传到源码仓库

- 真实配置：`config.ini`
- 本地密码和凭据：`campus_auth_password.txt`、`credential.dat`
- 运行日志：`campus_auth.log`、其他 `*.log`
- 完整登录 URL、学号、密码、MAC、个人 IP 或未经脱敏的截图
- 本地工具缓存：`.tools/`
- 构建产物：`build/`、`dist/`、`*.msi`
- Python 缓存、测试缓存和虚拟环境
- 内部设计与执行记录：`docs/superpowers/`

## GitHub Releases

MSI 安装包只作为 GitHub Release 附件发布，不提交到源码仓库。每次发布建议同时提供：

- 版本号和主要变更；
- `youziauth.msi`；
- `SHA256SUMS.txt`、`youziauth.msi.ed25519` 和 `release-provenance.json`；
- 发布签名的校验方法（见 [release-signing.md](release-signing.md)）；四个附件缺一不可，
  客户端的 `app_update.RELEASE_ASSETS` 会要求它们齐全；
- 支持的 Windows 和 Python 版本；
- 已知限制以及“非官方项目”声明。

## 上传前检查

1. 确认 `.gitignore` 已覆盖所有本地敏感文件和生成目录。
2. 对预计上传的文本文件运行敏感信息扫描。
3. 运行全部单元测试和 Python 编译检查。
4. 检查 `config.example.ini` 只有占位账号且密码为空。
5. 检查 README 包含西南大学适用范围、非官方声明、安全边界和 GPL-3.0-only。
6. **已确认：** `assets/` 中的图片和图标由项目作者 `yoouzic` 生成并持有版权，随项目按 GPL-3.0-only 开源发布。
7. 发布 MSI 前核对 Python、Tcl/Tk、PyInstaller、Pillow 等随安装包分发组件的许可证，并随 Release 提供必要的第三方版权与许可证说明。
8. 使用 `git status` 和 `git diff --cached` 人工复核首次提交的每一个文件。
9. 确认 Git Tag 与 `VERSION` 一致，且 Release 工作流在**构建后签名、验证通过才发布**，发布的是 `dist/youziauth.msi` 以及由 `tools/sign_release.py` 生成的三个附件。
10. 下载 Release 附件复核发布签名确实覆盖该安装包（`packaging/verify_release.ps1` 会做同样的事），并核对 `SHA256SUMS.txt` 和 `release-provenance.json` 后再公开推广。
11. 确认私钥没有进入仓库：`git ls-files` 不应出现任何 `.key` 文件（测试会代为检查）。
