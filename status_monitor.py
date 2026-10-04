# -*- coding: utf-8 -*-
"""系统状态监控模块（可扩展，无第三方依赖，纯 Windows API）。

扩展方式——新增一个状态项只需两步：

    from status_monitor import StatusProvider, register_provider

    @register_provider
    class WeatherProvider(StatusProvider):
        name = "weather"            # 唯一标识
        title = "天气"              # 气泡中显示的名称
        interval = 60.0             # 刷新间隔（秒）
        enabled = True              # 可设为 False 临时停用

        def collect(self):
            return "晴 26°C"        # 返回要显示的文本

注册后的提供者会自动出现在悬停气泡里，按注册顺序显示。
"""
import ctypes
import ctypes.wintypes as wt
import html as _html
import tempfile
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.request
import urllib.error
import weakref

import data_store

from PyQt5.QtCore import (Qt, QTimer, QPoint, QPointF, QRect, QRectF, QSize,
                          QObject, QEvent, pyqtSignal)
from PyQt5.QtGui import (QFont, QFontMetrics, QPainter, QPainterPath, QColor,
                         QPen, QPixmap, QTextOption)
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                             QTextEdit, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QTextBrowser, QMenu)


# ==================== LLM 工具（function calling）注册表 ====================


def _tool_log(name, args, result):
    """工具执行审计：写 %TEMP%\\oi_pet_tools.log，便于排查"AI 说做了但没生效"。"""
    try:
        with open(os.path.join(os.environ.get("TEMP", "."),
                               "oi_pet_tools.log"),
                  "a", encoding="utf-8") as f:
            f.write("[%s] %s %s -> %s\n"
                    % (time.strftime("%H:%M:%S"), name,
                       json.dumps(args, ensure_ascii=False)[:160],
                       str(result)[:200]))
    except Exception:
        pass


# ==================== token 用量记录 ====================
# token_usage.json 结构：
# {"records": [{"t": "2026-08-17 14:00", "module": "AI助手",
#               "prompt": 123, "completion": 45}, ...]}

_TOKEN_FILE = "token_usage.json"
_TOKEN_LOCK = threading.RLock()
_TOKEN_RETENTION_DAYS = 30

# 网络响应字节上限：一次性 read() 的硬顶，防止指向超大响应的端点耗尽内存(OOM)。
# 超过上限只读到该长度即截断（后续再按 max_chars 二次截断）。
_MAX_RESP_BYTES = 8 * 1024 * 1024   # 8 MB


def _record_token_usage(module, prompt, completion):
    """记录一次大模型调用的真实 token 消耗（线程安全，保留 30 天）。"""
    try:
        with _TOKEN_LOCK:
            d = data_store.read_dict(_TOKEN_FILE) or {}
            records = d.get("records") or []
            records.append({
                "t": time.strftime("%Y-%m-%d %H:00"),
                "module": str(module)[:40],
                "prompt": int(prompt),
                "completion": int(completion),
            })
            # 保留 30 天：删除早于 cutoff 的记录
            cutoff = time.time() - _TOKEN_RETENTION_DAYS * 86400
            cutoff_str = time.strftime("%Y-%m-%d", time.localtime(cutoff))
            records = [r for r in records if r.get("t", "")[:10] >= cutoff_str]
            d["records"] = records
            data_store.write_json(_TOKEN_FILE, d)
    except Exception:
        pass


def _load_token_usage():
    """读取全部 token 记录（带锁）。"""
    try:
        with _TOKEN_LOCK:
            d = data_store.read_dict(_TOKEN_FILE) or {}
            return d.get("records") or []
    except Exception:
        return []


def _today_token_summary(records):
    """按今日 24 小时汇总：返回 [24 个每小时 total]，供柱状图/模块显示。"""
    today = time.strftime("%Y-%m-%d")
    hourly = [0] * 24
    for r in records:
        t = str(r.get("t", ""))
        if t[:10] != today:
            continue
        try:
            h = int(t[11:13]) if len(t) > 13 else 0
            if 0 <= h < 24:
                hourly[h] += int(r.get("prompt", 0) or 0) \
                    + int(r.get("completion", 0) or 0)
        except Exception:
            continue
    return hourly


def _module_token_summary(records):
    """按模块汇总消耗（返回 [(模块名, total)]，降序）。"""
    agg = {}
    for r in records:
        m = str(r.get("module", "未知模块"))
        agg[m] = agg.get(m, 0) + int(r.get("prompt", 0) or 0) \
            + int(r.get("completion", 0) or 0)
    return sorted(agg.items(), key=lambda x: -x[1])


# ==================== 使用行为采集（通用统计表数据源） ====================
# usage_data.json 结构：
# {"events": [{"day": "2026-08-17", "kind": "chat"|"tool", "module": "AI助手", "n": 1}, ...]}

_USAGE_FILE = "usage_data.json"
_USAGE_LOCK = threading.RLock()


def _record_usage(kind, module="", n=1):
    """记录一次使用行为（聊天/工具调用），含时刻供按小时统计。"""
    try:
        with _USAGE_LOCK:
            d = data_store.read_dict(_USAGE_FILE) or {}
            events = d.get("events") or []
            events.append({
                "day": time.strftime("%Y-%m-%d"),
                "t": time.strftime("%Y-%m-%d %H:00"),
                "kind": str(kind),
                "module": str(module)[:40],
                "n": int(n),
            })
            d["events"] = events
            data_store.write_json(_USAGE_FILE, d)
    except Exception:
        pass


def _load_usage():
    try:
        with _USAGE_LOCK:
            d = data_store.read_dict(_USAGE_FILE) or {}
            return d.get("events") or []
    except Exception:
        return []


def _usage_daily_summary(events, kinds=None):
    """按天汇总：返回 [(日期, 次数)] 升序（近 N 天）。"""
    agg = {}
    for e in events:
        k = str(e.get("kind", ""))
        if kinds and k not in kinds:
            continue
        day = str(e.get("day", ""))
        agg[day] = agg.get(day, 0) + int(e.get("n", 1) or 1)
    return sorted(agg.items())


def _usage_today_hourly(events, kinds=None):
    """今日按小时汇总：返回 [24 个次数]。"""
    today = time.strftime("%Y-%m-%d")
    hourly = [0] * 24
    for e in events:
        k = str(e.get("kind", ""))
        if kinds and k not in kinds:
            continue
        if str(e.get("day", "")) != today:
            continue
        try:
            h = int(str(e.get("t", ""))[11:13]) if e.get("t") else \
                int(time.strftime("%H"))  # 无时刻时归到当前小时
            if 0 <= h < 24:
                hourly[h] += int(e.get("n", 1) or 1)
        except Exception:
            continue
    return hourly


def _tool_get_time(args):
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _tool_add_todo(args):
    text = str(args.get("text", "")).strip()
    if not text:
        return "参数 text 为空"
    try:
        data = data_store.read_list("todo_data.json")
        data.append(text)
        data_store.write_json("todo_data.json", data)
    except Exception as e:
        return "写入待办失败：%s" % e
    _pet_api.reload_todo()
    return "已添加到待办列表"


def _tool_list_todos(args):
    try:
        data = data_store.read_list("todo_data.json")
        return json.dumps(data, ensure_ascii=False)[:800] or "（空）"
    except Exception as e:
        return "读取待办失败：%s" % e


def _tool_open_url(args):
    import webbrowser
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "url 需以 http(s):// 开头"
    webbrowser.open(url)
    return "已用默认浏览器打开：%s" % url


class _ToolBridge(QObject):
    """跨线程命令桥：工具线程里触发，主线程槽里执行 UI 操作。
    - popup(str)：弹气泡提示（remind 用）
    - command(action, payload)：rules_reload / buttons_reload / pet_control
    """
    popup = pyqtSignal(str)
    command = pyqtSignal(str, object)


_tool_bridge = _ToolBridge()


class PetAPI:
    """轻量调用层：AI 工具 ↔ 桌宠状态/UI 之间的唯一通道。

    - 状态读写带锁 + 轻校验，工具不再各自拼 JSON / 重复 load-save；
    - UI 动作走统一桥命令，工具不碰 _tool_bridge / pet.settings 细节；
    - 以后要把 AI 拆成独立进程时，只需把本类的方法实现换成 IPC，工具代码不动。
    """

    def __init__(self):
        self._settings_lock = threading.Lock()

    # ---- 设置状态 ----
    def load_settings(self):
        from pet_gravity import load_settings
        st = load_settings()
        if not isinstance(st, dict):
            st = {}
        st.setdefault("status_rules", [])
        st.setdefault("slot_shortcuts", [])
        st.setdefault("hidden_builtins", [])
        return st

    def save_settings(self, st):
        from pet_gravity import save_settings
        save_settings(st)

    def mutate_settings(self, fn):
        """fn(settings) -> 变更描述字符串；返回 None 表示未变更（不保存）。
        持有 pet_gravity._SETTINGS_LOCK，使"读-改-写"与 UI/其他工具串行，
        避免并发丢更新；写入前确保关键字段类型正确。"""
        try:
            from pet_gravity import _SETTINGS_LOCK as _lock
        except Exception:
            _lock = self._settings_lock
        with _lock:
            st = self.load_settings()
            out = fn(st)
            if out is None:
                return None
            self.save_settings(st)
        return out

    # ---- UI 命令（桥）----
    def command(self, action, payload=None):
        _tool_bridge.command.emit(action, payload or {})

    def reload_rules(self):
        self.command("rules_reload")

    def reload_buttons(self):
        self.command("buttons_reload")

    def reload_todo(self):
        self.command("todo_reload")

    def apply_pet_setting(self, key, value):
        self.command("pet_setting", {"key": key, "value": value})

    def control_pet(self, action, x=None, y=None):
        self.command("pet_control", {"action": action, "x": x, "y": y})

    def notify(self, text):
        _tool_bridge.popup.emit(str(text))


_pet_api = PetAPI()


def _load_pet_settings():
    """兼容旧调用：读取 pet_settings.json。"""
    return _pet_api.load_settings()


def _save_pet_settings(settings):
    """兼容旧调用：保存 pet_settings.json。"""
    _pet_api.save_settings(settings)


# 提醒定时器注册表：可取消、有数量上限、周期提醒有最小间隔，防止 AI 无限堆
# Timer 线程（如 every=1）耗尽资源，也便于退出时统一清理。
_REMIND_LOCK = threading.RLock()
_REMIND_TIMERS = []            # 活跃 threading.Timer（可 cancel）
_REMIND_MAX = 20               # 活跃提醒数量上限
_REMIND_MIN_EVERY = 10         # 周期提醒最小间隔（秒）


def _remind_track(timer):
    """登记并启动一个定时器；先清理已结束的，超过上限则拒绝（不启动）。
    返回 True 表示已登记并启动，False 表示超限未启动。"""
    with _REMIND_LOCK:
        _REMIND_TIMERS[:] = [t for t in _REMIND_TIMERS if t.is_alive()]
        if len(_REMIND_TIMERS) >= _REMIND_MAX:
            return False
        timer.daemon = True
        _REMIND_TIMERS.append(timer)
        timer.start()
        return True


def cancel_all_reminders():
    """取消全部活跃提醒（退出/重置时调用）。"""
    with _REMIND_LOCK:
        for t in _REMIND_TIMERS:
            try:
                t.cancel()
            except Exception:
                pass
        _REMIND_TIMERS.clear()


def _tool_remind(args):
    msg = str(args.get("message", "提醒")).strip() or "提醒"
    secs = int(args.get("seconds", 0) or 0)                 # 一次性（0=默认60秒）
    every = int(args.get("every", 0) or 0)                  # 周期秒数（0=未设置）
    daily = str(args.get("daily", "") or "").strip()        # "HH:MM" 每天提醒

    def _fire():
        try:
            _pet_api.notify("⏰ 提醒：%s" % msg)
        except Exception:
            pass

    if daily:
        mm = re.match(r"^(\d{1,2}):(\d{2})$", daily)
        if mm and 0 <= int(mm.group(1)) <= 23 and 0 <= int(mm.group(2)) <= 59:
            if not _schedule_daily(msg, daily, _fire):
                return "提醒数量已达上限（%d 个），请先重启桌宠清空" % _REMIND_MAX
            return "已设置每日提醒：%s（每天 %s 弹出，重启桌宠后失效）" % (msg, daily)
    if every > 0:
        # 最小间隔护栏：防止 AI 设 every=1 每秒生成 Timer 线程耗尽资源
        every = max(_REMIND_MIN_EVERY, every)

        def _loop():
            _fire()
            _remind_track(threading.Timer(every, _loop))

        if not _remind_track(threading.Timer(every, _loop)):
            return "提醒数量已达上限（%d 个），请先重启桌宠清空" % _REMIND_MAX
        return "已设置周期提醒：%s（每 %d 秒弹出）" % (msg, every)
    delay = max(1, secs or 60)
    if not _remind_track(threading.Timer(delay, _fire)):
        return "提醒数量已达上限（%d 个），请先重启桌宠清空" % _REMIND_MAX
    return "已设置提醒：%s（%d 秒后弹出）" % (msg, delay)


def _schedule_daily(msg, hhmm, fire):
    """每天 HH:MM 弹提醒（进程内；递归安排下一天）。返回是否登记成功。"""
    from datetime import datetime, timedelta
    try:
        h, m = map(int, hhmm.split(":"))
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        def _tick():
            fire()
            _schedule_daily(msg, hhmm, fire)
        return _remind_track(
            threading.Timer((target - now).total_seconds(), _tick))
    except Exception:
        return False


def _tool_delete_todo(args):
    key = str(args.get("text", "") or "").strip()
    try:
        data = data_store.read_list("todo_data.json")
        if not key:
            return "请提供要删除的待办内容或序号"
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(data):
                removed = data.pop(idx)
                data_store.write_json("todo_data.json", data)
                _pet_api.reload_todo()
                return "已删除第 %d 项：%s" % (idx + 1, removed)
            return "序号 %s 超出范围（共 %d 项）" % (key, len(data))
        hit = next((t for t in data if key in str(t)), None)
        if hit is None:
            return "未找到包含「%s」的待办" % key
        data = [t for t in data if t != hit]
        data_store.write_json("todo_data.json", data)
        _pet_api.reload_todo()
        return "已删除待办：%s" % hit
    except Exception as e:
        return "删除待办失败：%s" % e


