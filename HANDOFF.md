# oi桌宠 v0.8.2 · 项目交接文档（HANDOFF）

> 给新接手的智能体/开发者的第一份必读材料。先读本文 + `自定义模块开发指南.md`，再动手。

## 1. 项目是什么

一个 Windows 桌面宠物（PyQt5），核心能力：

- 桌面悬浮桌宠：呼吸动画、拖拽、屏幕边缘吸附/重排、右键菜单
- 径向快捷菜单：最多 8 个按钮，支持网页链接 / 文件 / 程序 / 文件夹 / 命令，
  拖入添加、拖拽交换、拖出删除、右键编辑（按类型弹不同编辑框）
- 气泡模块系统：嵌入气泡或自动弹出；支持 HTTP / 大模型 / 智能体 / 时钟 /
  脚本(Python/JS/Shell) / 文件 / 静态文本；自定义 Qt 组件插件（widgets/）
- Codex 桥接：`codex_bridge.py` 读本地会话日志，气泡里显示 Codex 状态
- 设置窗口：外观、径向菜单预览、气泡模块列表（可排序/启停/编辑/导出导入）

## 2. 当前工作区

当前权威交接目录：

```text
D:\Practice\AItest\Codex_projects\oi桌宠_handoff
```

这份 handoff 快照没有 `.git` 历史；跨智能体/跨机器继续开发时，应先复制成新的工作目录，
或尽快 `git init` 并提交当前干净基线。历史路径仅作来源说明：

| 目录 | 状态 |
|---|---|
| `D:\Practice\AItest\Codex_projects\oi桌宠_handoff` | **当前权威交接快照**，开发、测试、打包都以这里为准 |
| `C:\Users\well\Documents\oi桌宠`、`D:\Practice\AItest\Codex_projects\oi桌宠` | 旧运行/开发快照，只读参考，不要继续修改 |
| `D:\Practice\AItest\Deepseek_projects\oi桌宠` | 更早历史文档中的路径，可能已不代表当前最新状态 |

本文后续命令写相对路径；如果目录被复制到新位置，就在新位置执行。

工作区与运行目录已合并为同一目录，**改动即生效，无需跨盘同步**；每次改完直接
`cd D:\Practice\AItest\Deepseek_projects\oi桌宠 && .\启动桌宠.bat` 重启桌宠即可。
改到的文件至少包括：`main.py`、`pet_gravity.py`、`status_monitor.py`、`bubble_ui.py`、
`bubble_layout.py`、`h5_cards.py`、`codex_bridge.py`、`widgets/`、
`自定义模块开发指南.md`、`HANDOFF.md`。

## 3. 环境与依赖

- 运行（开发调试）Python：`C:\Users\well\AppData\Local\Python\bin\python.exe`（3.14，x64）
- 运行依赖：PyQt5（含 PyQtWebEngine / QtWinExtras）、PyYAML、Pillow、psutil、numpy
- **打包专用 Python 3.12 环境**：`C:\Users\well\AppData\Local\oi-packenv`
  （Python 3.12.10 venv，装 PyQt5 + PyQtWebEngine + numpy + psutil + PyYAML +
  Pillow + **PyInstaller 6.21.0**）
  - 为什么用 3.12 打包：Python 3.14 + PyInstaller 的 onefile 在「自动重启」场景
    反复出现 `_MEI`/`python314.dll` 兼容问题；3.12 + PyInstaller 6.21 稳定。
  - 一键打包：`build_v082.bat`（内部已指向该 venv）。
- 仅 Windows（状态模块用 Windows API）。
- 启动时 `main._check_dependencies()` 会检查 PyQt5/PyYAML/Pillow 并提示安装。

## 4. 关键文件与职责

