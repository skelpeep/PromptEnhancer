# 踩坑记录

下面每一条都是本项目在真实环境里踩出来的，不是照抄文档 —— 所以现象和根因都对得上号。
遇到问题时按类别找。

---

## 一、网络与端口

| 现象 | 根因 | 处理 |
|---|---|---|
| 连 `127.0.0.1` 都报 `502 upstream connect failed` | 环境里被注入了 `http_proxy=http://127.0.0.1:12619`，连本地请求也被代理转发 | `core.build_opener()` 对 localhost 一律直连，并尊重 `NO_PROXY`；也可用 `ENHANCER_NO_PROXY=1` 强制所有请求不走系统代理 |
| 监听 8899 报 `WinError 10013` | 该端口落在 Windows 的保留/排除段（Hyper-V、WSL 会占用） | 默认端口改用 18080（假上游）/ 18765（代理），避开保留段 |

## 二、全局热键

| 现象 | 根因 | 处理 |
|---|---|---|
| 热键注册失败，错误码 `1409` 或返回 0 | 该组合已被别的软件占用（本机实测 `ctrl+alt+s`、`ctrl+alt+p` 都被占） | 设置页会指明是哪一个失败；用 `python tools/probe_hotkeys.py` 查本机空闲组合 |
| 热键按了没反应 | 目标程序以**管理员权限**运行，普通权限进程发不了按键（Windows UIPI 限制） | 用管理员权限启动本工具 |
| **模拟按键按不出全局热键** | Windows **不用注入的输入**去匹配 `RegisterHotKey`（实测确认，`SendInput` 还会被静默拦截、返回 0 且不报错） | 自动化测试改走 `--enhance` / `--undo` 命令通道；**用户真实按键不受影响** |

## 三、Win32 / ctypes

| 现象 | 根因 | 处理 |
|---|---|---|
| `OverflowError: int too long to convert`（`DefWindowProc` / GDI 调用处） | `ctypes.wintypes.LPARAM/WPARAM` 在 x64 下**仍是 32 位**；GDI 句柄参数不声明 `argtypes` 会被当成 C int 传 | 自己用 `c_size_t / c_ssize_t` 定义 `WPARAM/LPARAM`；给所有 Win32 调用补 `argtypes` |
| 明明配好了 `argtypes` 却还报同样的错 | `ctypes.WinDLL()` **每次调用都返回新的 Python 对象**，`argtypes` 不跨模块共享 | 复用同一个模块里已配好的句柄（`from toast import user32, gdi32`），不要自己再 `WinDLL` 一次 |
| 屏幕截图抓不到右下角浮层 | 浮层是 `WS_EX_LAYERED` 窗口，DWM 合成下不在屏幕 DC 里，屏幕 `BitBlt` 拿不到 | 用 `PrintWindow(hwnd, memDC, PW_RENDERFULLCONTENT=0x2)` 让窗口画自己 |

## 四、界面与 DPI

| 现象 | 根因 | 处理 |
|---|---|---|
| 界面文字发虚、整体被拉伸 | 进程没声明 DPI 感知，Windows 直接对窗口位图做拉伸 | 建 Tk 窗口**之前**调 `SetProcessDpiAwareness`，窗口默认尺寸按 DPI 缩放 |
| 浮层复用且尺寸变化时底部出现黑边 | 只 `InvalidateRect` 没 `UpdateWindow`，内容按旧尺寸绘制，多出来的那条没画到 | 改尺寸后补 `UpdateWindow`，并在 `WM_SIZE` 里 `InvalidateRect` |
| **浮层"一闪而过"：第一次正常，第二次之后几乎看不见** | `hide()` 只 `KillTimer(1)`，**淡出用的 2 号定时器残留**。它接着每 26ms 醒一次，下一轮 `show()` 刚设好停留计时就被它推进淡出（实测：第一轮 818ms，之后只剩 ~220ms） | `show()`/`hide()` 两个定时器都 `KillTimer`；`_on_timer` **按定时器编号**分支，残留的淡出定时器当场掐掉。回归：`python tools/dev/toast_duration_check.py` |
| 增强失败后左下角一直挂着"正在增强…" | `_on_enhance_done` 只在**部分分支**更新状态栏，失败 / 结果与原文相同 / 写剪贴板失败这几条路径漏了 | 所有出口统一走 `_set_status()`。回归：`python tools/dev/status_and_ui_check.py` |
| 窗口矮一点时，底部的「保存并应用」「恢复默认设置」**整个消失**（高 DPI 屏上尤其容易出现） | pack 按调用顺序分配空间，空间不够时**排在后面的控件直接被裁掉**；页面内容请求的高度是个常量 | 底部操作栏先 `pack(side="bottom")` 占位，页签区后 pack 并 `expand` 自己收缩；首开尺寸由 `_default_geometry()` 拿 `winfo_reqheight()` 兜底（必须 `_build()` 之后再量） |
| 复选框左边多出一个原生小方框，看着像两个框 | `make_checkbutton` 换了 `image`/`selectimage`，但没关 `indicatoron`，Tk 仍会画自己的指示器 | `indicatoron=0` |
| 托盘气泡通知不显示 | Win10/11 会**静默丢弃**未注册 AppUserModelID 的 `Shell_NotifyIcon` 气泡 | 改为自绘 GDI 浮层窗口（带 `WS_EX_NOACTIVATE`，绝不抢焦点——抢了会导致粘贴失效） |