def _tool_list_modules(args):
    try:
        st = _pet_api.load_settings()
        rules = st.get("status_rules", []) or []
        lines = ["%s（%s）" % (r.get("name", "?"),
                              "启用" if r.get("enabled", True) else "禁用")
                 for r in rules]
        return "；".join(lines) if lines else "（暂无模块）"
    except Exception as e:
        return "读取模块失败：%s" % e


_KNOWN_SOURCE_TYPES = {"static", "http", "file", "clock", "llm", "agent",
                       "script", "pomodoro", "disk"}


def _normalize_rule(rule):
    """把模型生成的规则归一化成标准结构；不合法时返回错误消息。"""
    if not isinstance(rule, dict):
        return None, "rule 必须是 JSON 对象"
    src = rule.get("source")
    if isinstance(src, str):
        # 模型常把 source 写成 URL 字符串、type 放顶层：归一化
        t = str(rule.pop("type", "http") or "http").lower()
        if t in ("web", "website", "url"):
            t = "http"
        if t not in _KNOWN_SOURCE_TYPES:
            t = "http"
        rule["source"] = {"type": t, "url": src}
    src = rule.get("source")
    if not isinstance(src, dict):
        return None, "source 必须是对象（如 {\"type\":\"http\",\"url\":\"...\"}）"
    t = str(src.get("type", "") or "").lower()
    if t not in _KNOWN_SOURCE_TYPES:
        return None, "未知 source.type：%s（支持：%s）" % (
            t or "(空)", "、".join(sorted(_KNOWN_SOURCE_TYPES)))
    src["type"] = t
    if "id" not in rule:
        rule["id"] = "r%d" % int(time.time() * 1000)
    rule.setdefault("enabled", True)
    rule.setdefault("interval", 3600)
    rule.setdefault("fallback", "获取失败")
    return rule, ""


def _tool_list_components(args):
    """列出 widgets/ 目录下可用的自定义组件（source.ui 可填的值）。"""
    try:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widgets")
        names = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".py") and not fn.startswith("_") \
                        and fn != "kit.py":
                    names.append(fn[:-3])
        return "可用组件 ui：%s" % ("、".join(names) if names else "（无）")
    except Exception as e:
        return "读取组件失败：%s" % e


