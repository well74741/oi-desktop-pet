# -*- coding: utf-8 -*-
"""自定义组件插件目录。

用法：
1. 在本目录（widgets/）下新建 <组件名>.py，定义一个继承 ModuleWidget 的类；
   类名用 Widget 即可（框架自动识别）。
2. 规则配置里 source.ui 填组件名（如 "tomato"），气泡与编辑器的测试区就会
   在主线程渲染该组件。

组件运行在主线程，可以自由使用 Qt 控件；后台脚本线程只负责提供 value。
子类需实现 render(state, value)：
  - state：该组件专属的持久字典（主线程使用，跨刷新保留）
  - value：后台脚本/规则取到的当前值
框架创建组件后会先调用 set_rule(rule) 再 render(state, value)。

常用控件与布局：widgets/kit.py 提供与气泡风格统一的按钮/开关/进度条/
分隔线/标签/滚动容器等，直接 from widgets import kit 使用。
组件行高度：框架按 heightForWidth → FIX_H → sizeHint 自适应；
内容会变高的组件不要给根控件 setFixedHeight。

安全与稳定性：
- 组件必须在主线程创建（Qt 控件不允许在后台线程创建）。
- 组件代码异常只影响该模块，不会影响桌宠。
- 修改组件文件后需要重启桌宠生效（安全起见不做热重载）。
"""
import importlib.util
import os

from PyQt5.QtWidgets import QWidget


class ModuleWidget(QWidget):
    """自定义组件基类：子类实现 render(state, value)。"""

    FIX_H = 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rule = {}     # 规则配置（set_rule 注入）
        self.state = {}    # 组件专属持久状态（主线程使用）

    def set_rule(self, rule):
        """框架调用：把规则配置交给组件（可读取 interval/fallback 等字段）。"""
        self.rule = rule or {}

    def current_height(self):
        """默认把 FIX_H 当作标准档逻辑高度；可折叠组件应覆盖此方法。"""
        if int(getattr(self, "FIX_H", 0) or 0) > 0:
            from widgets import kit
            return kit.bs(int(self.FIX_H))
        return 0

    def render(self, state, value):
        """子类实现：根据状态与当前值刷新组件显示。"""
        pass


# 组件通用基础样式：与气泡/卡片风格统一（滚动条等），加载时自动并入每个组件
BASE_MODULE_QSS = (
    "QScrollBar:vertical{background:transparent;width:6px;margin:0;border:none;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,60);border-radius:3px;"
    "min-height:18px;margin:1px;}"
    "QScrollBar::handle:vertical:hover{background:rgba(255,255,255,110);}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
    "QScrollBar:horizontal{background:transparent;height:6px;margin:0;border:none;}"
    "QScrollBar::handle:horizontal{background:rgba(255,255,255,60);border-radius:3px;"
    "min-width:18px;margin:1px;}"
    "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
    "QListWidget{background:transparent;border:none;}"
)


_WIDGET_CACHE = {}   # 组件名 -> 类（每个会话加载一次）


def _widget_dir():
    """组件目录：打包后指向 exe 旁边的 widgets/，方便用户增删组件。"""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "widgets")
    return os.path.dirname(os.path.abspath(__file__))


def _find_widget_class(mod):
    cls = getattr(mod, "Widget", None)
    if cls is None:
        cls = getattr(mod, "ModuleWidget", None)
    if cls is None:
        for v in vars(mod).values():
            if (isinstance(v, type) and issubclass(v, ModuleWidget)
                    and v is not ModuleWidget):
                cls = v
                break
    return cls


_WIDGET_FAIL = {}    # 组件名 -> 失败原因（避免反复 exec 失败的文件）


def _log_fail(name, msg):
    """组件加载失败记一次日志（同一组件只记一次）。

    这类失败原先完全静默——只在气泡行上显示一个红点，日志里什么都没有，
    v0.9.2 的"画布/拼豆加载失败"就因此查不出原因（真相是打包漏了
    widgets/icons.py）。
    """
    try:
        import datetime
        import os as _os
        import tempfile
        path = _os.path.join(tempfile.gettempdir(), "oi_pet_error.log")
        with open(path, "a", encoding="utf-8") as f:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write("[%s] [widget] %s%s" % (stamp, msg, os.linesep))
    except Exception:
        pass


def clear_widget_cache():
    """清空组件缓存（含失败记录）。"""
    _WIDGET_CACHE.clear()
    _WIDGET_FAIL.clear()


def load_module_widget(name, parent=None):
    """按 ui 名加载自定义组件，返回 (widget, error)；失败返回 (None, 错误信息)。"""
    name = str(name or "").strip()
    if not name:
        return None, ""
    # 失败也要记住：气泡每次重排都会重试加载，而 exec 一个上千行的组件文件很贵，
    # 一直失败就会表现为"点桌宠卡住"。同一个错误在缓存期内直接返回。
    failed = _WIDGET_FAIL.get(name)
    if failed is not None:
        return None, failed
    try:
        cls = _WIDGET_CACHE.get(name)
        if cls is None:
            path = os.path.join(_widget_dir(), name + ".py")
            if not os.path.exists(path):
                msg = "未找到组件 %s（widgets/%s.py）" % (name, name)
                _WIDGET_FAIL[name] = msg
                _log_fail(name, msg)
                return None, msg
            spec = importlib.util.spec_from_file_location("widgets_" + name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = _find_widget_class(mod)
            if cls is None:
                msg = "组件 %s 未定义 Widget 类" % name
                _WIDGET_FAIL[name] = msg
                _log_fail(name, msg)
                return None, msg
            _WIDGET_CACHE[name] = cls
        w = cls(parent=parent) if parent is not None else cls()
        # 组件内部尺寸已按 kit.bs() 放大；放进设置窗测试区时不能再被统一放大一次
        w.setProperty("oi_nozoom", True)
        # 公共基础样式按当前档位缩放；组件自身样式已在构造时按规范缩放。
        try:
            own = w.styleSheet() or ""
            from widgets import kit
            w.setStyleSheet(kit.scale_qss(BASE_MODULE_QSS) + "\n" + own)
        except Exception:
            pass
        return w, ""
    except Exception as e:
        import traceback
        msg = "组件 %s 加载失败：%s" % (name, e)
        _WIDGET_FAIL[name] = msg
        _log_fail(name, msg + os.linesep + traceback.format_exc())
        return None, msg
