# -*- coding: utf-8 -*-
"""模块模板回归测试：保证「添加模块」画廊里每个模板都真的能用。

覆盖每个 MODULE_TEMPLATES 条目：
1. build() 能用默认参数产出规则；
2. 规则通过 _normalize_rule（source.type 属于已知类型，字段齐全）；
3. module_core.module_spec 能解析出渲染契约；
4. 挂组件的模板（source.ui）能真实加载并渲染该组件（不是只存在文件）；
5. 非网络类模板能实际取到一个值（不抛异常、不返回 fallback 的"未启动"）。

网络类（http/llm）不在离线环境实跑，只校验配置结构。

用法：python -u test_templates.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication   # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import module_core            # noqa: E402
import status_monitor as sm   # noqa: E402
from widgets import load_module_widget   # noqa: E402

# 需要联网才能取到值的类型：只校验结构，不实跑
_NET_TYPES = {"http", "llm", "agent"}
# 这些模板必须填参数才有意义，给一组测试用的默认值
_TEST_PARAMS = {
    "countdown": {"target": "18:00"},
    "anniversary": {"date": "2027-01-01"},
    "static": {"text": "测试文本"},
    "script": {"code": "result = 'ok'"},
    "stats": {"metric": "todos"},
    "disk": {"drive": "C:"},
    "exchange": {"base": "USD", "quote": "CNY"},
    "json_api": {"url": "https://example.com/a.json", "path": "a.b"},
    "llm": {"base_url": "https://api.example.com/v1", "model": "m",
            "api_key": "sk-testkey1234567", "system_prompt": "你好"},
}

_passed = 0
_failed = []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print("PASS %s" % name)
    else:
        _failed.append("%s %s" % (name, detail))
        print("FAIL %s  %s" % (name, detail))


def main():
    templates = sm.MODULE_TEMPLATES
    check("模板库非空", len(templates) > 0, "共 %d 个" % len(templates))

    for key in sorted(templates):
        tpl = templates[key]
        params = dict(_TEST_PARAMS.get(key, {}))
        for p in tpl.get("params", []):
            params.setdefault(p, "")

        # 1) build
        try:
            rule = tpl["build"]("测试_%s" % key, params)
        except Exception as e:
            check("%s · build" % key, False, repr(e))
            continue
        check("%s · build" % key, isinstance(rule, dict) and bool(rule.get("name")))

        # 2) 归一化（source.type 必须是已知类型）
        norm, err = sm._normalize_rule(dict(rule))
        check("%s · 规则合法" % key, not err, err)
        if err:
            continue
        stype = str((norm.get("source") or {}).get("type", ""))

        # 3) 渲染契约
        try:
            spec = module_core.module_spec(norm)
            ok_spec = spec.view.kind in ("text", "action", "widget", "chat")
        except Exception as e:
            ok_spec, spec = False, None
            check("%s · 契约解析" % key, False, repr(e))
        else:
            check("%s · 契约解析" % key, ok_spec,
                  "" if ok_spec else str(spec.view))

        # 4) 组件可加载并渲染
        ui = str((norm.get("source") or {}).get("ui", "") or "")
        if ui:
            w, werr = load_module_widget(ui)
            check("%s · 组件[%s]加载" % (key, ui), w is not None, werr)
            if w is not None:
                try:
                    w.set_rule(norm)
                    w.render(getattr(w, "state", {}), "")
                    w.resize(200, max(30, w.current_height() or 40))
                    w.repaint()
                    _app.processEvents()
                    rendered = True
                    rerr = ""
                except Exception as e:
                    rendered, rerr = False, repr(e)
                check("%s · 组件[%s]渲染" % (key, ui), rendered, rerr)
                w.deleteLater()

        # 5) 实际取数（跳过联网类）
        if stype in _NET_TYPES:
            src = norm.get("source") or {}
            if stype == "http":
                check("%s · 接口地址合法" % key,
                      str(src.get("url", "")).startswith(("http://", "https://")),
                      str(src.get("url", "")))
            else:
                check("%s · 结构完整" % key, isinstance(src, dict))
            continue
        try:
            prov = sm.RuleProvider(norm)
            val = prov.collect()
            err2 = getattr(prov, "_last_error", "")
            ok = val is not None and not err2
        except Exception as e:
            val, ok, err2 = None, False, repr(e)
        check("%s · 取数" % key, ok, "err=%s val=%r" % (err2, val))

    print("\n==== %d passed, %d failed ====" % (_passed, len(_failed)))
    for f in _failed:
        print("  ! " + f)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