## 五、模型调用

| 现象 | 根因 | 处理 |
|---|---|---|
| 某些模型报 400 / 参数不支持 | 推理模型可能不收 `temperature` / `max_tokens` | `core.enhance()` 会自动退一步、去掉可选参数重试一次 |
| 增强很慢、很贵 | 用了推理模型 | 改写任务不需要推理，换成便宜快的对话模型 |

## 六、各客户端的坑

| 现象 | 根因 | 处理 |
|---|---|---|
| Codex 里改了 prompt 文件不生效 | 自定义 prompt 只在**会话启动时**加载 | 新开一个会话 |
| Claude Desktop 改了 MCP 配置不生效 | 配置只在启动时读 | 完全退出再启动（关窗口不算） |
| MCP 配置写在 `%APPDATA%` 下无效 | 3P 部署版的数据目录在 `%LOCALAPPDATA%\Claude-3p\` | 见主 README 第六节的路径说明 |

## 七、测试与自检

| 现象 | 根因 | 处理 |
|---|---|---|
| 本机正开着正式实例时，`tools/e2e_test.py` 报"增强器后台启动 ❌" | 单实例互斥体名**全局固定**、不随 `APPDATA` 隔离，第二个进程直接去唤起已有实例然后退出，一个文件都不写 | 给测试进程设 `PROMPT_ENHANCER_INSTANCE_NAME` 另起命名空间（e2e 与发行包启动自检都已内置） |
| 用 `event_generate` 造的按键"按了没反应"，还不报错 | 目标控件在**未选中的页签**里 —— 未映射的窗口，Tk 会直接丢弃事件，不抛异常 | 先 `notebook.select(页签)` + `update()`，确认 `winfo_ismapped()` 为 1 再发事件 |
| 想把浮层停久一点以便观察/截图 | 默认只停 0.6s | 设 `PROMPT_ENHANCER_TOAST_MS=4000`；或直接用 `tools/dev/toast_probe.py`（它自己传 20s） |
| 自检脚本报 `ModuleNotFoundError: tkinter` | 托管版 Python 3.13 不带 tkinter | 用带 tkinter 的系统版 Python 3.12 跑界面/端到端脚本（`toast_duration_check.py` 只用 ctypes，两个都能跑） |
| `capture_check` 偶发 `A 抓到的是选中的草稿 — ''` | 靶窗口要靠 `--keep-foreground` 反复抢前台，**上一个自检刚弹过窗口 / 终端抢着焦点**时，几次重试都可能抢不到 | 单独跑它（别和别的会抢前台的 GUI 自检连着跑）；连跑两次正常即可确认是环境抖动而非功能回归 |

## 八、打包与分发

| 现象 | 根因 | 处理 |
|---|---|---|
| 明明重新打了包，用户打开的却还是旧界面 | **在压缩包里直接双击 exe**：资源管理器会把 exe 解压到 `%TEMP%\BNZ.<hex>\` 再运行，这份临时副本会被留用；压缩包同名覆盖后，双击拿到的仍是当初解压的那份旧 exe（本机实测：BNZ 目录创建于旧包那次，zip 已更新，里面 exe 还是旧的） | 使用说明里已写明「先解压再运行」；排查时看 `%TEMP%\BNZ.*` 里 exe 的哈希，和 zip 里的对一下 |
| 两份包版本号一样，分不清手里是哪一份 | 版本号忘了升，或同名覆盖重打 | 「关于」页和窗口标题栏都显示**版本 + 构建时间**，构建时间即 exe 的文件修改时间；启动日志里也有 `版本=… 构建=… 程序=<路径>` |
| 想确认某个改动有没有真打进 exe | onefile 的模块在 PYZ 里被 zlib 压缩，`grep exe` 搜不到源码里的中文常量 | 跑 `python tools/dev/exe_contains.py <exe>`：解出字节码按符号核对，逐项 ✅/❌；`make_release.py` 打包时已自动跑这一步，不通过就不出包 |
| 改了源码但记不清 dist 里那份是不是最新的 | exe 时间戳只能说明"构建过"，不说明"装了哪版" | 用上面那条按符号核对；或直接跑 `python tools/dev/exe_probe.py dist/PromptEnhancer.exe` 看它画出来的界面 |

### 8.1 「exe 明明是刚打的，跑起来还是旧版」——最费时间的一个坑

这个症状有**两个完全不同的根因**，静态核对（`exe_contains`）对第二个查不出来，
所以两条都要查。

**根因 A：跑的根本不是这个 exe（zip 解压缓存）。**
在压缩包里双击 exe，Windows 会解到 `%TEMP%\BNZ.<hex>\` 再运行，而且**一直复用**那份缓存 ——
换了新 zip 也没用，双击到的还是当初那份旧 exe。

**根因 B：跑的是这个 exe，但里面的模块被 `%TEMP%` 里的同名 `.pyc` 顶掉了。**
onefile 解包后入口脚本的 `__file__` 在 `%TEMP%\_MEIxxxx\`，于是
`os.path.dirname(os.path.dirname(__file__))` 算出来是 **`%TEMP%` 本身**；
旧代码（`main.py` / `store.py` / `widgets.py` / `win_shell.py`）会把这个路径
`sys.path.insert(0, ...)` 插到最前面，而 PyInstaller 的 `FrozenImporter` 排在标准
`PathFinder` **之后** → 只要 `%TEMP%` 根目录存在 `ui.pyc` / `toast.pyc` / `core.pyc`
等同名文件，运行时就会加载它们，exe 里那份被跳过了。

它的迷惑性在于：**版本号和构建时间都是对的**（那两项由入口脚本 `main` 提供，而
`main` 是直接从 exe 里执行的，永远是新的），被顶掉的只是被 import 的那批模块。
实测证据（本机）：`main` 报 1.0.1，窗口标题却是旧的；`ui` / `toast` / `widgets` /
`win_core` / `win_shell` / `png_util` 六个模块的 `__loader__` 是
`SourcelessFileLoader@%TEMP%\ui.pyc`，而先于污染被导入的 `core` / `store` 却是正常的
`PyiFrozenLoader` —— 靠这个"部分被顶替"的特征可以一眼认出。

| 排查动作 | 命令 / 位置 |
|---|---|
| 看本次运行到底加载了哪份模块 | `%APPDATA%\PromptEnhancer\app.log` 里的 `[start] 导入诊断：…` 行（列出 `sys.path` 和每个模块的 loader@路径） |
| 有没有被顶掉 | 同一份日志里的 `[start] ⚠️ 有模块不是从 exe 内部加载：…`；正常则显示 `[start] 模块来源全部正常（均为 exe 内部）` |
| 清掉嫌疑残留 | `python tools/dev/temp_pyc_guard.py --list` 看，`--move` 挪走（`.pyc` 和 zip 解压缓存一起清，只挪不删） |
| 直接跑到界面上看 | `python tools/dev/exe_probe.py <exe>`：抓窗口标题并回显上面那几行日志；发现模块被顶替会以非 0 退出 |

代码侧的永久修复（1.0.2 起）：`main.py` 在冻结尾只做 sys.path 的"减法"（把
`%TEMP%`、`dirname(_MEIPASS)` 清出去，`_MEIPASS` 保留），import 完成后再清一遍；
`store.py` / `widgets.py` / `win_shell.py` 里那些"源码运行才需要"的 `sys.path`
插入一律用 `if not getattr(sys, "frozen", False)` 包起来。
另有一条经验：**"从源码跑"和"跑 exe"要用对解释器** —— 打包用的是带 tkinter 的系统
Python；用没有 tkinter 的解释器跑 `e2e_test.py --exe …`，测试内部那个"用源码投递信号"
的子进程会静默死掉，表现为"浮层不出现 / 没有增强记录"，看着像产品坏了，其实是环境问题。
