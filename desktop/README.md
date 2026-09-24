# 桌面版使用说明

桌面版通过 Windows 全局快捷键捕获选中文字，把它发送到配置的 API 服务，再按设置自动粘贴或复制到剪贴板。

## 配置与增强

首次启动填写 API 地址、API Key 和模型名称。地址通常形如 `https://api.example.com/v1`，服务需兼容 Chat Completions。测试连接会发送一次实际请求，可能产生费用；保存并应用后生效。

默认快捷键为 `Ctrl+Alt+E` 增强、`Ctrl+Alt+Z` 撤销、`Ctrl+Alt+O` 打开设置。快捷键录入框支持直接按组合键，`Esc` 清空并禁用对应快捷键。只填修饰键或重复的组合键无法正常工作，界面会给出校验提示。

增强期间切换窗口或发生键鼠操作时，结果改为复制到剪贴板，方便你在合适位置手动粘贴。如果期间复制了新内容，程序会保留新剪贴板，增强结果可从历史页复制。

关闭设置窗口会继续在后台运行；托盘菜单提供设置、暂停或恢复、撤销和退出操作。

## 模板与历史

可以编辑 system 和 user 模板，user 模板必须保留 `{input}` 占位符。恢复默认模板后仍需保存。

历史页记录最近的增强结果，可以查看、复制原文或结果、重新增强以及清空记录。重新增强的结果用于查看或复制，不会自动覆盖当前编辑器的内容。

恢复默认设置会保留 API Key；保存前可以取消修改。保存失败时应先处理错误并重试，未保存的修改不会成为运行中的新配置。API Key 加密失败会阻止保存，避免写入明文凭据。

## 撤销与剪贴板

完成自动粘贴后，撤销会尝试让原目标窗口执行原生 `Ctrl+Z`，同时把原文放到剪贴板。未自动粘贴、超过有效窗口或不符合撤销条件时，只复制原文。

目标应用如何组织撤销步骤由应用自己决定。如果增强后又编辑了其他文字，原生撤销可能先撤回后续编辑。需要准确恢复时，从历史复制原文并手动替换。

“没有选中文本时使用剪贴板”默认关闭；开启后可能使用上一次复制的文字。

## 数据位置

| 文件 | 用途 |
|---|---|
| `%APPDATA%\PromptEnhancer\settings.json` | 配置；API Key 用 Windows DPAPI 保护 |
| `%APPDATA%\PromptEnhancer\history.json` | 包含原文和结果的本地历史 |
| `%APPDATA%\PromptEnhancer\instance.json` | 运行中实例信息 |
| `%APPDATA%\PromptEnhancer\app.log` | 运行诊断日志 |

这些数据不会随 exe 目录移动。分享诊断信息前去除凭据和私人文本。DPAPI 依赖当前 Windows 用户，复制配置到其他用户或机器后可能需要重新填写 API Key。

## 从源码或脚本启动

需要 Windows、Python 3.10+ 和 Tcl/Tk。

```powershell
python desktop/main.py --show
python desktop/main.py --hidden
python desktop/main.py --enhance
python desktop/main.py --undo
python desktop/main.py --quit
```

`--enhance`、`--undo`、`--quit` 向已运行的实例发送命令，可用于键盘宏或快捷方式。`--hidden` 用于后台启动；配置不完整时仍可能显示设置。

## 常见问题

- 快捷键冲突：打开设置换用其他组合，或使用托盘操作。
- 无法复制或粘贴：确认目标允许文本编辑、窗口保持前台，并留意目标程序与增强器的权限差异。
- 无法连接：检查 API 地址、模型权限、Key 和网络；查看界面错误信息。
- 更换版本后仍显示旧版本：退出托盘里的旧进程，再启动新解压目录中的 exe。

构建与发布步骤见 [发布指南](../docs/RELEASING.md)，开发与测试见 [贡献指南](../CONTRIBUTING.md)。
