# -*- coding: utf-8 -*-
"""复现 + 体检：AI 助手的工具对话。

对应用户反馈："我用 AI 对话帮我写个模块，回复显示调用工具失败。"（在另一台电脑上）

做法：起一个**本地假大模型服务器**（OpenAI 兼容 /chat/completions），可以控制
"生成要花多久"和"返回什么工具调用"，然后走**真实的** RuleProvider.chat_stream_tools
代码路径。所有用户数据写到临时沙箱（断言过 get_config_path 在沙箱里才动手），
不碰真实设置、不联网、不需要 API Key。

用法：python _check_ai_tools.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 沙箱：先隔离，确认隔离成功才继续 ----------
import data_store                                    # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_ai_tools_")
data_store.DATA_DIR = SAND
import pet_gravity                                   # noqa: E402

_cfg = pet_gravity.get_config_path()
if os.path.abspath(_cfg).lower() != os.path.join(SAND, "pet_settings.json").lower():
    print("隔离失败，配置路径落在 %s，已中止" % _cfg)
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

from PyQt5.QtWidgets import QApplication             # noqa: E402

app = QApplication([])
import status_monitor as S                           # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


# ---------- 假大模型服务器 ----------
SCRIPT = {"delay": 0.0, "calls": [], "log": []}   # 每次请求按顺序弹出一个"剧本"


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        SCRIPT["log"].append("stream" if body.get("stream") else "non-stream")
        step = SCRIPT["calls"].pop(0) if SCRIPT["calls"] else {"text": "好的"}
        delay = step.get("delay", SCRIPT["delay"])
        if body.get("stream"):
            # 流式：真实服务商就是这么发的 —— 正文按字吐，
            # **工具调用的 arguments 按分片吐**（这里故意切碎，验证拼接）
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def emit(obj):
                self.wfile.write(("data: %s\n\n"
                                  % json.dumps(obj)).encode("utf-8"))
                self.wfile.flush()

            time.sleep(delay)
            if step.get("tool"):
                args = json.dumps(step.get("args", {}), ensure_ascii=False)
                mid = max(1, len(args) // 2)
                for k, piece in enumerate((args[:mid], args[mid:])):
                    emit({"choices": [{"delta": {"tool_calls": [
                        {"index": 0, "id": "call_1" if k == 0 else None,
                         "type": "function",
                         "function": {
                             "name": step["tool"] if k == 0 else None,
                             "arguments": piece}}]}}]})
                    time.sleep(0.01)
            else:
                for ch in step.get("text", "好的"):
                    emit({"choices": [{"delta": {"content": ch}}]})
                    time.sleep(0.005)
            if (body.get("stream_options") or {}).get("include_usage"):
                emit({"choices": [], "usage": {"prompt_tokens": 3,
                                               "completion_tokens": 4}})
            self.wfile.write(b"data: [DONE]\n\n")
            return
        # 非流式（工具轮）：模型要把整段生成完才返回第一个字节
        time.sleep(delay)
        msg = {"role": "assistant", "content": step.get("text")}
        if step.get("tool"):
            msg["content"] = None
            msg["tool_calls"] = [{
                "id": "call_1", "type": "function",
                "function": {"name": step["tool"],
                             "arguments": json.dumps(step.get("args", {}),
                                                     ensure_ascii=False)}}]
        out = json.dumps({"choices": [{"message": msg}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out.encode("utf-8"))
        except Exception:
            pass       # 客户端已超时断开


srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/v1" % srv.server_address[1]


def make_provider(**src_over):
    """按**出厂默认**的 AI 助手规则造一个 provider，只把地址指向假服务器。"""
    rule = None
    for r in pet_gravity.load_settings().get("status_rules", []) or []:
        if r.get("id") == "builtin_ai":
            rule = json.loads(json.dumps(r))
    if rule is None:     # 设置里没有就从出厂默认里拿
        import re  # noqa
        rule = json.loads(json.dumps(next(
            r for r in pet_gravity._BUILTIN_RULES if r.get("id") == "builtin_ai")))
    rule["source"]["base_url"] = BASE
    rule["source"]["api_key"] = "sk-fake"
    rule["source"].update(src_over)
    return S.RuleProvider(rule), rule


def run_chat(provider, rule, text):
    """照 bubble_ui 里的做法跑一遍，返回 (界面会显示的文字, 工具步骤列表)。"""
    steps = []
    tool_names = rule["source"].get("tools") or []
    reply, got, err = "", False, None
    try:
        for chunk, _r in provider.chat_stream_tools(
                text, [], tool_names, stop_event=threading.Event(),
                on_tool=lambda tn, ta, tr: steps.append((tn, str(tr)))):
            got, reply = True, chunk
    except Exception as e:
        err = e
    if not got:
        reply = "（工具对话失败：%s）" % err     # 与 bubble_ui.py 同一句
    return reply, steps


STATIC_RULE = {"name": "拼图测试", "interval": 600, "enabled": True,
               "embed": True, "fallback": "—",
               "source": {"type": "static", "text": "拼接成功"}}

MODULE_RULE = {"name": "随机名言", "interval": 600, "enabled": True, "embed": True,
               "fallback": "—",
               "source": {"type": "script", "lang": "python",
                          "code": "import random\nresult = random.choice(['a','b'])",
                          "timeout": 5}}

try:
    print("假服务器:", BASE, "  沙箱:", SAND)
    defaults_src = next(r for r in pet_gravity._BUILTIN_RULES
                        if r.get("id") == "builtin_ai")["source"]
    print("出厂默认 AI 规则 timeout 字段:", defaults_src.get("timeout", "<没有，落到代码默认 10 秒>"))

    # ===== 一、超时与请求次数 =====
    print("\n--- 一、超时 ---")
    p, rule = make_provider()
    SCRIPT["calls"] = [{"text": "你好呀"}]
    SCRIPT["delay"] = 0
    SCRIPT["log"] = []
    r, _ = run_chat(p, rule, "你好")
    print("   一句'你好'实际发了 %d 次请求：%s" % (len(SCRIPT["log"]), SCRIPT["log"]))
    check("【发现】不调工具的普通回答：只该请求一次（现在拿到答案后又重问一遍）",
          len(SCRIPT["log"]) == 1,
          "第一次的回答被丢弃，界面显示的是第二次的 %r" % r)

    # 出厂默认不写 timeout，所以这里正好在验证"默认值够不够用"
    SCRIPT["calls"] = [{"tool": "add_module",
                        "args": {"rule": STATIC_RULE}, "delay": 12.0},
                       {"text": "已经建好了。"}]
    SCRIPT["log"] = []
    t0 = time.monotonic()
    r, steps = run_chat(p, rule, "帮我写个随机名言模块")
    dt = time.monotonic() - t0
    print("   模型生成要 12 秒时，界面显示：%r（%.1fs 后）" % (r, dt))
    print("   这一轮发的请求：%s" % SCRIPT["log"])
    check("【已修】生成要 12 秒也能走完（工具轮走流式，不再卡在 10 秒超时）",
          "工具对话失败" not in r, "回复=%r" % r)
    check("【已修】工具轮的请求也是流式的",
          SCRIPT["log"] and SCRIPT["log"][0] == "stream", str(SCRIPT["log"]))
    check("工具调用参数分片下发时能拼成完整 JSON（落地的模块名对得上）",
          any(x.get("name") == "拼图测试"
              for x in pet_gravity.load_settings().get("status_rules", []))
          and "工具执行失败" not in (steps[0][1] if steps else ""),
          "工具步骤=%s" % (steps[0][0] if steps else None))

    # ===== 二、安全闸门：默认不允许 AI 建脚本模块 =====
    print("\n--- 二、安全闸门 ---")
    p, rule = make_provider(timeout=60)
    st = pet_gravity.load_settings()
    print("   出厂 allow_ai_exec_modules =", st.get("allow_ai_exec_modules"))
    SCRIPT["calls"] = [{"tool": "add_module", "args": {"rule": MODULE_RULE}},
                       {"text": "抱歉，没能创建。"}]
    r, steps = run_chat(p, rule, "帮我写个随机名言模块")
    tool_res = steps[0][1] if steps else ""
    print("   工具返回给模型的是：%s" % tool_res[:90])
    check("【复现】默认设置下 add_module(script) 被闸门拒绝",
          "为安全起见" in tool_res)
    has = any(r.get("name") == "随机名言"
              for r in pet_gravity.load_settings().get("status_rules", []))
    check("被拒绝时确实没有落地任何模块", not has)

    # ===== 三、名称匹配：不能误删 =====
    print("\n--- 三、名称匹配（误删防护）---")
    import status_monitor as _SM
    mods = [{"name": "聚合AI"}, {"name": "AI助手"}, {"name": "AI本地·Ollama"},
            {"name": "天气"}, {"name": "无限画布"}]
    hit, cands = _SM._match_one(mods, "AI")
    check("搜「AI」有多个候选时不猜，返回候选列表让用户确认",
          hit is None and len(cands) == 3, "候选=%s" % cands)
    hit, cands = _SM._match_one(mods, "AI助手")
    check("精确名字仍然精确命中", bool(hit) and hit["name"] == "AI助手", str(hit))
    hit, cands = _SM._match_one(mods, "画布")
    check("唯一的部分匹配（画布 → 无限画布）照常命中",
          bool(hit) and hit["name"] == "无限画布", str(hit))
    hit, cands = _SM._match_one(mods, "不存在的东西")
    check("完全找不到时返回空", hit is None and not cands)

    # ===== 四、闸门要提前告知 =====
    print("\n--- 四、闸门提前告知 ---")
    d_off = _SM.tool_description("add_module")
    _st = pet_gravity.load_settings()
    _st["allow_ai_exec_modules"] = True
    pet_gravity.save_settings(_st)
    d_on = _SM.tool_description("add_module")
    check("闸门关着时，add_module 的说明里就写明不能用 script/file",
          "不允许" in d_off and "static" in d_off and "template" in d_off)
    check("闸门打开后不再多那段提示", d_off != d_on and "不允许" not in d_on)
    _st["allow_ai_exec_modules"] = False
    pet_gravity.save_settings(_st)


    print("\n--- 三、走模板 ---")
    SCRIPT["calls"] = [{"tool": "add_module_from_template",
                        "args": {"template": "countdown", "name": "下班倒计时",
                                 "params": {"target": "18:00"}}},
                       {"text": "已添加。"}]
    r, steps = run_chat(p, rule, "加个下班倒计时")
    tool_res = steps[0][1] if steps else ""
    check("模板建模块（非脚本）在默认设置下可以成功",
          "已添加模块" in tool_res, tool_res[:60])
finally:
    srv.shutdown()
    shutil.rmtree(SAND, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：" + "、".join(BAD))
sys.exit(1 if BAD else 0)