def _trial_collect(rule, timeout=6):
    """添加模块后立即试取数一次，返回反馈文案（供模型自我纠正）。"""
    try:
        rp = RuleProvider(rule)
        box = [None]

        def _run():
            try:
                box[0] = rp.collect()
            except Exception as e:
                box[0] = "ERR:" + str(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return "试取数超时，跳过验证"
        r = box[0]
        if r is None:
            return "试取数无结果"
        r = str(r)
        if r.startswith("ERR:"):
            return "试取数失败：%s" % r[4:200]
        if r in ("—", "获取失败", "未启动", ""):
            return "试取数返回「%s」（配置可能有误，请检查）" % r
        return "试取数成功：%s" % r[:40]
    except Exception as e:
        return "试取数异常：%s" % e


def _ai_exec_allowed():
    """用户是否允许 AI 创建会执行本地代码/读文件的模块（默认否）。"""
    try:
        return bool(_pet_api.load_settings().get("allow_ai_exec_modules", False))
    except Exception:
        return False


# 这些模板的脚本 code 由模板写死（非 AI 提供），即使 type=script 也安全，AI 可直接建
_SAFE_SCRIPT_TEMPLATES = {
    "counter", "todo", "stats",
    # 组件类模板（_widget_template 生成，code 固定为一个字符串字面量）
    "notes", "calc", "tomato", "health", "launcher", "tokenmeter",
    "canvas", "perler",
}


def _add_rule(rule, from_template=None):
    """把一条已归一化的规则写入设置、热重载并试取数，返回反馈文案。
    from_template 传模板名时，若为安全模板则放行其固定脚本。"""
    name = str((rule or {}).get("name", "") or "").strip()
    if not name:
        return "模块缺少 name"
    rule, err = _normalize_rule(rule)
    if err:
        return "添加模块失败：%s" % err
    # 安全门禁：script 模块可执行本地代码、file 模块可读任意本地文件。默认禁止 AI
    # 自动创建"带任意 code 的 script"或"file"模块，防止进入模型上下文的不可信文本
    # （抓到的网页/读到的文件/识图内容/agent 返回）通过 prompt injection 诱导 AI
    # 落地可执行模块 → 注入驱动的 RCE/文件外泄。安全模板（counter/todo/stats，code
    # 固定）放行；用户可在「设置 → AI 设置」勾选『允许 AI 创建可执行模块』整体放开。
    _stype = str((rule.get("source") or {}).get("type", "")).lower()
    _trusted = from_template in _SAFE_SCRIPT_TEMPLATES
    if _stype == "file" or (_stype == "script" and not _trusted):
        if not _ai_exec_allowed():
            return (                    "为安全起见，AI 当前不能创建会执行本地代码/读取本地文件的"
                    "「%s」模块。**请改用这些替代方案**：static（固定文本）、"
                    "http（接口取数）、clock（时间/倒计时）、llm（大模型），"
                    "或 add_module_from_template 里的模板。"
                    "如需放开，请在「设置 → AI 设置」勾选"
                    "『允许 AI 创建可执行模块（脚本/读文件）』后重试"
                    "（存在被诱导执行任意命令的风险，自担风险）。" % _stype)
    st = _pet_api.load_settings()
    rules = [r for r in (st.get("status_rules", []) or [])
             if str(r.get("name", "")) != name]
    rules.append(rule)
    st["status_rules"] = rules
    _pet_api.save_settings(st)
    _pet_api.reload_rules()
    trial = _trial_collect(rule)
    return "已添加模块：%s（type=%s）。%s" % (
        name, rule["source"].get("type"), trial)


# 闸门关着时附加在 add_module 说明末尾的一段话。提前说清楚，模型就不会
# 去试那些注定被拒的类型 —— 原来说明里没提这道闸，模型只能撞上去才知道。
_EXEC_GATE_HINT = (
    "【重要】当前设置不允许 AI 创建 script / file 类型模块（它们会执行本地"
    "代码或读取本地文件）。请改用 static / http / clock / llm，"
    "或直接用 add_module_from_template。"
)


def tool_description(name):
    """取工具说明；add_module 会随安全闸门开关动态变化。"""
    d = (TOOL_DEFS.get(name) or {}).get("description", "")
    if name == "add_module" and not _ai_exec_allowed():
        return d + _EXEC_GATE_HINT
    return d


def _tool_add_module(args):
    rule = args.get("rule")
    if not isinstance(rule, dict):
        return "rule 必须是 JSON 对象"
    try:
        return _add_rule(rule)
    except Exception as e:
        return "添加模块失败：%s" % e


# ==================== 模块模板库 ====================

MODULE_TEMPLATES = {
    "clock": {
        "desc": "当前时间（每秒刷新）",
        "params": [],
        "build": lambda name, p: {
            "name": name, "interval": 1, "enabled": True, "embed": True,
            "fallback": "—",
            "source": {"type": "clock", "mode": "time"}}},
    "countdown": {
        "desc": "倒计时到目标时刻（参数 target，如 18:00）",
        "params": ["target"],
        "build": lambda name, p: {
            "name": name, "interval": 1, "enabled": True, "embed": True,
            "fallback": "已到点",
            "source": {"type": "clock", "mode": "countdown",
                       "target": str(p.get("target", "18:00"))}}},
    "weather": {
        "desc": "天气（wttr.in 接口，嵌入气泡）",
        "params": [],
        "build": lambda name, p: {
            "name": name, "interval": 1800, "enabled": True, "embed": True,
            "fallback": "天气获取失败",
            "source": {"type": "http",
                       "url": "https://wttr.in/?format=%c+%t", "timeout": 5}}},
    "static": {
        "desc": "固定文本（参数 text）",
        "params": ["text"],
        "build": lambda name, p: {
            "name": name, "interval": 3600, "enabled": True, "embed": True,
            "fallback": "",
            "source": {"type": "static", "text": str(p.get("text", ""))}}},
    "counter": {
        "desc": "可点击计数器（＋1/清零，挂 counter 组件）",
        "params": [],
        "build": lambda name, p: {
            "name": name, "interval": 3600, "enabled": True, "embed": True,
            "fallback": "未启动",
            "source": {"type": "script", "lang": "python", "ui": "counter",
                       "code": "result = '计数器'", "timeout": 5}}},
    "todo": {
        "desc": "待办清单（挂 todo 组件）",
        "params": [],
        "build": lambda name, p: {
            "name": name, "interval": 3600, "enabled": True, "embed": True,
            "fallback": "未启动",
            "source": {"type": "script", "lang": "python", "ui": "todo",
                       "code": "result = '待办清单'", "timeout": 5}}},
    "script": {
        "desc": "自定义 Python 脚本（参数 code；state 字典可跨刷新记状态）",
        "params": ["code"],
        "build": lambda name, p: {
            "name": name, "interval": 60, "enabled": True, "embed": True,
            "fallback": "脚本出错",
            "source": {"type": "script", "lang": "python",
                       "code": str(p.get("code", "result = 'hello'")),
                       "timeout": 15}}},
    "stats": {
        "desc": "统计表组件（参数 metric=todos/modules/buttons/chat/tool/token；"
                "或用 http_url+http_path 接任意本地/网络 JSON 接口）",
        "params": ["metric", "http_url", "http_path", "unit"],
        "build": lambda name, p: {
            "name": name, "interval": 60, "enabled": True, "embed": True,
            "fallback": "",
            "source": {"type": "script", "lang": "python", "ui": "stats",
                       "code": "result = ''",
                       "stats": {
                           "metric": str(p.get("metric", "todos")),
                           "http_url": str(p.get("http_url", "")),
                           "http_path": str(p.get("http_path", "value")),
                           "unit": str(p.get("unit", "")),
                       }}}},
    "llm": {
        "desc": "大模型聊天（OpenAI 兼容接口；参数 base_url/model/api_key/system_prompt）",
        "params": ["base_url", "model", "api_key", "system_prompt"],
        "build": lambda name, p: {
            "name": name, "interval": 60, "enabled": True, "embed": True,
            "chat": True,
            "fallback": "AI 不可用",
            "greeting": "你好！我是大模型助手，想聊什么？",
            "source": {"type": "llm",
                       "base_url": str(p.get("base_url", "")),
                       "model": str(p.get("model", "")),
                       "api_key": str(p.get("api_key", "")),
                       "system_prompt": str(p.get("system_prompt",
                                                "你是桌宠助手，用简短中文回复")),
                       "user_prompt": "你好",
                       "temperature": 0.8,
                       "max_tokens": 8192}},
    },
}


def _widget_template(ui, desc, label, interval=3600):
    """生成一个"挂自定义组件"的模板项：code 由模板写死，属安全模板。"""
    return {
        "desc": desc,
        "params": [],
        "build": (lambda name, p, _ui=ui, _lb=label, _iv=interval: {
            "name": name, "interval": _iv, "enabled": True, "embed": True,
            "fallback": "未启动",
            "source": {"type": "script", "lang": "python", "ui": _ui,
                       "code": "result = %r" % _lb, "timeout": 5}}),
    }


# ---- 组件类模板（挂 widgets/ 下已有的交互组件；code 固定，安全） ----
MODULE_TEMPLATES.update({
    "notes": _widget_template("notes", "便签：在气泡里随手记几行字", "便签"),
    "calc": _widget_template("calc", "计算器：气泡内快速算数", "计算器"),
    "tomato": _widget_template("tomato", "番茄钟：专注计时 + 休息提醒", "番茄钟"),
    "health": _widget_template("health", "久坐提醒：定时提醒起身活动", "久坐提醒"),
    "launcher": _widget_template("launcher", "快捷启动：气泡里点按钮开程序/网页", "快捷启动"),
    "tokenmeter": _widget_template("tokenmeter", "Token 用量：统计大模型消耗", "Token用量", 300),
    "canvas": _widget_template("canvas", "无限画布：手绘/AI 作画", "无限画布"),
    "perler": _widget_template("perler", "拼豆：像素画格子，可让 AI 画", "拼豆"),
})

# ---- 数据源类模板（http/clock/script，常用场景开箱即用） ----
MODULE_TEMPLATES.update({
    "hitokoto": {
        "desc": "每日一言（随机句子，来自 hitokoto.cn）",
        "params": [],
        "build": lambda name, p: {
            "name": name, "interval": 1800, "enabled": True, "embed": True,
            "fallback": "—",
            "source": {"type": "http",
                       "url": "https://v1.hitokoto.cn/?encode=text",
                       "plain_text": True, "timeout": 5}}},
    "exchange": {
        "desc": "汇率（参数 base 基准币种、quote 目标币种，如 USD→CNY）",
        "params": ["base", "quote"],
        "build": lambda name, p: {
            "name": name, "interval": 3600, "enabled": True, "embed": True,
            "fallback": "汇率获取失败",
            "transform": "rates.%s" % str(p.get("quote", "CNY") or "CNY").upper(),
            "source": {"type": "http",
                       "url": "https://open.er-api.com/v6/latest/%s"
                              % str(p.get("base", "USD") or "USD").upper(),
                       "timeout": 8}}},
    "anniversary": {
        "desc": "纪念日/倒数日（参数 date=YYYY-MM-DD，显示已过或还剩多少天）",
        "params": ["date"],
        "build": lambda name, p: {
            "name": name, "interval": 3600, "enabled": True, "embed": True,
            "fallback": "日期无效",
            "source": {"type": "clock", "mode": "days",
                       "date": str(p.get("date", "2026-01-01"))}}},
    "disk": {
        "desc": "磁盘剩余空间（参数 drive，如 C:）",
        "params": ["drive"],
        "build": lambda name, p: {
            "name": name, "interval": 600, "enabled": True, "embed": True,
            "fallback": "读取失败",
            "source": {"type": "disk",
                       "drive": str(p.get("drive", "C:") or "C:")}}},
    "json_api": {
        "desc": "任意 JSON 接口取字段（参数 url、path 如 data.items.0.title）",
        "params": ["url", "path"],
        "build": lambda name, p: {
            "name": name, "interval": 300, "enabled": True, "embed": True,
            "fallback": "获取失败",
            "transform": str(p.get("path", "") or ""),
            "source": {"type": "http", "url": str(p.get("url", "")),
                       "timeout": 8}}},
})


def _tool_list_templates(args):
    """列出模块模板库：模板名 + 说明 + 所需参数。"""
    lines = []
    for k, t in MODULE_TEMPLATES.items():
        ps = "、".join(t["params"]) if t["params"] else "无"
        lines.append("%s：%s（参数：%s）" % (k, t["desc"], ps))
    return "；".join(lines)


def _tool_add_module_from_template(args):
    """按模板创建模块：template=模板名，name=模块名，params={参数}。"""
    tpl = str(args.get("template", "") or "").strip().lower()
    name = str(args.get("name", "") or "").strip()
    params = args.get("params") or {}
    if tpl not in MODULE_TEMPLATES:
        return "未知模板：%s。可先 list_templates 查看。" % tpl
    if not name:
        return "模块缺少 name"
    if not isinstance(params, dict):
        return "params 必须是对象"
    try:
        rule = MODULE_TEMPLATES[tpl]["build"](name, params)
        return _add_rule(rule, from_template=tpl)
    except Exception as e:
        return "按模板添加失败：%s" % e


def _name_match(a, b):
    """模块名匹配：忽略大小写，精确优先，其次才是包含。

    **"包含"必须唯一才算命中。** 原来的规则是"互相包含即算命中"，于是
    `remove_module(name="AI")` 会把「聚合AI」「AI助手」「AI本地·Ollama」一起删掉
    —— 实测过，而且内置模块被删会记进 hidden_builtins、不会自动恢复。
    删除 / 启停 / 排序模块和按钮的 6 个工具都走这里，所以这条得保守。

    返回 True 表示"就它了"；有多个候选时返回 False，让调用方给出候选列表
    让用户确认，而不是赌一个。
    """
    a = str(a or "").strip().lower()
    b = str(b or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _not_found(kind, name, cands, list_tool):
    """说清是"没找到"还是"匹配到多个"，并把候选列出来。

    故意不猜：多个候选时请拿完整名字再来一次，总好过赌一个、把不相干的
    模块删掉（内置模块被删还会记进 hidden_builtins，不会自动恢复）。
    """
    if cands:
        return ("有 %d 个%s匹配「%s」：%s。请用完整名称重试。"
                % (len(cands), kind, name, "、".join(cands[:8])))
    return "未找到%s：%s（可先用 %s 看准确名称）" % (kind, name, list_tool)


def _match_one(items, query, key="name"):
    """在一组 item 里挑**唯一**匹配项。

    返回 (命中的 item 或 None, 候选名列表)。
    - 精确命中：只有一个（或取第一个同名）就直接给；
    - 模糊命中：**唯一**才给；多个候选返回候选列表，由调用方拒绝并提示。
    """
    key = key or "name"
    q = str(query or "").strip().lower()
    if not q:
        return None, []
    exact = [it for it in items if str(it.get(key, "")).strip().lower() == q]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return exact[0], []
    cands = [it for it in items if _name_match(it.get(key, ""), query)]
    if len(cands) == 1:
        return cands[0], []
    return None, [str(it.get(key, "")) for it in cands]


def _tool_remove_module(args):
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "请提供模块名称"
    try:
        st = _pet_api.load_settings()
        rules = list(st.get("status_rules", []) or [])
        _hit, _cands = _match_one(rules, name)
        if _hit is None:
            return _not_found("模块", name, _cands, "list_modules")
        keep = [r for r in rules if r is not _hit]
        removed_ids = [_hit.get("id")]
        st["status_rules"] = keep
        # 内置模块删除后必须记入 hidden_builtins，否则 load_settings 会重新合并回来
        hidden = list(set(st.get("hidden_builtins") or []))
        hidden.extend(i for i in removed_ids if i)
        st["hidden_builtins"] = hidden
        _pet_api.save_settings(st)
        _pet_api.reload_rules()
        return "已删除模块：%s（匹配 %d 个）" % (name, len(removed_ids))
    except Exception as e:
        return "删除模块失败：%s" % e


def _tool_enable_module(args):
    name = str(args.get("name", "") or "").strip()
    enabled = bool(args.get("enabled", True))
    try:
        st = _pet_api.load_settings()
        rules = list(st.get("status_rules", []) or [])
        _hit, _cands = _match_one(rules, name)
        if _hit is None:
            return _not_found("模块", name, _cands, "list_modules")
        _hit["enabled"] = enabled
        st["status_rules"] = rules
        _pet_api.save_settings(st)
        _pet_api.reload_rules()
        return "已%s模块：%s" % ("启用" if enabled else "禁用", name)
    except Exception as e:
        return "操作失败：%s" % e


def _tool_move_module(args):
    """调整模块顺序：direction=up/down 或 to=目标序号（0 起）。"""
    name = str(args.get("name", "") or "").strip()
    direction = str(args.get("direction", "") or "").lower()
    to = args.get("to")
    try:
        st = _pet_api.load_settings()
        rules = list(st.get("status_rules", []) or [])
        _hit, _cands = _match_one(rules, name)
        if _hit is None:
            return _not_found("模块", name, _cands, "list_modules")
        idx = rules.index(_hit)
        if isinstance(to, int) and 0 <= to < len(rules):
            r = rules.pop(idx)
            rules.insert(to, r)
        elif direction in ("up", "down"):
            delta = -1 if direction == "up" else 1
            j = idx + delta
            if not (0 <= j < len(rules)):
                return "已经在最%s了" % ("上面" if delta < 0 else "下面")
            rules[idx], rules[j] = rules[j], rules[idx]
        else:
            return "请提供 direction（up/down）或 to（目标序号）"
        st["status_rules"] = rules
        _pet_api.save_settings(st)
        _pet_api.reload_rules()
        return "已调整模块顺序：%s" % name
    except Exception as e:
        return "移动模块失败：%s" % e


def _tool_list_buttons(args):
    try:
        st = _pet_api.load_settings()
        scs = st.get("slot_shortcuts", []) or []
        lines = ["%s（%s）" % (s.get("name", "?"), s.get("path", ""))
                 for s in scs]
        return "；".join(lines) if lines else "（暂无按钮）"
    except Exception as e:
        return "读取按钮失败：%s" % e


def _tool_add_button(args):
    name = str(args.get("name", "") or "").strip()
    kind = str(args.get("kind", "url") or "url").lower()
    target = str(args.get("target", "") or "").strip()
    if not name or not target:
        return "name 和 target 不能为空"
    if kind in ("cmd", "command"):
        path = "run://" + target
    elif kind in ("file", "folder", "program", "path"):
        path = target
    else:
        if not target.startswith(("http://", "https://")):
            target = "https://" + target
        path = target
    try:
        st = _pet_api.load_settings()
        scs = list(st.get("slot_shortcuts", []) or [])
        if len(scs) >= 8:
            return "按钮已满（最多 8 个）"
        scs.append({"name": name, "path": path})
        st["slot_shortcuts"] = scs
        _pet_api.save_settings(st)
        _pet_api.reload_buttons()
        return "已添加按钮：%s（%s）" % (name, path)
    except Exception as e:
        return "添加按钮失败：%s" % e


def _tool_remove_button(args):
    name = str(args.get("name", "") or "").strip()
    try:
        st = _pet_api.load_settings()
        scs = list(st.get("slot_shortcuts", []) or [])
        keep = []
        _hit, _cands = _match_one(scs, name)
        if _hit is None:
            return _not_found("按钮", name, _cands, "list_buttons")
        keep = [x for x in scs if x is not _hit]
        removed = [_hit.get("name")]
        st["slot_shortcuts"] = keep
        _pet_api.save_settings(st)
        _pet_api.reload_buttons()
        return "已删除按钮：%s（%d 个）" % ("、".join(removed), len(removed))
    except Exception as e:
        return "删除按钮失败：%s" % e


def _tool_move_button(args):
    """调整径向按钮顺序：direction=up/down 或 to=目标序号（0 起）。"""
    name = str(args.get("name", "") or "").strip()
    direction = str(args.get("direction", "") or "").lower()
    to = args.get("to")
    try:
        st = _pet_api.load_settings()
        scs = list(st.get("slot_shortcuts", []) or [])
        _hit, _cands = _match_one(scs, name)
        if _hit is None:
            return _not_found("按钮", name, _cands, "list_buttons")
        idx = scs.index(_hit)
        if isinstance(to, int) and 0 <= to < len(scs):
            s = scs.pop(idx)
            scs.insert(to, s)
        elif direction in ("up", "down"):
            delta = -1 if direction == "up" else 1
            j = idx + delta
            if not (0 <= j < len(scs)):
                return "已经在最%s了" % ("前面" if delta < 0 else "后面")
            scs[idx], scs[j] = scs[j], scs[idx]
        else:
            return "请提供 direction（up/down）或 to（目标序号）"
        st["slot_shortcuts"] = scs
        _pet_api.save_settings(st)
        _pet_api.reload_buttons()
        return "已调整按钮顺序：%s" % name
    except Exception as e:
        return "移动按钮失败：%s" % e


def _tool_edit_button(args):
    """编辑径向按钮：改名和/或改目标（保持原有类型 url/file/cmd）。"""
    name = str(args.get("name", "") or "").strip()
    new_name = str(args.get("new_name", "") or "").strip()
    new_target = str(args.get("new_target", "") or "").strip()
    if not name:
        return "请提供按钮名称"
    if not new_name and not new_target:
        return "请提供 new_name 或 new_target 至少一项"
    try:
        st = _pet_api.load_settings()
        scs = list(st.get("slot_shortcuts", []) or [])
        _hit, _cands = _match_one(scs, name)
        if _hit is None:
            return _not_found("按钮", name, _cands, "list_buttons")
        hit = _hit
        if new_name:
            hit["name"] = new_name
        if new_target:
            hit["path"] = new_target
        st["slot_shortcuts"] = scs
        _pet_api.save_settings(st)
        _pet_api.reload_buttons()
        return "已编辑按钮：%s" % name
    except Exception as e:
        return "编辑按钮失败：%s" % e


# 桌宠设置可调键（与 pet_gravity._apply_pet_settings 对齐）
# 注意 pet_scale / bubble_scale 才是设置窗里「桌宠大小 / 气泡大小」两个滑块
# 对应的键（档位），pet_size 是基准像素、窗里没有对应控件。AI 改"把桌宠调大"
# 要走 pet_scale，否则改了 pet_size 而滑块还停在旧档位——用户看到的就是
# "AI 改完了，设置里还是旧的"。
_PET_SETTING_KEYS = {
    "pet_scale": "level", "bubble_scale": "level",
    "pet_size": "int", "pet_opacity": "float", "button_size": "int",
    "pet_image": "str", "mood_enabled": "bool", "bubble_enabled": "bool",
    "show_tooltips": "bool",
}


def _snap_level(value):
    """把缩放值吸附到设置窗滑块的档位（标准 1.0 / 较大 1.25 / 大 1.5 / 特大 2.0）。

    滑块只能停在这几档，而 `SettingsDialog._level_index()` 对不在表里的值一律
    回 0——不吸附的话 AI 设了 1.3，设置窗会显示成"标准"，两边又不一致了。
    返回 (档位值, 档位名)。
    """
    from pet_gravity import UI_SCALE_OPTIONS
    try:
        f = float(value)
    except (TypeError, ValueError):
        f = 1.0
    name, val = min(UI_SCALE_OPTIONS, key=lambda it: abs(it[1] - f))
    return val, name


def _tool_pet_setting(args):
    key = str(args.get("key", "") or "").strip()
    value = args.get("value")
    if key not in _PET_SETTING_KEYS:
        return "支持的设置键：%s" % "、".join(sorted(_PET_SETTING_KEYS))
    try:
        kind = _PET_SETTING_KEYS[key]
        extra = ""
        if kind == "level":
            v, lname = _snap_level(value)
            extra = "（%s档）" % lname
        elif kind == "int":
            v = max(40, min(200, int(value)))
        elif kind == "float":
            v = max(0.1, min(1.0, float(value)))
        elif kind == "bool":
            v = bool(value)
        else:
            v = str(value)
        st = _pet_api.load_settings()
        st[key] = v
        _pet_api.save_settings(st)
        _pet_api.apply_pet_setting(key, v)
        return "已设置桌宠 %s = %s%s" % (key, v, extra)
    except Exception as e:
        return "设置失败：%s" % e


def _tool_pet_info(args):
    try:
        st = _pet_api.load_settings()
        _pv, pname = _snap_level(st.get("pet_scale", 1.0))
        _bv, bname = _snap_level(st.get("bubble_scale", 1.0))
        return ("桌宠：大小档位 %s（pet_scale=%s，设置窗滑块就是这个），"
                "基准像素 %s，气泡档位 %s，透明度 %s，按钮 %d 个，模块 %d 个"
                % (pname, st.get("pet_scale", 1.0), st.get("pet_size"), bname,
                   st.get("pet_opacity"), len(st.get("slot_shortcuts") or []),
                   len(st.get("status_rules") or [])))
    except Exception as e:
        return "读取桌宠信息失败：%s" % e


def _tool_pet_control(args):
    action = str(args.get("action", "") or "").lower()
    if action == "hide":
        _pet_api.control_pet("hide")
        return "已隐藏桌宠（托盘图标可恢复）"
    if action == "show":
        _pet_api.control_pet("show")
        return "已显示桌宠"
    if action in ("move", "setpos"):
        x = int(args.get("x", 0) or 0)
        y = int(args.get("y", 0) or 0)
        _pet_api.control_pet("move", x=x, y=y)
        return "已把桌宠移到 (%d, %d)" % (x, y)
    return "支持的动作：hide / show / move"


def _valid_api_key(key):
    """判断 api_key 是否为真实可用的 Key（排除占位符/示例 Key）。"""
    key = str(key or "").strip()
    if not key or len(key) < 10:
        return False
    if key.startswith("<") or key.endswith(">"):
        return False
    low = key.lower()
    if "你的" in key or "your" in low or "api key" in low or "api_key" in low:
        return False
    return True


def _ai_profile():
    """统一 AI 大模型配置（settings['ai_profile']）：一处配好，AI 助手/建模块/
    绘画/工具等所有 AI 功能共用。返回含可用 base_url+api_key 的 llm 源 dict；
    未配置或无效则 None（回落到旧的"从启用 llm 规则里找 key"逻辑）。"""
    try:
        from pet_gravity import load_settings
        prof = load_settings().get("ai_profile") or {}
    except Exception:
        prof = {}
    if not isinstance(prof, dict):
        return None
    base = str(prof.get("base_url", "") or "").strip()
    key = str(prof.get("api_key", "") or "").strip()
    # base_url + api_key 都填了、且不是占位符即视为可用（api_key 放宽以兼容 Ollama 的 "ollama"）
    if base and key and not base.startswith("<") and not key.startswith("<") \
            and "你的" not in key:
        out = {"type": "llm"}
        out.update({k: v for k, v in prof.items() if k != "type"})
        return out
    return None


def _merge_ai_profile(st):
    """对 type=llm 的 source，用统一 AI 配置(ai_profile)兜底其空/占位字段，实现
    '一处配好、所有 AI 功能共用'：模块自己没配可用 key 时，整体继承 profile 的
    key+端点+模型（避免 key 与端点错配）；system_prompt 缺失也用 profile 的。
    返回合并后的新 dict；非 llm 或无 profile 时原样返回，不改原对象。"""
    if not isinstance(st, dict) or st.get("type") != "llm":
        return st
    prof = _ai_profile()
    if not prof:
        return st
    out = dict(st)
    if not _valid_api_key(out.get("api_key")):
        # 本模块没配可用 key：整体切到 profile 的 key + 端点 + 模型
        # （key 必须与端点匹配，故一并覆盖 base_url/model，避免 key 与端点错配）。
        out["api_key"] = prof.get("api_key", out.get("api_key", ""))
        if str(prof.get("base_url", "") or "").strip():
            out["base_url"] = prof.get("base_url")
        if str(prof.get("model", "") or "").strip():
            out["model"] = prof.get("model")
    if not str(out.get("system_prompt", "") or "").strip() and prof.get("system_prompt"):
        out["system_prompt"] = prof.get("system_prompt")
    if not str(out.get("api_style", "") or "").strip() and prof.get("api_style"):
        out["api_style"] = prof.get("api_style")
    return out


# 大模型请求的默认超时。
# 工具轮现在走**流式**（见 chat_stream_tools）：流式是边生成边到货，超时只约束
# "多久没有新数据"，所以可以给得宽松；以前工具轮是非流式，首字节要等整段生成完，
# 10 秒的默认值让"写个模块"这种长回答几乎必然超时（用户实测的"调用工具失败"）。
_LLM_TIMEOUT_DEFAULT = 60


def _llm_timeout(st, hard=False):
    """大模型请求超时（秒）。

    hard=True 用于非流式回退：那时首字节要等整段生成完，必须给足。
    """
    try:
        v = float(st.get("timeout") or 0)
    except Exception:
        v = 0.0
    if v <= 0:
        v = _LLM_TIMEOUT_DEFAULT
    return max(v, 120.0) if hard else v


def _apply_llm_auth(req, base, key, style=None):
    """统一给大模型请求加认证头。

    style 为接入协议（接入规则），当前主流两套：
    - "openai"    ：Authorization: Bearer（绝大多数服务商）
    - "anthropic" ：额外带 x-api-key + anthropic-version
    协议与具体服务商无关；未显式给出时按 URL 兜底推断（兼容旧配置）。
    """
    if key:
        req.add_header("Authorization", "Bearer " + key)
    st = str(style or "").strip().lower()
    if not st:
        st = "anthropic" if "anthropic" in str(base or "").lower() else "openai"
    if st == "anthropic":
        if key:
            req.add_header("x-api-key", key)
        req.add_header("anthropic-version", "2023-06-01")


def list_llm_models(base_url, api_key, timeout=15, api_style=None):
    """拉取该 Key 可用的模型列表。返回 (ok, [模型名...], 提示文案)。

    OpenAI 兼容与 Anthropic 兼容都提供 `GET {base}/models`，响应结构一致
    （`{"data":[{"id":...}]}`）；Ollama 原生的 `{"models":[{"name":...}]}`
    也一并兼容。供「AI 设置」里的"拉取模型"按钮使用。
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return False, [], "缺少接口地址"
    url = base + "/models"
    req = urllib.request.Request(url, method="GET")
    _apply_llm_auth(req, base, str(api_key or ""), api_style)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(_MAX_RESP_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(4096).decode("utf-8", "replace")[:160]
        except Exception:
            detail = ""
        return False, [], "HTTP %s：%s" % (e.code, detail or getattr(e, "reason", ""))
    except Exception as e:
        return False, [], "拉取失败：%s" % e
    names = []
    try:
        items = data.get("data") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            items = data.get("models")          # Ollama 原生格式
        for it in (items or []):
            if isinstance(it, dict):
                n = it.get("id") or it.get("name") or it.get("model")
            else:
                n = it
            n = str(n or "").strip()
            if n and n not in names:
                names.append(n)
    except Exception as e:
        return False, [], "响应解析失败：%s" % e
    if not names:
        return False, [], "接口未返回模型列表"
    names.sort()
    return True, names, "找到 %d 个模型" % len(names)


def ping_llm(base_url, model, api_key, timeout=15, api_style=None):
    """测试一个 LLM 配置是否可用：发一条最小 chat 请求。返回 (ok, 提示文案)。
    供「AI 设置」的测试连接按钮使用。"""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return False, "缺少 base_url"
    if not str(api_key or "").strip():
        return False, "缺少 api_key"
    url = base + "/chat/completions"
    body = {"model": str(model or "").strip() or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1, "stream": False}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    _apply_llm_auth(req, base, str(api_key), api_style)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.loads(resp.read(_MAX_RESP_BYTES).decode("utf-8"))
        return True, "连接成功（模型：%s）" % (str(model).strip() or "默认")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(4096).decode("utf-8", "replace")[:160]
        except Exception:
            detail = ""
        return False, "HTTP %s：%s" % (e.code, detail or getattr(e, "reason", ""))
    except Exception as e:
        return False, "连接失败：%s" % e


def _llm_source_cfg():
    """取已配置的大模型源：优先用统一 AI 配置（settings['ai_profile']），
    否则回落到"从启用的 type=llm 规则里找真实 api_key"（向后兼容旧配置）。"""
    prof = _ai_profile()
    if prof is not None:
        return prof
    from pet_gravity import load_settings
    st = load_settings()
    candidates = []
    for r in st.get("status_rules", []):
        s2 = (r.get("source") or {})
        if s2.get("type") == "llm" and _valid_api_key(s2.get("api_key")):
            candidates.append((r, s2))
    if not candidates:
        return None
    # 优先启用的规则（与对话 AI 实际使用的一致）
    for r, s2 in candidates:
        if r.get("enabled", True):
            return s2
    return candidates[0][1]


def _llm_json_array(prompt):
    """调用已配置大模型，要求只返回 JSON 数组；返回解析后的 list。
    失败抛异常（含未配置/网络/解析错误），让上层把真实结果回报给 AI。"""
    cfg = _llm_source_cfg()
    if cfg is None:
        raise RuntimeError("未配置大模型（需在 AI助手 模块里填 api_key）")
    body = {"model": cfg.get("model", "deepseek-chat"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": 4000}
    req = urllib.request.Request(
        str(cfg.get("base_url", "https://api.deepseek.com/v1")).rstrip("/")
        + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % cfg.get("api_key", "")})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read(_MAX_RESP_BYTES).decode("utf-8"))
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    # 提取 JSON 数组：AI 可能带 ```json 包裹、说明文字、尾部注释
    m = re.search(r"\[[\s\S]*\]", content)
    if not m:
        raise RuntimeError("AI 未返回像素指令")
    raw = m.group(0).strip()
    # 去掉 ```json ... ``` 围栏（若数组被围栏包住，re 仍会匹配到内部 []，此处兜底）
    raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw)
    return json.loads(raw)


def _tool_canvas_draw(args):
    """AI 绘画：仅在用户明确要求画布绘制时调用。同步生成图元指令并下发主线程执行，
    返回真实结果（已绘制 N 个图元 / 失败原因），AI 据此如实回答。"""
    desc = str(args.get("description", "") or "").strip()
    if not desc:
        return "请提供要绘制的内容描述"
    prompt = (
        "你在一个 100x100 的白色画布上作画。根据用户描述输出 JSON 数组，"
        "每个元素是一个图元："
        '{"type":"line","x1":..,"y1":..,"x2":..,"y2":..,"color":"#rrggbb","width":2}, '
        '{"type":"circle","cx":..,"cy":..,"r":..,"color":"#rrggbb","width":2}, '
        '{"type":"rect","x":..,"y":..,"w":..,"h":..,"color":"#rrggbb","width":2}, '
        '{"type":"fill","x":..,"y":..,"w":..,"h":..,"color":"#rrggbb"}, '
        '{"type":"text","x":..,"y":..,"text":"..","color":"#rrggbb","size":8}. '
        "只输出 JSON 数组，不要任何其他文字。用户描述：" + desc)
    try:
        cmds = _llm_json_array(prompt)
    except Exception as e:
        return "绘制失败：%s" % e
    if not isinstance(cmds, list) or not cmds:
        return "AI 未返回可绘制的内容"
    _pet_api.command("canvas_apply", {"cmds": cmds})
    return "已在画布上绘制 %d 个图元" % len(cmds)


def _tool_perler_draw(args):
    """AI 拼豆绘画：仅在用户明确要求"拼豆/像素画"时调用。同步生成像素坐标并下发
    主线程填色，返回真实结果（已绘制 N 个像素 / 失败原因）。"""
    desc = str(args.get("description", "") or "").strip()
    if not desc:
        return "请提供要绘制的内容描述"
    prompt = (
        "你在一个 11 列 x 10 行的像素画板上作画，坐标 x 为列 0-10、y 为行 0-9。"
        "根据用户描述输出 JSON 数组，每个元素："
        '{"x":列0-10,"y":行0-9,"color":"#rrggbb"}。'
        "尽量让图案居中、像素化、有轮廓。只输出 JSON 数组，不要其他文字。"
        "用户描述：" + desc)
    try:
        cells = _llm_json_array(prompt)
    except Exception as e:
        return "绘制失败：%s" % e
    if not isinstance(cells, list) or not cells:
        return "AI 未返回可绘制的像素指令"
    _pet_api.command("perler_apply", {"cells": cells})
    return "已在拼豆上绘制 %d 个像素" % len(cells)


TOOL_DEFS = {
    "get_time": {
        "description": "获取当前日期和时间",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_get_time},
    "add_todo": {
        "description": "把一件事加入桌宠的待办列表（todo_data.json）",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string",
                                               "description": "待办内容"}},
                       "required": ["text"]},
        "func": _tool_add_todo},
    "list_todos": {
        "description": "查看当前待办列表",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_list_todos},
    "open_url": {
        "description": "用默认浏览器打开一个网址",
        "parameters": {"type": "object",
                       "properties": {"url": {"type": "string"}},
                       "required": ["url"]},
        "func": _tool_open_url},
    "remind": {
        "description": "设置提醒：seconds=一次性N秒后；every=每N秒重复提醒；daily=每天HH:MM提醒",
        "parameters": {"type": "object",
                       "properties": {"seconds": {"type": "integer",
                                                  "description": "多少秒后提醒（一次性）"},
                                      "every": {"type": "integer",
                                                 "description": "每多少秒重复提醒（周期性）"},
                                      "daily": {"type": "string",
                                                 "description": "每天提醒时刻，格式 HH:MM，如 09:00"},
                                      "message": {"type": "string",
                                                  "description": "提醒内容"}},
                       "required": ["message"]},
        "func": _tool_remind},
    "delete_todo": {
        "description": "删除待办列表里的某项（按内容或序号，序号从 1 开始）",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string",
                                               "description": "待办内容或序号"}},
                       "required": ["text"]},
        "func": _tool_delete_todo},
    "list_components": {
        "description": "列出 widgets/ 目录下可用的自定义组件（source.ui 可填的值，如 counter/todo/panel/webchat）",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_list_components},
    "list_modules": {
        "description": "列出桌宠当前所有气泡模块（名称 + 启用状态）",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_list_modules},
    "list_templates": {
        "description": "列出模块模板库（优先用模板建模块，避免手写 JSON 出错）",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_list_templates},
    "add_module_from_template": {
        "description": "按模板创建模块（template=模板名，name=模块名，params=参数对象）。"
                      "模板见 list_templates：clock 时间/countdown 倒计时/weather 天气/"
                      "static 文本/counter 计数器/todo 待办/script 脚本。",
        "parameters": {"type": "object",
                       "properties": {"template": {"type": "string"},
                                      "name": {"type": "string"},
                                      "params": {"type": "object"}},
                       "required": ["template", "name"]},
        "func": _tool_add_module_from_template},
    "add_module": {
        "description": (
            "新建一个气泡模块（手写完整 rule JSON；建议优先用 add_module_from_template）。"
            "rule 格式：{name, enabled, interval(秒), embed(bool), fallback, source}。"
            "source.type 可选：static/http/clock/script/file/llm/agent。"
            "source.ui 可挂自定义组件（先 list_components 查）。"
            "需要记住状态（如计数）用 script + python + state 字典。"
            "添加后我会立即试取数并返回结果，失败请据此修正。"
        ),
        "parameters": {"type": "object",
                       "properties": {"rule": {"type": "object",
                                               "description": "规则 JSON，至少含 name 和 source"}},
                       "required": ["rule"]},
        "func": _tool_add_module},
    "remove_module": {
        "description": "删除一个气泡模块（按名称，模糊匹配；内置模块也能删）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
        "func": _tool_remove_module},
    "move_module": {
        "description": "调整气泡模块的显示顺序（direction=up/down 或 to=目标序号从 0 开始）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"},
                                      "direction": {"type": "string",
                                                    "description": "up 或 down"},
                                      "to": {"type": "integer",
                                             "description": "目标序号（0 起）"}},
                       "required": ["name"]},
        "func": _tool_move_module},
    "enable_module": {
        "description": "启用或禁用一个气泡模块（按名称）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"},
                                      "enabled": {"type": "boolean",
                                                  "description": "true 启用 / false 禁用"}},
                       "required": ["name"]},
        "func": _tool_enable_module},
    "list_buttons": {
        "description": "列出桌宠径向菜单当前所有按钮",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_list_buttons},
    "add_button": {
        "description": "往径向菜单添加一个按钮（kind: url 网页 / file 文件程序 / cmd 命令）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string", "description": "按钮名"},
                                      "kind": {"type": "string", "description": "url/file/cmd"},
                                      "target": {"type": "string", "description": "网址/路径/命令"}},
                       "required": ["name", "kind", "target"]},
        "func": _tool_add_button},
    "remove_button": {
        "description": "删除径向菜单里的一个按钮（按名称，模糊匹配）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
        "func": _tool_remove_button},
    "move_button": {
        "description": "调整径向菜单按钮的顺序（direction=up/down 或 to=目标序号从 0 开始）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"},
                                      "direction": {"type": "string"},
                                      "to": {"type": "integer"}},
                       "required": ["name"]},
        "func": _tool_move_button},
    "edit_button": {
        "description": "编辑径向菜单按钮：改名 new_name 和/或改目标 new_target（保持原类型）",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"},
                                      "new_name": {"type": "string"},
                                      "new_target": {"type": "string"}},
                       "required": ["name"]},
        "func": _tool_edit_button},
    "pet_control": {
        "description": "控制桌宠本体：hide 隐藏 / show 显示 / move 移动到 (x,y)",
        "parameters": {"type": "object",
                       "properties": {"action": {"type": "string",
                                                 "description": "hide/show/move"},
                                      "x": {"type": "integer"},
                                      "y": {"type": "integer"}},
                       "required": ["action"]},
        "func": _tool_pet_control},
    "canvas_draw": {
        "description": "仅在用户明确要求'在无限画布/画布上画 XX'时调用，在无限画布上绘制自由图形；"
                      "如果用户说的是'拼豆/像素画'，改用 perler_draw；平时聊天提到画/图片等不要调用",
        "parameters": {"type": "object",
                       "properties": {"description": {"type": "string",
                                                      "description": "要绘制的内容描述，如：画一只戴帽子的猫"}},
                       "required": ["description"]},
        "func": _tool_canvas_draw},
    "perler_draw": {
        "description": "仅在用户明确要求'拼豆/像素画/像素图'时调用，在拼豆（10x10 像素网格）上按描述填色绘制；"
                      "如果用户说的是'无限画布/画布'，改用 canvas_draw",
        "parameters": {"type": "object",
                       "properties": {"description": {"type": "string",
                                                      "description": "要绘制的图案描述，如：像素猫"}},
                       "required": ["description"]},
        "func": _tool_perler_draw},
    "pet_setting": {
        "description": "调整桌宠设置。**改桌宠/气泡大小请用档位键**："
                      "pet_scale / bubble_scale（1.0 标准 / 1.25 较大 / 1.5 大 / "
                      "2.0 特大，就是设置窗那两个滑块，会自动吸附到最近一档）；"
                      "其余：pet_size(基准像素 40-200，一般不用动) / "
                      "pet_opacity(0.1-1.0) / button_size / pet_image(图片路径) / "
                      "mood_enabled / bubble_enabled / show_tooltips",
        "parameters": {"type": "object",
                       "properties": {"key": {"type": "string",
                                              "description": "设置键名"},
                                      "value": {"type": "object",
                                                "description": "设置值（数字/字符串/布尔）"}},
                       "required": ["key", "value"]},
        "func": _tool_pet_setting},
    "pet_info": {
        "description": "查看桌宠当前信息（大小/透明度/按钮数/模块数/布局）",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "func": _tool_pet_info},
}


def tool_names():
    """返回已注册工具名列表。"""
    return list(TOOL_DEFS.keys())


# ==================== 扩展 API ====================

class StatusProvider:
    """状态提供者基类：子类实现 collect() 即可接入气泡显示。"""

    name = "base"       # 唯一标识（必填）
    title = "未命名"     # 显示名称
    interval = 2.0      # 刷新间隔（秒）
    enabled = True      # False 则跳过该提供者

    def collect(self):
        raise NotImplementedError


_STATUS_PROVIDERS = []  # 有序注册表


def register_provider(cls):
    """注册一个状态提供者（装饰器用法 @register_provider）。"""
    if not cls.name or cls.name == "base":
        raise ValueError("StatusProvider 必须设置唯一的 name")
    _STATUS_PROVIDERS.append(cls())
    return cls


def get_providers():
    """返回当前所有已启用提供者的副本。"""
    return [p for p in _STATUS_PROVIDERS if p.enabled]


def _fmt_rate(kbps):
    """把 KB/s 格式化，过大自动转 MB/s。"""
    if kbps >= 1024:
        return "%.1f MB/s" % (kbps / 1024.0)
    return "%.0f KB/s" % kbps


# ==================== 内置提供者 ====================

@register_provider
class CpuProvider(StatusProvider):
    """CPU 使用率（GetSystemTimes 差值）"""

    name = "cpu"
    title = "CPU"
    interval = 1.0

    def __init__(self):
        super().__init__()
        self._prev = None

    @staticmethod
    def _times():
        if sys.platform != "win32":
            return None
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None

        def to64(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return to64(idle), to64(kernel), to64(user)

    def collect(self):
        now = self._times()
        if now is None:
            return "—"
        if self._prev is None:
            self._prev = now
            return "…"
        idle_d = max(0, now[0] - self._prev[0])
        kern_d = max(0, now[1] - self._prev[1])
        user_d = max(0, now[2] - self._prev[2])
        self._prev = now
        total = kern_d + user_d
        if total <= 0:
            return "0%"
        busy = total - idle_d
        return "%.0f%%" % max(0.0, min(100.0, busy / total * 100.0))


@register_provider
class MemoryProvider(StatusProvider):
    """内存占用（GlobalMemoryStatusEx）"""

    name = "memory"
    title = "内存"
    interval = 2.0

    def collect(self):
        if sys.platform != "win32":
            return "仅支持 Windows"
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wt.DWORD),
                ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return "—"
        total = st.ullTotalPhys / (1024.0 ** 3)
        used = (st.ullTotalPhys - st.ullAvailPhys) / (1024.0 ** 3)
        return "%d%%  (%.1f/%.1f GB)" % (st.dwMemoryLoad, used, total)


@register_provider
class BatteryProvider(StatusProvider):
    """电池电量与充电状态（GetSystemPowerStatus）"""

    name = "battery"
    title = "电池"
    interval = 5.0

    def collect(self):
        if sys.platform != "win32":
            return "仅支持 Windows"
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wt.BYTE),
                ("BatteryFlag", wt.BYTE),
                ("BatteryLifePercent", wt.BYTE),
                ("SystemStatusFlag", wt.BYTE),
                ("BatteryLifeTime", wt.DWORD),
                ("BatteryFullLifeTime", wt.DWORD),
            ]
        st = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return "—"
        if st.BatteryLifePercent == 255:
            return "无电池（台式机）"
        state = "充电中" if st.ACLineStatus == 1 else "使用电池"
        return "%d%%  %s" % (st.BatteryLifePercent, state)


@register_provider
class DiskProvider(StatusProvider):
    """磁盘空间（GetDiskFreeSpaceExW）"""

    name = "disk"
    title = "磁盘 C"
    interval = 5.0
    path = "C:\\"

    def collect(self):
        if sys.platform != "win32":
            return "仅支持 Windows"
        total = ctypes.c_ulonglong(0)
        free = ctypes.c_ulonglong(0)
        if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                self.path, None, ctypes.byref(total), ctypes.byref(free)):
            return "—"
        total_g = total.value / (1024.0 ** 3)
        free_g = free.value / (1024.0 ** 3)
        used_g = total_g - free_g
        pct = (used_g / total_g * 100.0) if total_g > 0 else 0.0
        return "%.0f%%  (剩 %.0f/%.0f GB)" % (pct, free_g, total_g)


@register_provider
class NetworkProvider(StatusProvider):
    """实时网速（GetIfTable 差值，聚合所有活跃网卡）"""

    name = "network"
    title = "网络"
    interval = 2.0

    class MIB_IFROW(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", wt.DWORD),
            ("dwType", wt.DWORD),
            ("dwMtu", wt.DWORD),
            ("dwSpeed", wt.DWORD),
            ("dwPhysAddrLen", wt.DWORD),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", wt.DWORD),
            ("dwOperStatus", wt.DWORD),
            ("dwLastChange", wt.DWORD),
            ("dwInOctets", wt.DWORD),
            ("dwInUcastPkts", wt.DWORD),
            ("dwInNUcastPkts", wt.DWORD),
            ("dwInDiscards", wt.DWORD),
            ("dwInErrors", wt.DWORD),
            ("dwInUnknownProtos", wt.DWORD),
            ("dwOutOctets", wt.DWORD),
            ("dwOutUcastPkts", wt.DWORD),
            ("dwOutNUcastPkts", wt.DWORD),
            ("dwOutDiscards", wt.DWORD),
            ("dwOutErrors", wt.DWORD),
            ("dwOutQLen", wt.DWORD),
            ("dwDescrLen", wt.DWORD),
            ("bDescr", ctypes.c_ubyte * 256),
        ]

    def __init__(self):
        super().__init__()
        self._prev = None
        self._prev_t = None

    def _stats(self):
        try:
            iphlpapi = ctypes.windll.iphlpapi
            size = wt.DWORD(0)
            # 第一次调用只用于获取所需缓冲区大小（返回 ERROR_INSUFFICIENT_BUFFER 属正常）
            iphlpapi.GetIfTable(None, ctypes.byref(size), False)
            if size.value == 0:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if iphlpapi.GetIfTable(buf, ctypes.byref(size), False) != 0:
                return None
            num = ctypes.cast(buf, ctypes.POINTER(wt.DWORD)).contents.value
            row_size = ctypes.sizeof(self.MIB_IFROW)
            base = ctypes.addressof(buf) + ctypes.sizeof(wt.DWORD)
            total_in = total_out = 0
            for i in range(num):
                row = ctypes.cast(base + i * row_size, ctypes.POINTER(self.MIB_IFROW)).contents
                # 活跃的以太网/无线/拨号接口
                if row.dwOperStatus == 1 and row.dwType in (6, 71, 23):
                    total_in += row.dwInOctets
                    total_out += row.dwOutOctets
            return total_in, total_out
        except Exception:
            return None

    def collect(self):
        if sys.platform != "win32":
            return "仅支持 Windows"
        now = self._stats()
        t = time.monotonic()
        if now is None:
            return "—"
        if self._prev is None or self._prev_t is None:
            self._prev, self._prev_t = now, t
            return "…"
        dt = max(0.5, t - self._prev_t)
        down = max(0, now[0] - self._prev[0]) / dt / 1024.0
        up = max(0, now[1] - self._prev[1]) / dt / 1024.0
        self._prev, self._prev_t = now, t
        return "↓ %s  ↑ %s" % (_fmt_rate(down), _fmt_rate(up))


# ==================== 用户自定义规则 ====================

class RuleProvider(StatusProvider):
    """用户自定义规则：http / llm / file / static，统一输出文本。

    规则项（pet_settings.json 的 status_rules 列表）：
        {"id": "...", "name": "天气", "interval": 600, "enabled": true,
         "source": {"type": "http", "url": "https://wttr.in/?format=%c+%t", "timeout": 5},
         "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "天气：$1"},
         "fallback": "天气获取失败"}
    """

    _RECENT_K = 8         # 压缩时保留最近几条原文
    _SUMMARY_CHARS = 1800 # 较早历史总字符超过该值就触发摘要压缩

    def __init__(self, rule):
        super().__init__()
        from module_core import module_spec
        self.rule = rule if isinstance(rule, dict) else {}
        self.spec = module_spec(self.rule)
        self.rule = self.spec.rule
        self.name = self.spec.key
        self.title = self.spec.name
        self.interval = self.spec.interval
        self.popup = bool(self.rule.get("popup", False))      # 内容变化时自动弹出气泡
        self._pending = None   # agent 类型：当前待审批请求
        self._last_error = ""  # 最近一次取数/调用失败的原因
        self._hung = False     # 脚本超时卡死后暂停该模块（避免反复堆积线程）
        self.state = {}        # script 类型脚本的持久状态
        self._summary = None   # LLM 对话摘要（懒加载，None=未读取）
        try:
            self.max_chars = max(0, int(self.rule.get("max_chars", 0) or 0))
        except Exception:
            self.max_chars = 0

    def collect(self):
        if getattr(self, "_hung", False):
            self._last_error = "脚本执行超时，该模块已暂停（重新保存设置或重启后恢复）"
            return str(self.rule.get("fallback", "—"))
        try:
            val = self._transform(self._fetch())
            self._last_error = ""
        except Exception as e:
            self._last_error = str(e)
            fb = self.rule.get("fallback", "")
            val = fb if fb else "—"
        if self.max_chars and len(val) > self.max_chars:
            val = val[:self.max_chars] + "…"
        return val

    def _source(self):
        src = self.rule.get("source") or {}
        # 防御：source 必须是对象（AI 生成的规则可能把 URL 字符串直接当 source，
        # 会导致 .get 崩溃拖垮整个桌宠；畸形时按空配置处理，只显示 fallback）
        if not isinstance(src, dict):
            return {}
        # type=llm 时用统一 AI 配置兜底空字段：所有 llm 取数/对话/工具路径都经此，
        # 实现"一处配好、所有 AI 功能共用"（非 llm 零成本，直接原样返回）。
        return _merge_ai_profile(src)

    def _fetch(self):
        st = self._source()
        t = st.get("type", "static")
        if t == "http":
            return self._fetch_http(st)
        if t == "pomodoro":
            try:
                from h5_cards import PomodoroState
                return PomodoroState.current_text()
            except Exception:
                return "未启动"
        if t == "script":
            return self._fetch_script(st)
        if t == "llm":
            if st.get("mode") == "task":
                return self._fetch_llm_task(st)   # 单次任务模式（可 JSON 结构化）
            return self._fetch_llm(st)
        if t == "clock":
            return self._fetch_clock(st)
        if t == "disk":
            return self._fetch_disk(st)
        if t == "agent":
            return self._fetch_agent(st)
        if t == "file":
            with open(st.get("path", ""), "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        return str(st.get("text", ""))

    def _fetch_script(self, st):
        """执行用户脚本（Python / JavaScript / Shell），脚本输出作为结果。"""
        code = str(st.get("code", "") or "")
        lang = str(st.get("lang", "python") or "python").lower()
        if not code.strip():
            return ""
        try:
            if lang in ("python", "py"):
                timeout = float(st.get("timeout", 15) or 15)
                loc = {"state": self.state, "time": time, "math": math, "result": ""}
                holder = {}

                def _run():
                    try:
                        exec(code, loc)
                    except Exception as e:
                        holder["err"] = e

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                t.join(timeout)
                if t.is_alive():
                    # 线程无法强制终止：暂停该模块，避免持续堆积卡死线程
                    self._hung = True
                    self._last_error = ("脚本执行超过 %s 秒，该模块已暂停"
                                        % int(timeout))
                    return str(self.rule.get("fallback", "脚本超时"))
                if "err" in holder:
                    raise holder["err"]
                return str(loc.get("result", ""))
            if lang in ("javascript", "js"):
                import shutil
                import subprocess
                timeout = float(st.get("timeout", 15) or 15)
                node = shutil.which("node")
                if not node:
                    raise RuntimeError("未找到 node：JavaScript 需要安装 Node.js")
                r = subprocess.run([node, "-e", code], capture_output=True, text=True,
                                   timeout=timeout, creationflags=0x08000000)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout).strip()[:200])
                return r.stdout.strip()
            if lang in ("shell", "sh", "bash", "cmd", "powershell"):
                import subprocess
                timeout = float(st.get("timeout", 15) or 15)
                if lang == "powershell":
                    r = subprocess.run(["powershell", "-NoProfile", "-Command", code],
                                       capture_output=True, text=True, timeout=timeout,
                                       creationflags=0x08000000)
                else:
                    r = subprocess.run(code, capture_output=True, text=True, timeout=timeout,
                                       shell=True, creationflags=0x08000000)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout).strip()[:200])
                return r.stdout.strip()
            return ""
        except Exception as e:
            self._last_error = str(e)
            return str(self.rule.get("fallback", "脚本错误"))

    def _fetch_http(self, st):
        url = st.get("url", "")
        if not str(url).startswith(("http://", "https://")):
            raise ValueError("URL 必须是 http/https")
        req = urllib.request.Request(url, method=str(st.get("method", "GET")).upper())
        if st.get("plain_text"):
            # 纯文本接口（如 wttr.in）：浏览器 UA 会返回完整网页，用 curl UA 拿纯文本
            req.add_header("User-Agent", "curl/8.0")
        else:
            # 默认模拟浏览器 UA，避免部分接口因缺 UA 返回 403/超时
            req.add_header(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        for k, v in (st.get("headers") or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=float(st.get("timeout", 5) or 5)) as resp:
            data = resp.read(_MAX_RESP_BYTES)
        try:
            txt = data.decode("utf-8")
        except Exception:
            txt = data.decode("gbk", errors="replace")
        try:
            return json.loads(txt)
        except Exception:
            return txt

    def _fetch_agent(self, st):
        """读取智能体待审批请求：GET endpoint，返回 [{id, title}, ...] 中第一条"""
        endpoint = st.get("endpoint", "")
        if not str(endpoint).startswith(("http://", "https://")):
            raise ValueError("缺少 endpoint")
        req = urllib.request.Request(endpoint, method="GET")
        if st.get("api_key_env"):
            req.add_header("Authorization", "Bearer " + os.environ.get(st["api_key_env"], ""))
        with urllib.request.urlopen(req, timeout=float(st.get("timeout", 5) or 5)) as resp:
            data = json.loads(resp.read(_MAX_RESP_BYTES).decode("utf-8"))
        items = data.get("requests") if isinstance(data, dict) else data
        items = items if isinstance(items, list) else []
        self._pending = items[0] if items else None
        if self._pending:
            return "待审批：%s" % self._pending.get("title", "新请求")
        return "无待审批请求"

    def chat(self, text, history=None):
        """对话模式：历史过长时先压缩成摘要，再把摘要+近期原文+当前输入发给大模型。"""
        st = self._source()
        if st.get("type") != "llm":
            raise ValueError("仅大模型规则支持对话")
        st2 = dict(st)
        # 对话回复给足长度：避免 max_tokens 过小把回复截断（支持长代码完整输出）
        st2["max_tokens"] = max(int(st2.get("max_tokens", 8192) or 8192), 8192)
        history = list(history or [])
        summary = self._load_summary()
        if len(history) > self._RECENT_K:
            old = history[:-self._RECENT_K]
            recent = history[-self._RECENT_K:]
            if self._old_too_long(old):
                # 较早的对话压缩成摘要（面板仍显示完整记录，只压缩发给模型的上下文）
                summary = self._summarize(st, summary, old) or summary
                if summary:
                    self._save_summary(summary)
        else:
            recent = history
        return self._fetch_llm(st2, history=recent, summary=summary, user_text=text)

    def chat_stream(self, text, history=None, stop_event=None, images=None):
        """流式对话：生成器逐块产出累计文本（SSE），失败时 yield 空（由调用方回退）。
        stop_event 为 threading.Event：置位后中止流式（用户点"停止"）。
        images 为本地图片路径列表（多模态识图，转 base64 内嵌）。"""
        st = self._source()
        if st.get("type") != "llm":
            raise ValueError("仅大模型规则支持对话")
        st2 = dict(st)
        st2["max_tokens"] = max(int(st2.get("max_tokens", 8192) or 8192), 8192)
        history = list(history or [])
        summary = self._load_summary()
        if len(history) > self._RECENT_K:
            old = history[:-self._RECENT_K]
            recent = history[-self._RECENT_K:]
            if self._old_too_long(old):
                summary = self._summarize(st, summary, old) or summary
                if summary:
                    self._save_summary(summary)
        else:
            recent = history
        messages = [{"role": "system", "content": str(st2.get("system_prompt", ""))}]
        if summary:
            messages.append({"role": "system",
                             "content": "之前的对话摘要：\n" + summary})
        for m in (recent or [])[-20:]:
            role = "assistant" if m.get("role") == "assistant" else "user"
            content = str(m.get("text", "")).strip()
            if content:
                messages.append({"role": role, "content": content})
        # 当前输入：带图片时用多模态 content（文字 + image_url）
        imgs = [p for p in (images or []) if p]
        if imgs:
            clean_text = re.sub(r'\[图片:[^\]]*\]', '', str(text)).strip()
            content = [{"type": "text", "text": clean_text or str(text)}]
            for img in imgs:
                data_url = self._image_data_url(img)
                if data_url:
                    content.append({"type": "image_url",
                                    "image_url": {"url": data_url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": str(text)})
        body = {"model": st2.get("model", ""), "messages": messages,
                "temperature": float(st2.get("temperature", 0.8) or 0.8),
                "max_tokens": int(st2.get("max_tokens", 8192) or 8192),
                "stream": True}
        yield from self._stream_sse(st2, body, stop_event)

    def _image_data_url(self, path):
        """本地图片转 data URL（多模态识图内嵌用），失败返回 None。"""
        try:
            import base64
            import mimetypes
            with open(path, "rb") as f:
                data = f.read()
            mime = mimetypes.guess_type(path)[0] or "image/png"
            return "data:%s;base64,%s" % (
                mime, base64.b64encode(data).decode("ascii"))
        except Exception:
            return None

    def abort_stream(self):
        """中断当前活动的流式请求：关闭响应对象，解除 worker 线程的阻塞读。"""
        resp = getattr(self, "_active_resp", None)
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _sse_open(self, st2, body):
        """开一个 SSE 流，返回响应对象。工具轮和正文流共用同一套请求准备。"""
        base = str(st2.get("base_url", "")).strip().rstrip("/")
        if not base:
            raise ValueError("缺少 base_url")
        if not base.endswith("/chat/completions"):
            base += "/chat/completions"
        key = st2.get("api_key", "") or ""
        if not key and st2.get("api_key_env"):
            key = os.environ.get(st2["api_key_env"], "") or ""
        req = urllib.request.Request(base, data=json.dumps(body).encode("utf-8"),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        _apply_llm_auth(req, base, key, st2.get("api_style"))
        return urllib.request.urlopen(req, timeout=_llm_timeout(st2))

    @staticmethod
    def _sse_events(resp, stop_event=None):
        """把 SSE 响应逐条解析成 event dict（跳过非 data 行）。"""
        for raw in resp:
            if stop_event is not None and stop_event.is_set():
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except Exception:
                continue

    def _stream_sse(self, st2, body, stop_event=None):
        """SSE 流式请求：逐块产出 (累计文本, 思考文本)。"""
        acc = ""
        reasoning = ""
        self._active_resp = None
        try:
            with self._sse_open(st2, body) as resp:
                self._active_resp = resp
                try:
                    for obj in self._sse_events(resp, stop_event):
                        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                        piece = delta.get("content") or ""
                        rp = delta.get("reasoning_content") or ""
                        if rp:
                            reasoning += rp
                        if piece:
                            acc += piece
                        if piece or rp:
                            yield acc, reasoning
                except Exception:
                    # 被 abort_stream 关闭响应而中断：若是用户停止则静默结束
                    if stop_event is None or not stop_event.is_set():
                        raise
        finally:
            self._active_resp = None

    def chat_stream_tools(self, text, history=None, tools=None, stop_event=None,
                          on_tool=None):
        """带 function calling 的对话：先处理工具调用，最后一轮输出正文。

        **每一轮都走流式**，所以"模型想得久"不会再变成超时失败；
        仅在服务端不下发流式 tool_calls 时退回非流式（那时才用放宽的超时）。
        轮数不设上限，靠"同一工具+同一参数连续重复"检测防死循环。

        tools 为工具名列表（见 TOOL_DEFS）。
        stop_event 为 threading.Event：置位后中止（用户点"停止"）。
        on_tool(name, args, result)：每次工具执行后回调（过程可视化）。
        """
        st = self._source()
        if st.get("type") != "llm":
            raise ValueError("仅大模型规则支持对话")
        # 使用行为采集：记录一次对话（通用统计表数据源）
        try:
            _record_usage("chat", module=str(self.rule.get("name", "AI助手")))
        except Exception:
            pass
        st2 = dict(st)
        st2["max_tokens"] = max(int(st2.get("max_tokens", 8192) or 8192), 8192)
        history = list(history or [])
        summary = self._load_summary()
        if len(history) > self._RECENT_K:
            old = history[:-self._RECENT_K]
            recent = history[-self._RECENT_K:]
            if self._old_too_long(old):
                summary = self._summarize(st, summary, old) or summary
                if summary:
                    self._save_summary(summary)
        else:
            recent = history
        messages = [{"role": "system", "content": str(st2.get("system_prompt", ""))}]
        if summary:
            messages.append({"role": "system",
                             "content": "之前的对话摘要：\n" + summary})
        for m in (recent or [])[-20:]:
            role = "assistant" if m.get("role") == "assistant" else "user"
            content = str(m.get("text", "")).strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(text)})
        names = [t for t in (tools or []) if t in TOOL_DEFS]
        if not names:
            yield from self.chat_stream(text, history)
            return
        tool_objs = [{"type": "function",
                      "function": {"name": n,
                                   "description": tool_description(n),
                                   "parameters": TOOL_DEFS[n]["parameters"]}}
                     for n in names]
        # 工具循环不设轮数上限；用"重复调用检测"防烧 token：
        # 同一工具 + 同一参数连续重复 >=5 次视为死循环，停止
        repeat_track = []   # 最近调用签名
        # **工具轮走流式**：首字节一到就能读到，超时只约束"多久没有新数据"，
        # 所以不会因为"模型想得久"而失败。以前是非流式，首字节要等整段生成完，
        # 默认 10 秒超时让"帮我写个模块"这种长回答几乎必然失败 —— 用户实测的
        # "调用工具失败"就是这条。
        use_stream = True
        while True:
            if stop_event is not None and stop_event.is_set():
                yield "（已停止）", ""
                return
            body = {"model": st2.get("model", ""), "messages": messages,
                    "temperature": float(st2.get("temperature", 0.8) or 0.8),
                    "max_tokens": int(st2.get("max_tokens", 8192) or 8192),
                    "tools": tool_objs}
            if use_stream:
                msg, empty = self._tool_round_stream(st2, body, stop_event)
                if empty:
                    # 整轮既没有正文也没有工具调用：可能是该服务商的流式实现不
                    # 下发 tool_calls。退回非流式（那才需要把超时放宽）再要一次。
                    use_stream = False
                    continue
            else:
                b = dict(body)
                b["stream"] = False
                msg = self._llm_request_msg(st2, b)
            calls = msg.get("tool_calls") or []
            if not calls:
                # 无工具调用：这一轮的正文就是最终回答
                yield from self._finish_round(st2, body, msg, stop_event)
                return
            messages.append({"role": "assistant",
                             "content": msg.get("content") or None,
                             "tool_calls": calls})
            # 本轮调用签名：检测与上一轮完全相同的工具+参数
            sigs = []
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                sigs.append((name, json.dumps(args, sort_keys=True)))
                if name in TOOL_DEFS:
                    try:
                        result = TOOL_DEFS[name]["func"](args)
                    except Exception as e:
                        result = "工具执行失败：%s" % e
                else:
                    result = "未知工具：%s" % name
                _tool_log(name, args, result)
                try:
                    _record_usage("tool", module=name)
                except Exception:
                    pass
                if on_tool is not None:
                    try:
                        on_tool(name, args, result)
                    except Exception:
                        pass
                messages.append({"role": "tool",
                                 "tool_call_id": call.get("id", ""),
                                 "content": str(result)})
            # 重复检测：连续 5 轮调用签名完全一致 → 死循环
            if sigs and repeat_track and repeat_track[-1] == sigs:
                repeat_track.append(sigs)
                if len(repeat_track) >= 5:
                    yield ("（检测到重复工具调用，已停止，避免无限循环）", "")
                    return
            else:
                repeat_track = [sigs] if sigs else []

    @staticmethod
    def _assemble_stream_message(obj, acc):
        """把一条 SSE 事件累积进 acc（就地修改）。

        工具调用的 arguments 在流式里是**分片**下发的，必须按 index 拼起来才能
        得到合法 JSON —— 这是流式 function calling 唯一的坑，拼错就会得到
        "Expecting value" 之类的解析失败，表现成"工具调用失败"。
        """
        try:
            if obj.get("usage"):
                acc["usage"] = obj["usage"]
            ch = (obj.get("choices") or [{}])[0]
            delta = ch.get("delta") or ch.get("message") or {}
            if delta.get("content"):
                acc["content"] += delta["content"]
            if delta.get("reasoning_content"):
                acc["reasoning"] += delta["reasoning_content"]
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index")
                if idx is None:
                    idx = len(acc["tool_calls"])
                cur = acc["tool_calls"].setdefault(
                    idx, {"id": "", "type": "function",
                          "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    cur["id"] = tc["id"]
                fn = tc.get("function") or {}
                nm = fn.get("name") or ""
                if nm and not cur["function"]["name"].endswith(nm):
                    # 名字可能整段给（只给一次），也可能分片；两种都拼得对
                    cur["function"]["name"] += nm
                if fn.get("arguments"):
                    cur["function"]["arguments"] += fn["arguments"]
        except Exception:
            pass

    def _record_stream_usage(self, acc, st2):
        """把流式这一轮的 token 用量记进统计（服务端回了 usage 才有）。"""
        try:
            u = acc.get("usage") or {}
            pt = int(u.get("prompt_tokens") or 0)
            ct = int(u.get("completion_tokens") or 0)
            if pt or ct:
                _record_token_usage(
                    module=str(self.rule.get("name", "未知模块") or "未知模块"),
                    prompt=pt, completion=ct)
        except Exception:
            pass

    def _tool_round_stream(self, st2, body, stop_event):
        """流式跑一轮工具决策，返回 (message, 是否整轮为空)。

        message = {"content", "reasoning", "tool_calls", "usage"}
        `是否整轮为空`（既无正文也无工具调用）时，调用方退回非流式再要一次 ——
        少数服务商的流式实现不下发 tool_calls，不能因此把功能弄坏。
        """
        for with_usage in (True, False):
            acc = {"content": "", "reasoning": "", "tool_calls": {}, "usage": None}
            b = dict(body)
            b["stream"] = True
            if with_usage:
                # 请求服务端在流末尾补一条 usage，好把工具轮的 token 也统计上
                b["stream_options"] = {"include_usage": True}
            try:
                self._active_resp = None
                with self._sse_open(st2, b) as resp:
                    self._active_resp = resp
                    for obj in self._sse_events(resp, stop_event):
                        self._assemble_stream_message(obj, acc)
                    break
            except Exception:
                if stop_event is not None and stop_event.is_set():
                    break
                if not with_usage:
                    raise      # 去掉 stream_options 仍然失败，那就是真失败了
            finally:
                self._active_resp = None
        self._record_stream_usage(acc, st2)
        calls = [acc["tool_calls"][k] for k in sorted(acc["tool_calls"])]
        msg = {"content": acc["content"] or None,
               "reasoning": acc["reasoning"],
               "tool_calls": calls,
               "usage": acc["usage"]}
        return msg, (not calls and not acc["content"])

    def _finish_round(self, st2, body, msg, stop_event):
        """最后一轮：把这一轮拿到的正文吐出去。

        以前这里无条件再发一次流式请求，等于**每句话都问模型两遍**：第一次的回答
        直接丢掉，token 和等待时间都翻倍，而且显示出来的可能和第一次不是同一个。
        现在拿到正文就直接用；只有正文为空（比如这一轮只回了思考）才补一次，
        补的时候**去掉 tools**，逼它给一段纯文本，避免又回一轮工具调用导致界面空白。
        """
        text = str(msg.get("content") or "")
        if text:
            yield text, str(msg.get("reasoning") or "")
            return
        b = dict(body)
        b["stream"] = True
        b.pop("tools", None)
        b.pop("stream_options", None)
        yield from self._stream_sse(st2, b, stop_event)

    def pending_request(self):
        return getattr(self, "_pending", None)

    def approve(self):
        """审批当前待处理请求（POST approve 端点）"""
        st = self._source()
        if st.get("type") != "agent":
            return False
        req = self.pending_request()
        approve_url = st.get("approve", "")
        if not req or not approve_url:
            return False
        body = urllib.request.Request(approve_url,
                                      data=json.dumps({"id": req.get("id")}).encode("utf-8"),
                                      method="POST")
        if st.get("api_key_env"):
            body.add_header("Authorization", "Bearer " + os.environ.get(st["api_key_env"], ""))
        try:
            with urllib.request.urlopen(body, timeout=float(st.get("timeout", 5) or 5)) as resp:
                return resp.status < 400
        except Exception:
            return False

    def _fetch_clock(self, st):
        """时钟规则：mode=time 当前时间；countdown 到目标时刻倒计时；
        days 纪念日/倒数日（到 date=YYYY-MM-DD 还剩/已过多少天）；date 今天日期。"""
        mode = st.get("mode", "time")
        if mode == "countdown":
            target = str(st.get("target", "18:00"))
            try:
                hh, mm = [int(x) for x in target.split(":")[:2]]
            except Exception:
                return "目标时间格式错误"
            now = time.localtime()
            secs = (hh * 3600 + mm * 60) - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
            if secs <= 0:
                return "已到点"
            return "还剩 %02d:%02d:%02d" % (secs // 3600, (secs % 3600) // 60, secs % 60)
        if mode == "days":
            from datetime import date as _date
            raw = str(st.get("date", "") or "").strip()
            try:
                parts = [int(x) for x in raw.replace("/", "-").split("-")[:3]]
                target = _date(parts[0], parts[1], parts[2])
            except Exception:
                return "日期格式应为 YYYY-MM-DD"
            delta = (target - _date.today()).days
            if delta > 0:
                return "还有 %d 天" % delta
            if delta < 0:
                return "已过 %d 天" % (-delta)
            return "就是今天"
        if mode == "date":
            return time.strftime("%Y-%m-%d %a")
        return time.strftime("%H:%M:%S")

    def _fetch_disk(self, st):
        """磁盘剩余空间：drive 如 C: ；返回 '剩余 120.5G / 476G'。"""
        drive = str(st.get("drive", "C:") or "C:").strip()
        if not drive.endswith(("\\", "/")):
            drive += "\\"
        try:
            import shutil as _sh
            usage = _sh.disk_usage(drive)
        except Exception as e:
            raise RuntimeError("读取磁盘失败：%s" % e)
        g = 1024.0 ** 3
        return "剩余 %.1fG / %.0fG" % (usage.free / g, usage.total / g)

    def _old_too_long(self, old):
        """较早历史是否需要压缩（按总字符估算 token）。"""
        try:
            total = sum(len(str(m.get("text", ""))) for m in old)
            return total > self._SUMMARY_CHARS or len(old) > 20
        except Exception:
            return len(old) > 20

    def _load_summary(self):
        if self._summary is not None:
            return self._summary or ""
        try:
            data = data_store.read_dict("chat_summary.json")
            self._summary = str(data.get(self.rule.get("id"), "") or "")
        except Exception:
            self._summary = ""
        return self._summary or ""

    def _save_summary(self, summary):
        try:
            self._summary = str(summary or "")

            def _mut(d):
                d[self.rule.get("id")] = self._summary
                return d
            data_store.mutate_json("chat_summary.json", {}, _mut)
        except Exception:
            pass

    def clear_summary(self):
        """清空对话时同步清除摘要。"""
        try:
            self._summary = ""

            def _mut(d):
                d.pop(self.rule.get("id"), None)
                return d
            data_store.mutate_json("chat_summary.json", {}, _mut)
        except Exception:
            pass

    def _summarize(self, st, old_summary, old_messages):
        """把较早的对话压缩成一段摘要（用同一个模型）。"""
        try:
            msgs = [{"role": "system", "content":
                     "你是对话压缩器。把对话压缩成简洁中文摘要，保留：用户身份/偏好、"
                     "话题脉络、关键信息、未完成事项。不要编造，不要逐句复述。"}]
            if old_summary:
                msgs.append({"role": "system",
                             "content": "已有摘要（与新增对话合并）：\n" + old_summary})
            for m in old_messages:
                content = str(m.get("text", "")).strip()
                if content:
                    msgs.append({"role": "assistant" if m.get("role") == "assistant"
                                 else "user", "content": content})
            msgs.append({"role": "user", "content": "请压缩以上对话为一段摘要。"})
            body = {"model": st.get("model", ""), "messages": msgs,
                    "temperature": 0.3, "max_tokens": 400}
            return self._llm_request(st, body)
        except Exception:
            return old_summary or ""

    def _fetch_llm(self, st, history=None, user_text=None, summary=None):
        base = str(st.get("base_url", "")).strip().rstrip("/")
        if not base:
            raise ValueError("缺少 base_url")
        key = st.get("api_key", "") or ""
        if not key and st.get("api_key_env"):
            key = os.environ.get(st["api_key_env"], "") or ""
        messages = [{"role": "system", "content": str(st.get("system_prompt", ""))}]
        if summary:
            messages.append({"role": "system",
                             "content": "之前的对话摘要：\n" + summary})
        # 历史对话（用户/助手轮次），让大模型记住上下文；最多带最近 20 条控制 token
        for m in (history or [])[-20:]:
            role = "assistant" if m.get("role") == "assistant" else "user"
            content = str(m.get("text", "")).strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user",
                         "content": str(user_text if user_text is not None
                                       else st.get("user_prompt", "用一句话回复"))})
        body = {
            "model": st.get("model", ""),
            "messages": messages,
            "temperature": float(st.get("temperature", 0.8) or 0.8),
            "max_tokens": int(st.get("max_tokens", 80) or 80),
        }
        return self._llm_request(st, body)

    def _fetch_llm_task(self, st):
        """llm 单次任务模式：system_prompt + user_prompt 一次调用。

        json=True 时要求模型输出 JSON（response_format json_object），
        结果可再经 transform.jsonpath 取字段。"""
        messages = [{"role": "system",
                     "content": str(st.get("system_prompt", ""))}]
        up = str(st.get("user_prompt", "") or "").strip() or "请回复"
        messages.append({"role": "user", "content": up})
        body = {"model": st.get("model", ""), "messages": messages,
                "temperature": float(st.get("temperature", 0.5) or 0.5),
                "max_tokens": int(st.get("max_tokens", 1024) or 1024)}
        if st.get("json"):
            body["response_format"] = {"type": "json_object"}
        return self._llm_request(st, body)

    def _llm_request_msg(self, st, body):
        """POST /chat/completions，返回完整 message 对象（含 tool_calls）。
        同时从响应 usage 字段记录真实 token 消耗到 token_usage.json。"""
        base = str(st.get("base_url", "")).strip().rstrip("/")
        if not base:
            raise ValueError("缺少 base_url")
        if not base.endswith("/chat/completions"):
            base += "/chat/completions"
        key = st.get("api_key", "") or ""
        if not key and st.get("api_key_env"):
            key = os.environ.get(st["api_key_env"], "") or ""
        req = urllib.request.Request(base, data=json.dumps(body).encode("utf-8"),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        _apply_llm_auth(req, base, key, st.get("api_style"))
        with urllib.request.urlopen(
                req, timeout=_llm_timeout(st, hard=True)) as resp:
            out = json.loads(resp.read(_MAX_RESP_BYTES).decode("utf-8"))
        # 记录真实 token 用量（OpenAI 兼容接口的 usage 字段）
        try:
            usage = out.get("usage") or {}
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            if pt or ct:
                _record_token_usage(module=str(self.rule.get("name", "未知模块") or "未知模块"),
                                    prompt=pt, completion=ct)
        except Exception:
            pass
        return out["choices"][0].get("message") or {}

    def _llm_request(self, st, body):
        msg = self._llm_request_msg(st, body)
        return str(msg.get("content") or "").strip()

    def _transform(self, raw):
        tr = self.rule.get("transform") or {}
        t = tr.get("type", "text")
        if t == "jsonpath":
            try:
                data = raw if isinstance(raw, (dict, list)) else json.loads(raw)
            except Exception:
                return str(raw)
            val = _json_get(data, tr.get("path", ""))
            return str(tr.get("default", "—") if val is None else val)
        if t == "template":
            return str(tr.get("template", "{value}")).replace("{value}", str(raw))
        pat = tr.get("pattern")
        if pat:
            try:
                rep = re.sub(r"\$(\d+)", r"\\g<\1>", str(tr.get("replacement", r"\g<0>")))
                return re.sub(pat, rep, str(raw))
            except Exception:
                return str(raw)
        return str(raw)


def _json_get(data, path):
    """简易 JSON 路径：data.items[0].name 或 data.temp"""
    cur = data
    for part in str(path).split("."):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.+?)\[(\d+)\]$", part)
        if m:
            cur = cur.get(m.group(1)) if isinstance(cur, dict) else None
            try:
                cur = cur[int(m.group(2))]
            except Exception:
                return None
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


_proc_names_cache = {"t": 0.0, "names": set()}


def _running_process_names():
    """返回当前所有进程名（小写，10 秒缓存，避免频繁快照）"""
    now = time.monotonic()
    if _proc_names_cache["names"] and now - _proc_names_cache["t"] < 10:
        return _proc_names_cache["names"]
    names = set()
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32

        class PE(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snap != -1:
            pe = PE()
            pe.dwSize = ctypes.sizeof(PE)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                names.add(pe.szExeFile.lower())
                ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
            kernel32.CloseHandle(snap)
    except Exception:
        pass
    _proc_names_cache["t"] = now
    _proc_names_cache["names"] = names
    return names


_POPUP_REG = []   # 弱引用：参与自动避让的弹出气泡


def _register_popup(w):
    try:
        _POPUP_REG.append(weakref.ref(w))
    except Exception:
        pass


def _visible_popups():
    out = []
    dead = []
    for r in _POPUP_REG:
        w = r()
        if w is None:
            dead.append(r)
        elif w.isVisible():
            # 淡出中的气泡保持原位，不参与每帧重排，避免消失过程闪烁
            if not getattr(w, "_fading_out", False):
                out.append(w)
    for d in dead:
        try:
            _POPUP_REG.remove(d)
        except Exception:
            pass
    return out


def _reflow_popups(pet):
    """让所有可见弹出气泡自动避让：纵向堆叠、互不重合，并跟随桌宠移动。"""
    try:
        vis = _visible_popups()
        if not vis:
            return
        pc = pet.mapToGlobal(QPoint(pet.width() // 2, pet.height() // 2))
        scr = QApplication.screenAt(pc) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        menu = getattr(pet, "radial_menu", None)
        menu_open = bool(menu and getattr(menu, "is_visible_state", False)
                         and menu._sector_outer() > 0)
        main_bubble = getattr(pet, "status_bubble", None)
        main_visible = bool(main_bubble is not None and main_bubble.isVisible()
                            and main_bubble.height() > 0)
        margin = 6
        if main_visible:
            # 主气泡栏可见：堆在气泡栏外侧，绝不盖住气泡栏内容
            # （菜单展开时主气泡栏已在菜单上方，弹出气泡随之堆到主气泡栏上方）
            mb_top = main_bubble.y()
            mb_bottom = main_bubble.y() + main_bubble.height()
            pet_top = pc.y() - pet.height() // 2
            if mb_top < pet_top:
                anchor = mb_top - margin          # 气泡栏在桌宠上方 → 堆在气泡栏上缘之上
                upward = True
            else:
                anchor = mb_bottom + margin       # 气泡栏在桌宠下方 → 堆在气泡栏下缘之下
                upward = False
        elif menu_open:
            # 只有菜单展开且主气泡栏不可见：堆在菜单盘上方，更靠外
            anchor = pc.y() - menu._sector_outer() - margin
            upward = True
        else:
            # 独立弹出：堆在桌宠上方
            anchor = pc.y() - pet.height() // 2 - margin
            upward = True
        y = anchor
        for w in vis:
            h = w.height()
            if upward:
                y -= h
            x = pc.x() - w.width() // 2
            x = max(g.left(), min(int(x), g.right() - w.width()))
            if y < g.top():
                y = g.top()
            if y + h > g.bottom():
                y = g.bottom() - h
            if (w.x(), w.y()) != (int(x), int(y)):
                w.move(int(x), int(y))
            if upward:
                y -= margin
            else:
                y += h + margin
    except Exception:
        pass


def _u(v):
    """桌宠旁小气泡的尺寸按界面基准倍率放大（这些窗口不参与统一放大）。"""
    try:
        from widgets import kit
        return kit.ui(v)
    except Exception:
        return v


def _uf(pt, bold=False):
    """桌宠旁小气泡的字号按界面基准倍率放大。"""
    f = QFont("Microsoft YaHei")
    try:
        from widgets import kit
        f.setPointSizeF(pt * kit.UI_BASE)
    except Exception:
        f.setPointSize(pt)
    f.setBold(bold)
    return f


class MoodBubble(QWidget):
    """待机情绪气泡：工作时随机冒出鼓励语句，带淡入淡出"""

    _phrases = [
        "加油！代码会变好的～",
        "写得不错嘛，继续保持！",
        "累了就喝口水吧 ☕",
        "这个 bug 难不倒你的！",
        "你已经很棒了 ✨",
        "摸摸头，稳住别慌～",
        "专注的人最有魅力！",
        "再坚持一下，马上就通了！",
        "新的一天，元气满满！",
        "别忘了休息，眼睛很重要哦",
        "代码有 bug 不怕，找出来就是胜利！",
        "深呼吸，慢慢来～",
        "你已经比昨天更厉害了！",
        "思路卡住了？先走两步再说",
        "写完这段就去泡杯茶吧",
        "稳住，能赢！",
        "今天也要闪闪发光 ✨",
        "遇到难题说明你在变强",
        "休息五分钟，回来更高效",
        "别急，答案正在路上",
    ]

    def __init__(self, pet_widget):
        super().__init__(pet_widget)
        self.pet = pet_widget
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._font = _uf(9)
        self._text = ""
        self._fade_timer = QTimer(self)
        self._fade_timer.setTimerType(Qt.PreciseTimer)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_active = False
        self._fade_from = 0.0
        self._fade_to = 1.0
        self._fade_dur = 0.25
        self._fade_t0 = 0.0
        self._fade_done = None
        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.timeout.connect(self.hide_animated)
        self.hide()
        self._fading_out = False
        _register_popup(self)

    def show_phrase(self, text=None):
        self._fading_out = False
        self._text = text or random.choice(self._phrases)
        fm = QFontMetrics(self._font)
        w = min(fm.horizontalAdvance(self._text) + _u(24), _u(320))
        h = fm.height() + _u(12)
        self.resize(w, h)
        self._fade_active = False
        self._fade_timer.stop()
        self.show()
        self.raise_()
        _reflow_popups(self.pet)
        self._fade(0.0, 1.0, 0.22)
        self._life_timer.start(4200)

    def hide_animated(self):
        if not self.isVisible():
            return
        self._fading_out = True
        self._fade(self.windowOpacity(), 0.0, 0.18, on_done=self.hide)

    def hide(self):
        self._fade_active = False
        self._fade_timer.stop()
        super().hide()
        self.setWindowOpacity(1.0)   # 先隐藏再复位透明度，避免消失瞬间亮闪
        _reflow_popups(self.pet)

    def _fade(self, start, end, dur, on_done=None):
        self._fade_active = True
        self._fade_from = float(start)
        self._fade_to = float(end)
        self._fade_dur = float(dur)
        self._fade_t0 = time.monotonic()
        self._fade_done = on_done
        self.setWindowOpacity(self._fade_from)
        if not self._fade_timer.isActive():
            self._fade_timer.start(16)

    def _fade_tick(self):
        if not self._fade_active:
            self._fade_timer.stop()
            return
        t = min(1.0, (time.monotonic() - self._fade_t0) / self._fade_dur)
        eased = 1.0 - (1.0 - t) ** 3
        self.setWindowOpacity(self._fade_from + (self._fade_to - self._fade_from) * eased)
        if t >= 1.0:
            self._fade_active = False
            self._fade_timer.stop()
            if self._fade_done:
                done = self._fade_done
                self._fade_done = None
                done()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(46, 50, 66, 215))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), _u(8), _u(8))
        p.setFont(self._font)
        p.setPen(QColor(240, 244, 252))
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()


class PopupBubble(QWidget):
    """弹出式气泡：独立浮出在桌宠附近（类似情绪气泡）。

    用于规则的"自动弹出"显示类型；鼠标悬停时不消失，离开后按设定时长重新计时。
    """

    def __init__(self, pet_widget):
        super().__init__(pet_widget)
        self.pet = pet_widget
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._font = _uf(9)
        self._text = ""
        self._duration_ms = 3000
        self._fade_timer = QTimer(self)
        self._fade_timer.setTimerType(Qt.PreciseTimer)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_active = False
        self._fade_from = 0.0
        self._fade_to = 1.0
        self._fade_dur = 0.18
        self._fade_t0 = 0.0
        self._fade_done = None
        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.timeout.connect(self.hide_animated)
        self.hide()
        self._fading_out = False
        _register_popup(self)

    def show_text(self, text, duration_ms=3000):
        self._fading_out = False
        self._text = str(text)
        self._duration_ms = max(500, int(duration_ms or 3000))
        fm = QFontMetrics(self._font)
        w = min(fm.horizontalAdvance(self._text) + _u(24), _u(320))
        h = fm.height() + _u(12)
        self.resize(w, h)
        self._fade_active = False
        self._fade_timer.stop()
        self.show()
        self.raise_()
        _reflow_popups(self.pet)
        self._fade(0.0, 1.0, 0.18)
        self._life_timer.start(self._duration_ms)

    def enterEvent(self, e):
        # 鼠标停在气泡上：不消失
        self._life_timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        # 鼠标离开：按设定时长重新计时
        self._life_timer.start(self._duration_ms)
        super().leaveEvent(e)

    def hide_animated(self):
        if not self.isVisible():
            return
        self._fading_out = True
        self._fade(self.windowOpacity(), 0.0, 0.15, on_done=self.hide)

    def hide(self):
        self._fade_active = False
        self._fade_timer.stop()
        self._life_timer.stop()
        super().hide()
        self.setWindowOpacity(1.0)
        _reflow_popups(self.pet)

    def _fade(self, start, end, dur, on_done=None):
        self._fade_active = True
        self._fade_from = float(start)
        self._fade_to = float(end)
        self._fade_dur = float(dur)
        self._fade_t0 = time.monotonic()
        self._fade_done = on_done
        self.setWindowOpacity(self._fade_from)
        if not self._fade_timer.isActive():
            self._fade_timer.start(16)

    def _fade_tick(self):
        if not self._fade_active:
            self._fade_timer.stop()
            return
        t = min(1.0, (time.monotonic() - self._fade_t0) / self._fade_dur)
        eased = 1.0 - (1.0 - t) ** 3
        self.setWindowOpacity(self._fade_from + (self._fade_to - self._fade_from) * eased)
        if t >= 1.0:
            self._fade_active = False
            self._fade_timer.stop()
            if self._fade_done:
                done = self._fade_done
                self._fade_done = None
                done()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(46, 50, 66, 225))
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), _u(8), _u(8))
        p.setFont(self._font)
        p.setPen(QColor(240, 244, 252))
        p.drawText(self.rect().adjusted(_u(6), 0, -_u(6), 0), Qt.AlignCenter, self._text)
        p.end()


class ErrorPopup(QWidget):
    """错误提示弹窗：显示在桌宠附近，数秒后自动消失（非阻塞）。"""

    def __init__(self, pet_widget):
        super().__init__(pet_widget)
        self.pet = pet_widget
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._font = _uf(8)
        self._title = ""
        self._msg = ""
        self._fade_timer = QTimer(self)
        self._fade_timer.setTimerType(Qt.PreciseTimer)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_active = False
        self._fade_from = 0.0
        self._fade_to = 1.0
        self._fade_dur = 0.2
        self._fade_t0 = 0.0
        self._fade_done = None
        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.timeout.connect(self.hide_animated)
        self.hide()
        self._fading_out = False
        _register_popup(self)

    def show_error(self, title, msg, duration_ms=5000):
        self._fading_out = False
        self._title = str(title or "错误")
        self._msg = str(msg or "")
        fm = QFontMetrics(self._font)
        w = min(max(fm.horizontalAdvance(self._msg) + _u(28), _u(180)), _u(360))
        h = fm.height() * 2 + _u(20)
        self.resize(w, h)
        self._fade_active = False
        self._fade_timer.stop()
        self.show()
        self.raise_()
        _reflow_popups(self.pet)
        self._fade(0.0, 1.0, 0.18)
        self._life_timer.start(max(1500, int(duration_ms)))

    def hide_animated(self):
        if not self.isVisible():
            return
        self._fading_out = True
        self._fade(self.windowOpacity(), 0.0, 0.15, on_done=self.hide)

    def hide(self):
        self._fade_active = False
        self._fade_timer.stop()
        self._life_timer.stop()
        super().hide()
        self.setWindowOpacity(1.0)
        _reflow_popups(self.pet)

    def _fade(self, start, end, dur, on_done=None):
        self._fade_active = True
        self._fade_from = float(start)
        self._fade_to = float(end)
        self._fade_dur = float(dur)
        self._fade_t0 = time.monotonic()
        self._fade_done = on_done
        self.setWindowOpacity(self._fade_from)
        if not self._fade_timer.isActive():
            self._fade_timer.start(16)

    def _fade_tick(self):
        if not self._fade_active:
            self._fade_timer.stop()
            return
        t = min(1.0, (time.monotonic() - self._fade_t0) / self._fade_dur)
        eased = 1.0 - (1.0 - t) ** 3
        self.setWindowOpacity(self._fade_from + (self._fade_to - self._fade_from) * eased)
        if t >= 1.0:
            self._fade_active = False
            self._fade_timer.stop()
            if self._fade_done:
                done = self._fade_done
                self._fade_done = None
                done()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(56, 32, 36, 235))
        p.setPen(QPen(QColor(240, 120, 110, 200), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), _u(7), _u(7))
        p.setFont(_uf(8, True))
        p.setPen(QColor(255, 150, 140))
        p.drawText(QRect(_u(8), _u(5), self.width() - _u(16), _u(16)), Qt.AlignLeft | Qt.AlignVCenter, self._title)
        p.setFont(self._font)
        p.setPen(QColor(245, 230, 228))
        p.drawText(QRect(_u(8), _u(20), self.width() - _u(16), self.height() - _u(24)),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self._msg)
        p.end()



# ==================== 气泡 UI（拆分至 bubble_ui.py） ====================
from bubble_ui import (StatusBubble, ChatPanel, _BubbleTip, _TrashIconButton,
                       _ArrowButton, _FoldButton, _ThumbStrip, _ChatBtnFilter,
                       _ChatInput, _ChatHistory, _chat_html, _chat_html_inline,
                       _chat_history_path, _chat_summary_path, _split_links,
                       _w_is_alive, _style_module_widget, _IMG_RE, _LINK_RE)

