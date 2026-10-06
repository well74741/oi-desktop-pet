# -*- coding: utf-8 -*-
"""模块模板库 —— 「添加 / 编辑模块」窗口和 AI 工具共用的**唯一一份**目录。

以前有三份互不一致的清单：RuleDialog._presets（28 个）、TemplateGalleryDialog.GALLERY
（24 个）、status_monitor.MODULE_TEMPLATES（22 个），改一处漏两处。现在都从这里来：
- 编辑窗口：按 GROUPS 分组列出 ui=True 的模板，表单只显示该模板的参数；
- AI 工具：status_monitor.MODULE_TEMPLATES = ai_templates()，模板名 / 参数名保持不变；
- 编辑已有模块：match(rule) 按内容认出是哪个模板、读出参数。认不出就是「自定义」，
  编辑时只改表单里那几个字段（write 原地修改），规则里其余字段一个不丢。

本文件不依赖 Qt，可单独测试。
"""
import copy
import datetime
import re
from urllib.parse import quote, unquote

# ---- AI 助手：内置模块和模板共用的人设 / 工具清单 ----
AI_SYSTEM_PROMPT = (
    "你是桌宠的 AI 助手。你拥有这些能力（看到相关需求必须调用"
    "对应工具来真正执行，禁止只口头说'已设置/已删除'而不调用"
    "工具）：查时间/开网页/设置定时提醒；管理待办（添加、查看、"
    "删除）；管理气泡模块（列出、新建、删除、启停、排序）；管理"
    "径向菜单按钮（列出、添加、删除、排序、编辑）；控制桌宠本体"
    "（隐藏/显示/移动）；调整桌宠设置（大小/透明度等，先 pet_info"
    "查看当前值再 pet_setting 修改）；画画（用户说'画布/无限画布/绘图'"
    "用 canvas_draw，说'拼豆/像素画/像素点'用 perler_draw，"
    "两者都用中文描述要画的内容）。用户需要提取/整理为固定"
    "格式（如 JSON、列表）时，直接输出对应结构，不要附加解释。"
    "其余问题直接简短回答。")

AI_TOOLS = ["get_time", "add_todo", "list_todos", "open_url",
            "remind", "delete_todo", "list_modules",
            "add_module", "remove_module", "enable_module",
            "move_module", "list_buttons", "add_button",
            "remove_button", "move_button", "edit_button",
            "pet_control", "pet_setting", "pet_info",
            "list_components", "list_templates",
            "add_module_from_template", "canvas_draw", "perler_draw"]

AI_GREETING = ("你好！我是你的 AI 助手，可以：管理待办/模块/按钮、"
               "控制桌宠、调整桌宠设置、查时间、开网页、设提醒。")

# 编辑窗口左侧的分组顺序（AI 放最上面）
GROUPS = ("AI", "常用", "时间", "信息", "工具", "创作", "高级")

# 「刷新」下拉的档位（秒, 文案）；规则里是别的值时窗口会临时补一项
INTERVALS = ((1, "每秒"), (60, "每分钟"), (300, "每 5 分钟"), (600, "每 10 分钟"),
             (1800, "每 30 分钟"), (3600, "每小时"), (86400, "每天"))

# 编辑窗口里不显示的簿记字段（id 由程序管理、name 在表单里、builtin 是内置标记）
HIDDEN_KEYS = ("id", "name", "builtin")


def interval_text(sec):
    for v, t in INTERVALS:
        if v == sec:
            return t
    return "每 %d 秒" % sec


# ---------------------------------------------------------------- 路径读写
def _get(rule, path):
    cur = rule
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set(rule, path, value):
    """value 为空字符串 = 删掉这个键（让运行时用它自己的默认值）。"""
    parts = path.split(".")
    cur = rule
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    if not str(value).strip():
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value


def _src(rule):
    s = rule.get("source") if isinstance(rule, dict) else None
    return s if isinstance(s, dict) else {}