| 文件 | 职责 |
|---|---|
| `main.py` | 入口、托盘、单实例锁、依赖检查、启动 `codex_bridge` |
| `pet_gravity.py` | **核心**：桌宠窗口、径向菜单、设置窗口、RuleDialog、插槽系统、默认图标、右键多格式添加/编辑 |
| `status_monitor.py` | 模块提供器：http/llm/agent/clock/script/file/static、`RuleProvider`、`approve()` |
| `module_core.py` | **模块契约层**：版本号、规则归一化、`ModuleSpec`/`ModuleView`、后台刷新调度和失败退避 |
| `bubble_ui.py` | 气泡窗口、聊天面板、模块行渲染、链接点击、`_widget_height` 高度自适应 |
| `bubble_layout.py` | **唯一启用的气泡布局**（`StatusBubbleLayout`，pet_gravity 无条件实例化）；基类 StatusBubble 的旧手绘呈现已退役（不可达，勿再为其加功能） |
| `h5_cards.py` | 测试区卡片渲染、`ResultView`、番茄卡片（QtWebEngine 可选） |
| `codex_bridge.py` | 本地 HTTP 桥（127.0.0.1:8765）：`/status`、`/status.json`、`/requests`、`/approve`；读 `~/.codex/sessions/**/rollout-*.jsonl` |
| `widgets/` | 组件插件：`__init__.py`(ModuleWidget/加载器)、`kit.py`(通用控件库)、`tomato/panel/todo.py` 示例 |
| `widgets/icons.py` | **共享高清图标库**：24 网格、4 倍分辨率绘制，供画布/拼豆等工具栏使用 |
| `assets/` | 默认图标 `oi.png`、动图 `yxm.webp`、`icon.ico` |
| `oi_pet_v020.spec` / `build_v082.bat` | PyInstaller 打包配置与当前构建脚本 |
| `_make_backup.py` | 一键备份为 `oi桌宠_backup_<时间戳>.zip` |

## 5. 每次改动必须走的工作流

1. 在工作区（= 运行目录）改代码
2. 编译检查：`python -m py_compile <改动文件>`（或全量 `Get-ChildItem -Recurse -Filter *.py | python -m py_compile`）
3. 跑测试：`python -u test_bubble.py`（39 项，必须全过）；组件缩放测试 `python -u test_components_scale.py`
   （组件测试会真实渲染拼豆网格和共享图标，避免 paintEvent 静默失败）
4. 重启桌宠：在当前工作区执行 `.\启动桌宠.bat`
5. 备份：`python _make_backup.py`
6. 需要发布 exe 时打包：`build_v082.bat`（用 Python 3.12 venv + PyInstaller 6.21），
   之后 `dist\oi桌宠.exe`、`dist\oi桌宠0.8.2.exe` 和 `dist\oi桌宠_Setup_v0.8.2.exe` 生成；
   dist 发布包要含 `widgets/`、`config.yaml`、`webchat_sites.json`（脚本已自动拷贝）
7. `git add -A && git commit -m "..."`（**不要提交** pet_settings.json / chat_history.json / 各 *_data.json 等用户数据，.gitignore 已排除）

## 6. 功能地图（当前已实现）

- 桌宠：呼吸动画、按下回弹、阴影、oi.png/yxm.webp 双默认图标循环、
  图片/GIF/WebP 拖入替换、冻结环境图标路径已修复（`_resolve_image_candidates`）
- 径向菜单：贴边分区重排、背景盘填充、高亮/外扩、按钮与背景动画、
  拖拽交换/删除、右键"编辑…（类型）"按 网页链接/命令/文件/文件夹/路径 弹不同编辑框、
  盘面空白右键添加菜单（网页链接/文件/程序/文件夹/命令/清空）、网址按钮地球图标、
  命令按钮用 `run://` 前缀 shell 执行
- 气泡：固定 210px 宽、深色主题、透明度滑块、钉住/收起、链接点击、
  对话面板（LLM 流式、代码块、复制、多会话、图片、**停止按钮**可打断回复）、
  模块增删排序导出导入；
  **模块行右键菜单**：立即刷新 / 禁用启用 / 上移下移 / 删除模块（内置模块
  删除自动记入 hidden_builtins 防止复活）
- 修复：点气泡✕关闭后无法再弹出——关闭即置 `_click_suppress`（需鼠标离开
  桌宠再悬停才会再弹），桌宠帧循环增加悬停弹出兜底（不再依赖 enterEvent）
- 模块系统：内置规则（CPU/内存/电池/网络/情绪/Codex 状态）与普通模块同权，
  可编辑删除；嵌入/自动弹出二选一；`source.ui` 挂自定义组件（widgets/）；
  **交互组件行统一带标题栏**（对话面板用自带标题栏，其余组件由布局框架加标题，
  短文本行保持"标题+滚动值"单行）；AI 改待办后经 `todo_reload` 桥命令刷新
  嵌入的待办组件（组件不再用旧列表覆盖 AI 新增）
