# -*- coding: utf-8 -*-
"""模块契约、配置归一化与后台刷新调度。

这个模块故意不依赖 Qt：气泡、status_monitor 和测试都能使用同一套
模块定义；UI 层只根据 ModuleSpec 渲染，不再解释散落的 rule 字段。
"""
from dataclasses import dataclass
import hashlib
import time


# 版本号唯一来源：改这一行即可。version_info.txt 与安装包版本由 build.bat
# 依此生成，不必再逐个文件改。
# 编号规则：1.0 之前用两位小版本（0.9.03 -> 0.9.04 -> …），留足迭代空间；
# 1.0 留给正式版。
APP_VERSION = "0.9.42"

_CHAT_PANEL_UIS = {"chat"}
_TITLE_LESS_WIDGET_UIS = {"canvas", "tokenmeter", "stats", "perler"}
# 纯本地计算、没有任何 I/O 的源类型：算一次也就几微秒，不值得为它起线程，
# 刷新间隔也不必和网络模块一样压到 5 秒起（时钟压到 5 秒就会慢半拍跳分钟）。
_LOCAL_SOURCE_TYPES = {"clock", "static"}
_LOCAL_MIN_INTERVAL = 1.0
_MIN_INTERVAL = 5.0
_MAX_BACKOFF = 300.0
_BACKOFF_BASE = 5.0


@dataclass(frozen=True)
class ModuleView:
    """气泡行的渲染意图。kind: text / action / widget / chat。"""
    kind: str
    ui: str = ""
    action: str = ""
    title_visible: bool = True


@dataclass(frozen=True)
class ModuleSpec:
    """一个模块在运行期的稳定契约。rule 仍保留原始字段供提供器使用。"""
    id: str
    name: str
    rule: dict
    source: dict
    interval: float
    enabled: bool
    embed: bool
    popup: bool
    view: ModuleView

    @property
    def key(self):
        return "rule:" + self.id

    @property
    def is_chat(self):
        return self.view.kind == "chat"

    @property
    def is_action(self):
        return self.view.kind == "action"

    @property
    def is_local(self):
        """纯本地计算、没有 I/O：可以直接在主线程算，不必起线程。"""
        return str(self.source.get("type") or "") in _LOCAL_SOURCE_TYPES


def _stable_id(name):
    digest = hashlib.sha1(("oi-module:" + name).encode("utf-8")).hexdigest()
    return "m" + digest[:11]


def normalize_rule(rule, used_ids=None):
    """返回一份防御性副本；补齐 source 对象和稳定 ID，兼容旧配置。"""
    if not isinstance(rule, dict):
        rule = {"name": str(rule or "模块")}
    out = dict(rule)
    name = str(out.get("name") or out.get("id") or "模块")
    out["name"] = name
    source = out.get("source")
    out["source"] = dict(source) if isinstance(source, dict) else {}

    rid = str(out.get("id") or "").strip()
    if used_ids is None:
        return out
    if not rid:
        rid = _stable_id(name)
        base = rid
        n = 2
        while rid in used_ids:
            rid = "%s-%d" % (base, n)
            n += 1
    n = 2
    base = rid
    while rid in used_ids:
        rid = "%s-%d" % (base, n)
        n += 1
    used_ids.add(rid)
    out["id"] = rid
    return out


def normalize_rules(rules):
    """归一化整张模块表；禁用模块也获得稳定 ID。"""
    used = set()
    return [normalize_rule(rule, used)
            for rule in (rules or []) if isinstance(rule, dict)]


def module_spec(rule):
    """从单条规则构造运行期契约；输入通常已经过 normalize_rule。"""
    safe = normalize_rule(rule)
    source = safe.get("source", {})
    stype = str(source.get("type") or "")
    floor = _LOCAL_MIN_INTERVAL if stype in _LOCAL_SOURCE_TYPES else _MIN_INTERVAL
    try:
        interval = max(floor, float(safe.get("interval", 60) or 60))
    except (TypeError, ValueError):
        interval = 60.0
    name = str(safe.get("name") or safe.get("id") or "模块")
    rid = str(safe.get("id") or _stable_id(name))
    ui = str(source.get("ui") or "")
    action = str(source.get("action") or "")
    chat = bool(safe.get("chat")) and source.get("type") == "llm" \
        and source.get("mode") != "task"
    if chat:
        view = ModuleView("chat", ui, action, title_visible=False)
    elif ui == "webchat" or action == "webchat":
        view = ModuleView("action", ui or "webchat", "webchat", True)
    elif ui:
        title_visible = ui not in _TITLE_LESS_WIDGET_UIS
        view = ModuleView("widget", ui, action, title_visible)
    else:
        view = ModuleView("text")
    return ModuleSpec(
        id=rid,
        name=name,
        rule=safe,
        source=source,
        interval=interval,
        enabled=bool(safe.get("enabled", True)),
        embed=bool(safe.get("embed", True)),
        popup=bool(safe.get("popup", False)),
        view=view,
    )


def is_chat_rule(rule):
    return module_spec(rule).is_chat


def is_action_rule(rule):
    return module_spec(rule).is_action


class ModuleScheduler:
    """后台模块刷新的状态机：并发去重、失败计数和指数退避。

    线程创建仍由气泡的 Qt signal 桥管理；这里只决定“什么时候该跑”和
    “失败后什么时候再跑”，方便独立测试。
    """

    def __init__(self, max_backoff=_MAX_BACKOFF):
        self.pending = set()
        self.failures = {}
        self._ready_at = {}
        self._max_backoff = float(max_backoff)

    def reset(self, name=None):
        if name is None:
            self.pending.clear()
            self.failures.clear()
            self._ready_at.clear()
            return
        self.pending.discard(name)
        self.failures.pop(name, None)
        self._ready_at.pop(name, None)

    @staticmethod
    def _blocked(provider):
        return bool(getattr(provider, "_hung", False))

    def should_run(self, provider, last_at, now=None):
        now = time.monotonic() if now is None else now
        spec = getattr(provider, "spec", None)
        if spec is None:
            spec = module_spec(getattr(provider, "rule", {}))
        if spec.is_chat or self._blocked(provider):
            return False
        name = provider.name
        if name in self.pending or now < self._ready_at.get(name, 0.0):
            return False
        if last_at is None:
            return True
        return (now - float(last_at)) >= spec.interval

    def started(self, name, ok=True):
        if ok:
            self.pending.add(name)
        return ok

    def failed_to_start(self, name):
        self.pending.discard(name)

    def finish(self, name, failed, now=None):
        now = time.monotonic() if now is None else now
        self.pending.discard(name)
        if not failed:
            self.failures.pop(name, None)
            self._ready_at.pop(name, None)
            return 0.0
        failures = int(self.failures.get(name, 0)) + 1
        self.failures[name] = failures
        # 指数退避 5/10/20/40/80/160/300…：exponent 封在 6（2**6=64→320）后被
        # _max_backoff=300 截断，真正达到文档所述的 300 秒上限（此前封在 5，
        # 最高只到 160 秒，与文档不符）。
        delay = min(self._max_backoff,
                    _BACKOFF_BASE * (2 ** min(failures - 1, 6)))
        self._ready_at[name] = now + delay
        return delay

    def failure_count(self, name):
        return int(self.failures.get(name, 0))

    def retry_in(self, name, now=None):
        now = time.monotonic() if now is None else now
        return max(0, int(round(self._ready_at.get(name, 0.0) - now)))