def _stype(rule):
    return str(_src(rule).get("type", "") or "").lower()


def _ui(rule):
    return str(_src(rule).get("ui", "") or "")


# ---------------------------------------------------------------- 定义
class Param:
    """模板参数。kind：line 单行 / text 多行短文本 / code 代码 / choice 下拉。
    path：在规则里的位置（如 source.target）；None 表示由模板自己的 read/write 处理。
    check：(正则, 不符合时的提示)，保存前校验；form=False 只给 AI 用、窗口不显示。"""

    def __init__(self, key, label, default="", hint="", kind="line", path=None,
                 choices=None, check=None, form=True):
        self.key = key
        self.label = label
        self.default = default
        self.hint = hint
        self.kind = kind
        self.path = path
        self.choices = choices or ()
        self.check = check
        self.form = form

    def error(self, value):
        if not self.check:
            return ""
        rule, msg = self.check
        ok = rule(value or "") if callable(rule) else re.match(rule, value or "")
        return "" if ok else msg


class Template:
    """refresh：表单显示「刷新」间隔；popup：可选「定时弹出」；
    preview：auto 改了就预览 / manual 点按钮才跑（脚本、大模型）/ None 不预览（组件）；
    generic：兜底模板，匹配时排在具体模板之后。"""

    def __init__(self, key, group, title, desc, base, params=(), sig=None, ai_desc="",
                 refresh=False, popup=False, preview="auto", ui=True, ai=True,
                 generic=False, read=None, write=None):
        self.key = key
        self.group = group
        self.title = title
        self.desc = desc
        self.base = base
        self.params = list(params)
        self.sig = sig
        self.ai_desc = ai_desc or desc
        self.refresh = refresh
        self.popup = popup
        self.preview = preview
        self.ui = ui
        self.ai = ai
        self.generic = generic
        self._read = read
        self._write = write

    def defaults(self):
        return {p.key: p.default for p in self.params}

    def read(self, rule):
        """从已有规则读出参数；读不出（结构对不上）返回 None。"""
        if self._read is not None:
            return self._read(rule)
        out = {}
        for p in self.params:
            if p.path:
                v = _get(rule, p.path)
                out[p.key] = "" if v is None else str(v)
        return out

    def write(self, rule, params):
        """把参数原地写进规则：只动这个模板管的字段。"""
        # 不 strip：统计单位 " 项" 前面的空格是有意的；空白判断在 _set 里做
        vals = {p.key: str(params.get(p.key, p.default) or "") for p in self.params}
        if self._write is not None:
            self._write(rule, vals)
            return
        for p in self.params:
            if p.path:
                _set(rule, p.path, vals[p.key])

    def build(self, name, params=None):
        rule = {"name": name, "enabled": True, "embed": True}
        rule.update(copy.deepcopy(self.base))
        p = self.defaults()
        p.update({k: v for k, v in (params or {}).items() if v is not None})
        self.write(rule, p)
        return rule

    def matches(self, rule):
        return self.sig is not None and bool(self.sig(rule))


def _widget(key, group, title, desc, ui=None, interval=3600, aliases=()):
    """挂 widgets/ 下交互组件的模板：code 写死，没有参数（属安全模板）。"""
    uis = (ui or key,) + tuple(aliases)
    return Template(
        key, group, title, desc,
        {"interval": interval, "fallback": "未启动",
         "source": {"type": "script", "lang": "python", "ui": uis[0],
                    "code": "result = %r" % title, "timeout": 5}},
        sig=lambda r, _u=uis: _ui(r) in _u, preview=None)


# ---- 几个参数和字段不是一一对应的模板 ----
_WTTR = re.compile(r"^(https?://wttr\.in/)([^?]*)(\?.*)?$")


def _weather_read(rule):
    m = _WTTR.match(str(_src(rule).get("url", "")))
    return {"city": unquote(m.group(2)).strip("/")} if m else None


