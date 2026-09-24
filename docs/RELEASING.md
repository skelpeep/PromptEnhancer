# 上传到 GitHub 与发布版本

当前版本为 `1.1.0`。根目录的 `VERSION` 是唯一版本来源，发布标签必须与之对应，即
`v1.1.0`。后续发布时，请把本文命令中的示例版本替换为新版本。

## 1. 检查源码并构建发行包

在项目根目录执行以下命令。打包需要 Windows x64 和包含 Tcl/Tk 的 Python 3.12，
建议使用 python.org 官方安装包。CI 还会在 Windows、Ubuntu 上检查 Python 3.10 和 3.12。

```powershell
python -m unittest discover -s tests -v
python -m compileall -q core.py cli.py proxy.py mcp_server.py win_core.py hotkey.py png_util.py desktop tools tests
python tools/make_release.py --smoke-source
python -m pip install -r requirements-build.txt
python tools/make_release.py --build
```

Linux CI 检查核心逻辑、CLI、代理、MCP 和使用模拟加密接口的存储测试；Windows 专用
测试会明确跳过。UI 单元测试不创建窗口，未安装 Tkinter 的 Python 会明确跳过该模块。
桌面启动与退出检查仅在 Windows 执行，Linux CI 通过不表示桌面程序支持 Linux。

打包默认会在隔离的数据目录中检查程序能否正常启动和退出，不读取你的 API Key，
也不发送 API 请求。这项检查不覆盖特定编辑器的选择、粘贴和撤销行为；发布前请在
普通编辑器中手动验证这些操作，并测试请求等待期间切换窗口或继续输入的情况。

构建完成后会生成：

- `dist/PromptEnhancer.exe`：Windows 可执行程序。
- `dist/PromptEnhancer.build.json`：本次构建的输入文件哈希和构建工具版本。
- `release/PromptEnhancer-1.1.0-win64.zip`：供用户下载的发行包。
- `release/SHA256SUMS.txt`：发行包的 SHA256 校验和。
- `release/RELEASE_NOTES.md`：可直接用于 GitHub Release 页的发布说明。
- `release/PromptEnhancer-1.1.0-win64/`：校验后解压的副本，方便本地检查。

如果解压目录已存在，脚本会创建带后缀的新目录。旧发行文件不会自动删除，上传时只选择
本次准备发布的版本。

打包参数说明：

- `--no-extract`：不保留解压副本，仍校验压缩包内容。
- `--no-smoke`：跳过启动和退出检查，不能把这种构建视为已经完成运行验证。
- `--version v1.1.0`：核对预期版本，不能借此把旧程序重新标成新版本。

内容校验和构建清单校验始终执行；任何构建输入发生变化后，都需要重新构建。
ZIP 内包含程序、使用说明、许可证、更新日志和构建信息。压缩包元数据时间默认固定，
也可通过 `SOURCE_DATE_EPOCH` 指定。这不保证不同机器上的 PyInstaller 构建逐字节一致：
虽然 PyInstaller 版本已经锁定，间接依赖和运行器更新仍可能影响二进制文件。

## 2. 上传源码仓库

先在 GitHub 创建一个空仓库，不勾选自动生成 README、许可证或 `.gitignore`；
本地项目已包含这些文件。

检查当前改动后，执行以下首次上传命令。将地址中的 `YOUR-ACCOUNT` 和
`YOUR-REPOSITORY` 替换成你的 GitHub 账号与仓库名。`git branch -M main` 用于命名
初始分支；已有仓库应继续使用原来的分支约定。

```powershell
git status --short
git add .
git diff --cached --stat
git diff --cached
git commit -m "Prepare Prompt Enhancer 1.1.0"
git branch -M main
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin main
```

如果已经配置了 `origin`，先运行 `git remote -v` 核对地址，直接使用正确的现有远程仓库，
不要重复执行 `git remote add`。如果 Git 要求填写作者信息，请先配置你希望使用的
Git 用户名和邮箱，再提交。

提交前检查暂存区差异，确认不包含 `config.env`、本地设置和历史、日志、虚拟环境、
`tools/dev/out/`、`build/`、`dist/` 或 `release/`。`.gitignore` 已排除这些内容。
如果敏感文件曾被 Git 跟踪，添加忽略规则无法从历史提交中移除它们；曾暴露的凭据
需要在公开仓库之前更换。

## 3. 使用 GitHub Actions 自动发布

1. 在新仓库中启用 Actions，等待 `CI` 工作流通过。
2. 如需先试构建，在 Actions 中打开 `Windows Release`，选择 **Run workflow**。
   手动运行只生成并验证构建产物，不创建 GitHub Release。
3. 确认 `VERSION` 和 `CHANGELOG.md` 中对应版本的记录都已更新并提交，再创建并推送标签：

```powershell
git tag -a v1.1.0 -m "Prompt Enhancer 1.1.0"
git push origin v1.1.0
```

推送 `v*` 标签后，工作流会检查标签是否等于 `v` 加上 `VERSION`，随后执行测试、构建
Windows 程序、校验压缩包和隔离启动/退出，最后创建正式的 GitHub Release，附上 ZIP
和校验和文件。Release 的可见范围跟随仓库设置。

构建和 PR 测试只具有 `contents: read` 权限，单独的发布任务具有 `contents: write`
权限。默认 `GITHUB_TOKEN` 即可完成发布，不需要把个人访问令牌提交到仓库或写入工作流。
组织策略可能限制 Actions 或发布权限；失败原因会保留在 Actions 的运行记录中。

不要用已发布的版本标签指向不同代码。修复后应更新 `VERSION`、添加更新日志并发布新标签。
如果任务在创建 Release 之前失败，可以使用 GitHub 的 **Re-run failed jobs** 重试。

## 4. 手动创建 Release

也可以先在本地构建，然后推送匹配的版本标签，在 GitHub 网页上创建 Release：选择
该标签，粘贴 `release/RELEASE_NOTES.md`，上传对应版本的 ZIP 和 `SHA256SUMS.txt`。

自动发布与手动发布选择一种即可。选择手动方式时，请在推送标签之前禁用
`Windows Release` 工作流，避免两种方式同时创建相同 Release。

GitHub 自动生成的 **Source code** ZIP 和 TAR.GZ 只包含标签对应的源码，不含桌面
可执行程序。请在 Release 说明中明确引导用户下载 Windows 发行包。

下载后可用 PowerShell 校验：

```powershell
Get-FileHash .\PromptEnhancer-1.1.0-win64.zip -Algorithm SHA256
```

将输出与 Release 页 `SHA256SUMS.txt` 中的数值对照。当前构建没有代码签名证书；
校验和用于检查文件完整性，不等同于代码签名。