- LLM 能力层（显示名"AI 助手"，llm 不只是聊天）：`source.tools` 工具调用——
  常用（get_time/open_url/remind）、待办（add_todo/delete_todo/list_todos）、
  **管理模块**（list_modules/add_module/remove_module/enable_module/move_module）、
  **管理桌宠**（pet_control hide/show/move、pet_setting 大小/透明度等、
  pet_info）、**管理径向按钮**（list_buttons/add_button/remove_button/
  move_button/edit_button）；注册表在 status_monitor.TOOL_DEFS；
  `mode: task` 单次任务 + `json: true` 结构化输出；长对话自动摘要省 token；
  Ollama 本地模型预设（免费离线）；**轻量调用层 `status_monitor.PetAPI`
  （`_pet_api`）**：AI 工具↔桌宠状态/UI 的唯一通道（load/save/mutate_settings
  带锁校验 + reload_rules/reload_buttons/reload_todo/apply_pet_setting/
  control_pet/notify 命令助手），工具不再直接碰 pet_gravity/_tool_bridge，
  以后把 AI 拆独立进程时只改 PetAPI 实现；管理类操作经该层主线程执行
  （改设置文件 + 热重载）；**模块模板库 status_monitor.MODULE_TEMPLATES**
  （clock/countdown/weather/static/counter/todo/script）+ list_templates/
  add_module_from_template 工具，AI 建模块优先套模板；AI 助手面板带标题栏显示模型名
- 网页版聊天（无需 API Key）：已**拆到独立子进程**——主进程的
  `widgets/webchat.py` 只是薄客户端，通过 127.0.0.1:<port> 控制
  `webchat_server.py`（独立 QWebEngineView + Chromium），共享逻辑在
  `webchat_engine.py`；**主进程不再加载 Chromium**（webchat 崩/卡只影响
  子进程）。两种模式不变——入口按钮开独立聊天窗，或 `chat: true` 在气泡
  对话面板聊；`source.url` 配置站点（预设：DeepSeek/豆包），登录态按站点
  持久化到 `%APPDATA%\oi_pet\webchat_profile\<站点>`
- Codex 接入：状态显示已可用；桌面端审批无公开本地接口（详见"已知限制"）

## 7. 最近改动历史（重要，交代来龙去脉）

- 【模块架构 v0.8 2026-08-29】新增 `module_core.py`：统一版本号、规则防御归一化、
  稳定 ID、`ModuleSpec`/`ModuleView` 和动作/文本/组件/对话视图契约。抽出
  `ModuleScheduler` 管理首次加载、间隔、并发去重、失败计数和指数退避
  （5/10/20/40…最高 300 秒）；修复规则成功刷新后旧错误不清除的问题。
  聚合AI迁移为通用动作行契约（`ui=webchat` 或 `action=webchat`），旧配置兼容。
  设置窗口新增版本号显示，托盘和 exe 元数据升级到 v0.8.0。气泡回归现为 36 项。
- 【启动热修 2026-08-29】修复 `_refresh_impl` 中普通规则无交互组件时
  `_t` 未定义导致的启动崩溃；恢复普通模块标题/内容 fallback，并补充启动期
  回归用例。恢复 `ui=webchat` 的“打开”按钮动作行；正常模块不再常驻绿色
  健康点，圆点仅用于加载/过期/错误/暂停/空闲提示。气泡回归现为 28 项。
- 【气泡优化 2026-08-28】新增 `kit.BUBBLE_TOKENS`，气泡宽度/行高/标题列/手柄/
  卡片圆角/动作按钮统一读设计令牌；`kit.action_qss` 统一气泡动作按钮。
  模块行新增健康状态：正常 / 加载中 / 数据过期 / 出错 / 已暂停 / 空闲，
  标题前显示状态点，悬停显示错误和下次刷新；模块右键新增“诊断信息”，
  可查看上次更新、刷新间隔、下次刷新和最近错误。规则错误不再只有一次性弹窗。
  v0.7 新增聚合 AI/设置窗口图标；气泡与桌宠缩放继续支持热更新。
  气泡回归已扩到 25 项，`test_components_scale.py` 覆盖 9 个内置组件。