def _weather_write(rule, p):
    src = rule.setdefault("source", {})
    m = _WTTR.match(str(src.get("url", "")))
    query = (m.group(3) if m else None) or "?format=%c+%t"
    src["url"] = "https://wttr.in/%s%s" % (quote(p["city"].strip()), query)
    src["plain_text"] = True       # 不带它 wttr.in 会按浏览器返回整张网页


_ERAPI = re.compile(r"^https?://open\.er-api\.com/v6/latest/([A-Za-z]+)")


def _jsonpath_of(rule):
    """取规则的 JSON 取值路径；transform 是正则 / 模板等别的写法时返回 None。"""
    tr = rule.get("transform")
    if tr in (None, ""):
        return ""
    if isinstance(tr, str):
        return tr
    if isinstance(tr, dict) and tr.get("type") == "jsonpath" \
            and set(tr) <= {"type", "path"}:
        return str(tr.get("path", ""))
    return None


def _set_jsonpath(rule, path):
    if path:
        rule["transform"] = {"type": "jsonpath", "path": path}
    else:
        rule.pop("transform", None)


def _exchange_read(rule):
    m = _ERAPI.match(str(_src(rule).get("url", "")))
    path = _jsonpath_of(rule) or ""
    if not m or not path.startswith("rates."):
        return None
    return {"base": m.group(1).upper(), "quote": path[6:].upper()}


def _exchange_write(rule, p):
    base = (p["base"].strip() or "USD").upper()
    rule.setdefault("source", {})["url"] = "https://open.er-api.com/v6/latest/%s" % base
    _set_jsonpath(rule, "rates.%s" % (p["quote"].strip() or "CNY").upper())


def _http_read(rule):
    path = _jsonpath_of(rule)
    if path is None:
        return None
    return {"url": str(_src(rule).get("url", "")), "path": path}


def _http_write(rule, p):
    rule.setdefault("source", {})["url"] = p["url"].strip()
    _set_jsonpath(rule, p["path"].strip())


_URL = (r"^https?://\S+$", "接口地址要以 http:// 或 https:// 开头")
_HHMM = (r"^\d{1,2}:\d{2}$", "时刻写成 HH:MM，比如 18:00")
def _real_date(v):
    try:
        y, m, d = (int(x) for x in v.split("-"))
        datetime.date(y, m, d)
        return True
    except (ValueError, TypeError):
        return False


_DATE = (_real_date, "日期写成 YYYY-MM-DD，比如 2027-01-01")

_STATS_CHOICES = (("todos", "待办数"), ("modules", "模块数"), ("buttons", "快捷按钮数"),
                  ("chat", "对话次数"), ("tool", "工具调用次数"), ("token", "Token 消耗"))


def _llm(rule):
    return _stype(rule) == "llm"


