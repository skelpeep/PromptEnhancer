# v1.1.0 本地验证记录

验证日期：2026-09-24。环境：Windows 11 x64、Python 3.12.8、Tk 8.6、PyInstaller 6.22.3。
网络测试使用本地模拟服务；没有调用真实模型服务。桌面检查使用临时配置目录。

| 检查 | 结果 |
|---|---|
| `python -m unittest discover -s tests -v` | 69 / 69 通过 |
| `python tools/dev/status_and_ui_check.py` | 27 / 27 通过 |
| `python tools/dev/ui_walkthrough.py` | 3 张设置页截图已更新并检查 |
| `python tools/e2e_test.py` | 31 / 31 通过 |
| `python tools/make_release.py --smoke-source` | 隔离启动、退出通过 |
| `python tools/make_release.py --build` | EXE、压缩包内容、构建清单与启动检查通过 |
| `python tools/e2e_test.py --exe dist/PromptEnhancer.exe` | 最终隔离复测 31 / 31 通过 |
| 源码编译检查 | 通过 |
| 发行 ZIP 的 SHA256 | 与 `release/SHA256SUMS.txt` 一致 |

重点覆盖：切换窗口或焦点控件、等待期间键鼠操作、新剪贴板内容保护、未粘贴结果的撤销、设置保存失败、损坏配置恢复、参数校验、完整 API URL、代理 SSE、MCP 异常输入及中文编码。

交互测试需要 Windows 桌面焦点。首次 EXE 完整测试触发键鼠活动保护，另一次未获得前台焦点；随后修复测试的独立实例、动态端口和 onefile 子进程清理，清理遗留测试实例后完整通过。该过程没有关闭产品的焦点或输入保护。

发行产物：`release/PromptEnhancer-1.1.0-win64.zip`（12,164,083 字节）。

```text
496a6ce848b738ee3ec3c8e294d0ed6375afd0a1ae90116b1fb492bf58f8627d
```

GitHub Actions 已配置，但尚未连接远程仓库或在 GitHub 执行。Linux、Python 3.10、Windows 10 及其他编辑器的实际兼容性仍由后续 CI 和目标环境验证；本记录不代表这些环境已在本机测试。

原生撤销由目标应用管理撤销栈，增强后继续编辑可能先撤回后续编辑。准确恢复可在历史页复制原文。