- 【迁移 2026-08-14】项目整体搬迁到 `D:\Practice\AItest\Deepseek_projects\oi桌宠`
  （工作区与运行目录合一，含 git 历史/全部备份）；旧目录只读保留
- LLM 预设精简（去掉 Groq/OpenRouter），加入小米 MiMo
  （`https://api.xiaomimimo.com/v1`，模型 `mimo-v2-flash`）
- 新增 Codex 状态模块 + `codex_bridge.py`（读会话日志，单行滚动显示）
- 自定义 UI 组件系统：`widgets/kit.py` 工具包、`panel.py` 示例、
  组件行高度自适应 `_widget_height`（FIX_H → heightForWidth → sizeHint）
- 模块编辑器去掉"组件下拉"（改为 JSON 直接写 `source.ui`）
- 修复打包后默认图标不切换：冻结环境相对路径解析问题
  （`_resolve_image_candidates` 按 `__file__`/_MEIPASS 解析）
- 径向菜单多格式添加 + 按钮右键"编辑…"（类型自适应）+ 桌面按钮改名/换图标
- 修复"径向菜单置顶盖住桌宠导致右键串菜单"：右键先判桌宠圆形区域，弹桌宠本体菜单
- 【2026-08 后续】气泡/桌宠**两档独立缩放**：设置"桌宠外观"里"气泡大小/桌宠大小"
  两个滑块（四档 1/1.25/1.5/2），存 `bubble_scale`/`pet_scale`；气泡内 `kit.bs()`、
  桌宠 `kit.ps()`；`QT_SCALE_FACTOR` 固定 1.5；模块标题/按钮/文字统一按档位缩放
- 【缩放稳定性 2026-08-28】`FIX_H` 统一定义为标准档逻辑高度，框架按 `current_height`
  或 `kit.bs(FIX_H)` 换算；`kit.row/col`、基础组件 QSS、字体和气泡卡片度量统一缩放。
  设置里的气泡/桌宠档位变更后由 `GravityPet._apply_pet_settings` 热更新：气泡档位
  变化时安全重建 `StatusBubbleLayout`，桌宠档位变化时重算本体并重建菜单几何，
  **不再要求手动重启**。
- 内置模块规则：内置与自定义不能同名（编辑窗校验 + 加载期去重）；编辑内置模块保留
  内置标记不迁移；内置可删除（hidden_builtins）；QQQ 默认每 30 秒弹出；打包默认开启
  天气/内存/电池/聚合AI/无限画布/拼豆
- AI 绘画工具（canvas_draw/perler_draw）改为**同步生成图元/像素再下发主线程执行**，
  回报真实结果；`_llm_source_cfg` 跳过占位 API Key 优先取启用规则的真实 Key
- 打包 v0.6：改用 **Python 3.12 venv + PyInstaller 6.21 + PyQtWebEngine**；
  当时大小调整改为仅提示手动重启，右键不再有"重启桌宠"；
  退出前清理 QtWebEngineProcess 子进程（避免 onefile 退出报 Failed to remove temp）
- 修复径向菜单打不开（补模块级 `_kit` 导入）；修复展开菜单时气泡瞬跳/一帧切过去
  （气泡避让用稳定半径 `_sector_outer_full`，`_smooth_move_to` 目标变化时从当前位置重起动画）

## 8. 已知限制 / 坑位

- **桌面端 Codex 审批无法外部注入**：桌面 app-server 走进程内 RPC，无公开本地接口；
  官方远程审批入口是 ChatGPT 手机 App；CLI 版可用 `codex app-server`
  （JSON-RPC，`thread/start` + `execCommandApproval` 回 `decision`），
  但完整 CLI 客户端尚未实现，桥接 `/requests` `/approve` 已预留
- 打包（单文件）后资源在 `_MEIPASS` 临时目录：默认图标/资源必须按 `__file__`
  解析，不能按 CWD（exe 所在目录）解析；`_resolve_image_candidates` 已处理