TEMPLATES = [
    # ================= AI =================
    Template(
        "ai", "AI", "AI 助手",
        "在气泡里和大模型对话，还能帮你管理待办、模块、按钮和桌宠设置。"
        "模型在「AI 设置」里统一配置。",
        {"interval": 60, "chat": True, "fallback": "AI 不可用",
         "source": {"type": "llm", "base_url": "", "model": "", "api_key": "",
                    "system_prompt": AI_SYSTEM_PROMPT, "user_prompt": "你好",
                    "temperature": 0.8, "max_tokens": 8192, "tools": list(AI_TOOLS)}},
        [Param("greeting", "开场白", AI_GREETING, "打开对话时它先说的话", kind="text",
               path="greeting")],
        sig=lambda r: _llm(r) and bool(r.get("chat")),
        ai_desc="AI 助手：气泡里对话，共用『AI 设置』的模型，能操作桌宠（参数 greeting 可选）",
        preview=None),
    Template(
        "ai_line", "AI", "AI 每日一句",
        "定时让大模型说一句话：问候、冷知识、鼓励……适合设成定时弹出。",
        {"interval": 3600, "fallback": "AI 不可用",
         "source": {"type": "llm", "user_prompt": "开始", "temperature": 0.9,
                    "max_tokens": 200}},
        [Param("prompt", "让它说什么",
               "你是一只桌面宠物，每次用一句话说一件有趣的事（问候/冷知识/有意义的话均可）",
               kind="text", path="source.system_prompt")],
        sig=lambda r: _llm(r) and not r.get("chat"),
        ai_desc="AI 每日一句：定时让大模型说一句话（参数 prompt 描述要说什么）",
        refresh=True, popup=True, preview="manual"),
    Template(
        "webchat", "AI", "聚合AI",
        "用系统自带的 Edge / Chrome 打开各家网页版大模型，侧边栏一键切换站点。",
        {"interval": 3600, "chat": True, "fallback": "组件未加载",
         "source": {"type": "script", "lang": "python", "ui": "webchat",
                    "url": "https://chat.deepseek.com",
                    "code": "result = '聚合AI'", "timeout": 5}},
        sig=lambda r: _ui(r) == "webchat", preview=None),
    # 原始大模型配置：只给 AI 工具用（窗口里的「AI 助手」共用 AI 设置，不用填 Key）
    Template(
        "llm", "AI", "大模型聊天",
        "大模型聊天（OpenAI 兼容接口；参数 base_url/model/api_key/system_prompt）",
        {"interval": 60, "chat": True, "fallback": "AI 不可用",
         "greeting": "你好！我是大模型助手，想聊什么？",
         "source": {"type": "llm", "user_prompt": "你好", "temperature": 0.8,
                    "max_tokens": 8192}},
        [Param("base_url", "接口地址", path="source.base_url"),
         Param("model", "模型", path="source.model"),
         Param("api_key", "API Key", path="source.api_key"),
         Param("system_prompt", "人设", "你是桌宠助手，用简短中文回复",
               path="source.system_prompt")],
        ui=False),

    # ================= 常用 =================
    Template(
        "weather", "常用", "天气",
        "显示本地天气（来自 wttr.in），不填城市也能用。",
        {"interval": 1800, "fallback": "天气获取失败",
         "source": {"type": "http", "url": "https://wttr.in/?format=%c+%t",
                    "timeout": 5, "plain_text": True}},
        [Param("city", "城市", "", "留空 = 按网络自动定位")],
        sig=lambda r: _stype(r) == "http" and "wttr.in" in str(_src(r).get("url", "")),
        ai_desc="天气（wttr.in，参数 city 可选，留空自动定位）",
        refresh=True, popup=True, read=_weather_read, write=_weather_write),
    Template(
        "countdown", "常用", "倒计时",
        "倒数到每天的某个时刻，到点后显示你写的文字。",
        {"interval": 1, "fallback": "已到点",
         "source": {"type": "clock", "mode": "countdown"}},
        [Param("target", "目标时刻", "18:00", "HH:MM", path="source.target", check=_HHMM),
         Param("done_text", "到点后显示", "", "留空 = 已到点", path="source.done_text")],
        sig=lambda r: _stype(r) == "clock" and _src(r).get("mode") == "countdown",
        ai_desc="倒计时到目标时刻（参数 target 如 18:00；done_text 到点后显示的文字，可选）"),
    _widget("notes", "常用", "便签", "在气泡里随手记几行字。"),
    _widget("todo", "常用", "待办清单", "在气泡里记待办事项，AI 助手也能帮你增删。"),

    # ================= 时间 =================
    Template(
        "clock", "时间", "时钟", "显示当前时间，每秒刷新。",
        {"interval": 1, "fallback": "—", "source": {"type": "clock", "mode": "time"}},
        sig=lambda r: _stype(r) == "clock" and _src(r).get("mode", "time") in ("time", "date"),
        ai_desc="当前时间（每秒刷新）"),
    Template(
        "anniversary", "时间", "纪念日", "距离某一天还有 / 已经过了多少天。",
        {"interval": 3600, "fallback": "日期无效", "source": {"type": "clock", "mode": "days"}},
        [Param("date", "日期", "2027-01-01", "YYYY-MM-DD", path="source.date", check=_DATE)],
        sig=lambda r: _stype(r) == "clock" and _src(r).get("mode") == "days",
        ai_desc="纪念日/倒数日（参数 date=YYYY-MM-DD，显示已过或还剩多少天）",
        popup=True),

    # ================= 信息 =================
    Template(
        "hitokoto", "信息", "每日一言", "随机来一句话（来自 hitokoto.cn），换换心情。",
        {"interval": 1800, "fallback": "—",
         "source": {"type": "http", "url": "https://v1.hitokoto.cn/?encode=text",
                    "plain_text": True, "timeout": 5}},
        sig=lambda r: _stype(r) == "http" and "hitokoto.cn" in str(_src(r).get("url", "")),
        refresh=True, popup=True),
    Template(
        "exchange", "信息", "汇率", "实时汇率（来自 open.er-api.com）。",
        {"interval": 3600, "fallback": "汇率获取失败",
         "source": {"type": "http", "url": "", "timeout": 8}},
        [Param("base", "基准币种", "USD", "如 USD"), Param("quote", "目标币种", "CNY", "如 CNY")],
        sig=lambda r: _stype(r) == "http" and "open.er-api.com" in str(_src(r).get("url", "")),
        ai_desc="汇率（参数 base 基准币种、quote 目标币种，如 USD→CNY）",
        refresh=True, popup=True, read=_exchange_read, write=_exchange_write),
    Template(
        "disk", "信息", "磁盘空间", "显示某个盘还剩多少空间。",
        {"interval": 600, "fallback": "读取失败", "source": {"type": "disk"}},
        [Param("drive", "盘符", "C:", "如 C:", path="source.drive")],
        sig=lambda r: _stype(r) == "disk",
        ai_desc="磁盘剩余空间（参数 drive，如 C:）", refresh=True, popup=True),
    Template(
        "stats", "信息", "统计卡片", "统计待办、模块、对话次数等数量。",
        {"interval": 60, "fallback": "",
         "source": {"type": "script", "lang": "python", "ui": "stats", "code": "result = ''"}},
        [Param("metric", "统计项", "todos", kind="choice", choices=_STATS_CHOICES,
               path="source.stats.metric"),
         Param("http_url", "接口地址", path="source.stats.http_url", form=False),
         Param("http_path", "字段路径", path="source.stats.http_path", form=False),
         Param("unit", "单位", "", "如 项", path="source.stats.unit")],
        sig=lambda r: _ui(r) == "stats",
        ai_desc="统计表组件（参数 metric=todos/modules/buttons/chat/tool/token；"
                "或用 http_url+http_path 接任意本地/网络 JSON 接口）",
        refresh=True, preview=None),
    _widget("tokenmeter", "信息", "Token 用量", "统计大模型消耗了多少 Token。", interval=300),

    # ================= 工具 =================
    _widget("counter", "工具", "计数器", "点一下 +1，可清零。"),
    _widget("calc", "工具", "计算器", "在气泡里快速算数。"),
    _widget("tomato", "工具", "番茄钟", "专注计时 + 休息提醒。", aliases=("pomodoro",)),
    _widget("health", "工具", "久坐提醒", "定时提醒起身活动。"),
    _widget("launcher", "工具", "快捷启动", "在气泡里点按钮开程序 / 网页。"),

    # ================= 创作 =================
    _widget("canvas", "创作", "无限画布", "手绘，也可以让 AI 作画。"),
    _widget("perler", "创作", "拼豆", "像素画格子，也可以让 AI 画。"),

    # ================= 高级 =================
    Template(
        "static", "高级", "固定文本", "显示一句固定的话。",
        {"interval": 3600, "fallback": "", "source": {"type": "static"}},
        [Param("text", "文本", "摸鱼中", path="source.text")],
        sig=lambda r: _stype(r) == "static", ai_desc="固定文本（参数 text）",
        popup=True, generic=True),
    Template(
        "json_api", "高级", "网页接口",
        "定时抓一个网址的内容。返回 JSON 时可以填字段路径，只取其中一项。",
        {"interval": 300, "fallback": "获取失败",
         "source": {"type": "http", "url": "", "timeout": 8}},
        [Param("url", "接口地址", "", "https://...", check=_URL),
         Param("path", "字段路径", "", "如 data.items.0.title（不是 JSON 就留空）")],
        sig=lambda r: _stype(r) == "http",
        ai_desc="任意网页/JSON 接口取字段（参数 url、path 如 data.items.0.title，可留空）",
        refresh=True, popup=True, generic=True, read=_http_read, write=_http_write),
    Template(
        "script", "高级", "自定义脚本",
        "用 Python 写一段脚本，把要显示的内容赋给 result；state 字典可以跨刷新记状态。",
        {"interval": 60, "fallback": "脚本出错",
         "source": {"type": "script", "lang": "python", "timeout": 15}},
        [Param("code", "代码", "result = 'hello'", kind="code", path="source.code",
               check=(r"(?s).*\S", "代码不能为空"))],
        sig=lambda r: _stype(r) == "script" and not _ui(r),
        ai_desc="自定义 Python 脚本（参数 code；state 字典可跨刷新记状态）",
        refresh=True, popup=True, preview="manual", generic=True),
    Template(
        "custom", "高级", "自定义（JSON）",
        "直接写完整的模块配置 JSON，可以用上所有字段。格式见右上角「？」里的开发指南。",
        {"interval": 3600, "fallback": "获取失败",
         "source": {"type": "static", "text": "你好"}},
        preview="manual", ai=False),
]

# 编辑内置模块（CPU / 内存 / 情绪……）时用：它们的脚本是程序内部的，不该当成
# 「自定义脚本」把代码摊给用户看，只给名称 / 刷新 / 显示方式，JSON 收进高级。
BUILTIN = Template("builtin", "", "内置模块", "桌宠自带的模块。", {},
                   refresh=True, popup=True, preview="manual", ui=False, ai=False)

BY_KEY = {t.key: t for t in TEMPLATES}
CUSTOM = BY_KEY["custom"]


def ui_groups():
    """[(分组名, [模板...])]，按 GROUPS 顺序，只含窗口里显示的模板。"""
    return [(g, [t for t in TEMPLATES if t.ui and t.group == g]) for g in GROUPS]


def match(rule):
    """认出一条已有规则属于哪个模板，返回 (模板, 参数)。认不出 → (CUSTOM, {})。"""
    if not isinstance(rule, dict):
        return CUSTOM, {}
    for generic in (False, True):
        for t in TEMPLATES:
            if t.generic != generic or not t.ui or not t.matches(rule):
                continue
            params = t.read(rule)
            if params is None:
                continue
            if rule.get("builtin") and t.key in ("script", "custom"):
                break
            return t, params
    if rule.get("builtin"):
        return BUILTIN, {}
    return CUSTOM, {}


def ai_templates():
    """给 AI 工具用的视图：{模板名: {desc, params, build}}（status_monitor.MODULE_TEMPLATES）。"""
    return {t.key: {"desc": t.ai_desc, "params": [p.key for p in t.params],
                    "build": t.build}
            for t in TEMPLATES if t.ai}


def safe_script_keys():
    """AI 可直接创建的脚本类模板：代码由模板写死、不是参数（便签 / 待办 / 统计……）。
    「自定义脚本」的 code 是参数，不在其中 —— 仍受「允许 AI 创建可执行模块」闸门管。"""
    return {t.key for t in TEMPLATES
            if t.ai and str((t.base.get("source") or {}).get("type")) == "script"
            and not any(p.path == "source.code" for p in t.params)}