- webchat 已拆进程：主进程不 import QtWebEngine（`webchat_server.py` 子进程
  自己初始化）；`h5_cards` 的 webengine 也已改惰性导入。开发模式由
  `widgets/webchat.py` 用 `sys.executable webchat_server.py --port ...` 拉起
  子进程；**打包 exe 需把 webchat_server.py + webchat_engine.py 一起发布**，
  且子进程需能找到 python（或用 exe 的 --webchat-server 模式），重打包后必须
  实测聊天窗能打开网页
- 单实例锁：测试 exe 前必须先停 `pythonw main.py`，否则 exe 静默退出
- widgets 组件按会话缓存：改组件文件必须重启桌宠，无热重载
- WebP 动图依赖 Pillow（`PIL._webp`）与 Qt `qwebp.dll`，打包时都已包含
- 气泡布局已统一为 StatusBubbleLayout（`bubble_layout.py`），无开关；基类
  StatusBubble 把"模型"与"旧手绘呈现状态"（`_rows/_row_y/_link_hits`）耦合的
  历史遗留已标记退役（不可达）。**新功能只按 StatusBubbleLayout 实现**，
  不要再维护旧手绘路径
- 设置窗口、桌宠均有单实例限制；气泡动画历史上有过重影/跳动问题，
  改动布局动画需谨慎并用回归测试验证
- 日志：`%TEMP%\oi_pet_error.log`、`%TEMP%\oi_pet_diag.log`
- 配置：`pet_settings.json`（含 API Key，**不要提交进 git**）
- **打包环境坑（重要）**：打包必须用 `C:\Users\well\AppData\Local\oi-packenv`
  （Python 3.12 venv，PyInstaller 6.21.0 + PyQtWebEngine）；不要用系统 Python 3.14
  打包（onefile 重启/`_MEI`/`python314.dll` 一堆兼容问题）。大小调整现已热更新。

## 9. 迁移到其他智能体（交接步骤）

新智能体没有本对话的记忆，但能看到全部文件。把"记忆"转成下面三样：

1. **本文档**（架构 + 工作流 + 历史 + 坑位）—— 第一必读
2. **git 提交**：当前状态已提交，历史就是改动记录；继续迭代时按
   `git add -A && git commit -m "..."` 逐步提交
3. **备份 zip**：`oi桌宠_backup_<时间戳>.zip` 是每个版本快照，可回滚对照

给新智能体的开场提示（直接粘贴）：

```text
请先完整阅读 D:\Practice\AItest\Deepseek_projects\oi桌宠\HANDOFF.md 和
D:\Practice\AItest\Deepseek_projects\oi桌宠\自定义模块开发指南.md，了解项目架构、
工作流与已知限制后再继续。工作区与运行目录合一，都在
D:\Practice\AItest\Deepseek_projects\oi桌宠。
- 运行/调试用 Python 3.14：C:\Users\well\AppData\Local\Python\bin\python.exe
- 打包用 Python 3.12 venv：C:\Users\well\AppData\Local\oi-packenv（见 build_v080.bat）
- 设置中的大小档位已热更新；改 Python 代码后仍需重启开发进程。
每次改动必须：编译检查 → 跑 test_bubble.py → 重启桌宠（本目录 启动桌宠.bat）→
备份 → 必要时用 build_v080.bat 重打包。不要提交 pet_settings.json 等敏感文件。
```

迁移到别的机器时，拷贝：整个工作区（含 `widgets/`、`assets/`、测试、spec、
`启动桌宠.bat`、`build_v080.bat`、`.git`）。用户数据（pet_settings.json / *_data.json /
chat_*.json）可不带，新机器会自动生成默认值。新机器需装：Python 3.14（运行）+
PyQt5/PyQtWebEngine/PyYAML/Pillow/psutil/numpy，以及 Python 3.12 + 上述依赖 +
PyInstaller 6.21（打包）。

## 10. 常用命令速查

```powershell
python -u test_bubble.py                          # 气泡/模块契约回归测试（36 项）
python -u test_components_scale.py                 # 组件缩放回归测试
python _make_backup.py                            # 备份
build_v080.bat                                    # 打包（Python 3.12 venv + PyInstaller 6.21）
cd D:\Practice\AItest\Deepseek_projects\oi桌宠; .\启动桌宠.bat   # 启动桌宠
git add -A; git commit -m "..."                   # 提交（已自动忽略用户数据）
```
