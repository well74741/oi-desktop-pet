# -*- coding: utf-8 -*-
"""模块添加 / 编辑窗口（module_editor）+ 统一模板库（module_templates）自动测试。

运行：python test_module_editor.py（离屏；用户数据进临时沙箱）。

重点：
1) 打开任何已有模块、不改直接保存 → 规则不变（内置 12 个 + 旧版窗口的 28 个预设）；
2) 编辑只动表单管的字段，开场白 / max_chars / tools 等不丢（旧窗口会丢开场白）；
3) 模板目录只有一份：AI 工具的模板名一个不少，安全闸门照旧；
4) 顺手修掉的旧 bug：汇率 / JSON 接口永远"获取失败"、天气模板拿到整张网页。
"""
import ast
import copy
import os
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data_store                                    # noqa: E402

SAND = tempfile.mkdtemp(prefix="oi_editor_test_")
data_store.DATA_DIR = SAND
import pet_gravity as G                              # noqa: E402

if os.path.abspath(G.get_config_path()).lower() != \
        os.path.join(SAND, "pet_settings.json").lower():
    print("隔离失败，配置路径落在 %s，已中止" % G.get_config_path())
    shutil.rmtree(SAND, ignore_errors=True)
    sys.exit(2)

from PyQt5.QtCore import Qt                          # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog    # noqa: E402

app = QApplication([])
import module_editor as ME                           # noqa: E402
import module_templates as mt                        # noqa: E402
import status_monitor as sm                          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def pick(d, key):
    for i in range(d.tpl_list.count()):
        if d.tpl_list.item(i).data(Qt.UserRole) == key:
            d.tpl_list.setCurrentRow(i)
            return
    raise KeyError(key)


def norm(rule):
    """保存时会把"嵌入 / 弹出"补成显式值，比较前按运行时默认值补齐。"""
    r = copy.deepcopy(rule)
    r.setdefault("embed", True)
    r.setdefault("popup", False)
    r.setdefault("enabled", True)
    return r


# 旧版 RuleDialog 的 28 个预设：当作"用户手里已有的模块"来测兼容
OLD_PRESETS = []
try:
    _old = subprocess.run(["git", "show", "4846b07:pet_gravity.py"], cwd=HERE,
                          capture_output=True).stdout.decode("utf-8")
    _i = _old.index("    _presets = [")
    _j = _old.index("\n    ]\n", _i)
    OLD_PRESETS = [dict(body, id="p%d" % n, name=name) for n, (_t, _l, name, body) in
                   enumerate(ast.literal_eval(_old[_i + 15:_j + 6].replace("\n    ", "\n")))]
except Exception as e:
    print("（取不到旧预设，跳过这一组：%s）" % e)

ST = {"ai_profile": {"provider": "DeepSeek", "model": "deepseek-chat",
                     "base_url": "https://api.deepseek.com/v1", "api_key": "sk-test"}}
try:
    # ===== 一、模板目录 =====
    print("--- 一、模板目录只有一份 ---")
    groups = [g for g, items in mt.ui_groups() if items]
    check("分组顺序：AI 在最上面", groups[0] == "AI" and groups == list(mt.GROUPS), str(groups))
    ui_keys = [t.key for _g, items in mt.ui_groups() for t in items]
    check("每个窗口模板只出现一次", len(ui_keys) == len(set(ui_keys)))
    old_ai_keys = {"anniversary", "calc", "canvas", "clock", "countdown", "counter", "disk",
                   "exchange", "health", "hitokoto", "json_api", "launcher", "llm", "notes",
                   "perler", "script", "static", "stats", "todo", "tokenmeter", "tomato",
                   "weather"}
    check("AI 工具原有的 22 个模板名一个不少",
          old_ai_keys <= set(sm.MODULE_TEMPLATES),
          str(old_ai_keys - set(sm.MODULE_TEMPLATES)))
    check("AI 工具模板参数名不变（汇率 base/quote、倒计时 target、JSON 接口 url/path）",
          sm.MODULE_TEMPLATES["exchange"]["params"] == ["base", "quote"]
          and sm.MODULE_TEMPLATES["countdown"]["params"][0] == "target"
          and sm.MODULE_TEMPLATES["json_api"]["params"] == ["url", "path"])
    safe = sm._SAFE_SCRIPT_TEMPLATES
    check("安全模板里没有「自定义脚本」等能执行任意代码的",
          not ({"script", "llm", "ai", "custom", "json_api"} & safe), str(sorted(safe)))
    check("组件类模板仍算安全（便签 / 待办 / 统计）", {"notes", "todo", "stats"} <= safe)
    sm._pet_api.load_settings = G.load_settings
    sm._pet_api.save_settings = G.save_settings
    sm._pet_api.reload_rules = lambda: None
    st0 = G.load_settings()
    st0["allow_ai_exec_modules"] = False
    G.save_settings(st0)
    res = sm._tool_add_module_from_template(
        {"template": "script", "name": "坏脚本", "params": {"code": "result = 1"}})
    check("闸门关着时 AI 仍不能用「自定义脚本」模板建模块", "为安全起见" in res, res[:40])
    check("……而且没写进设置",
          not any(r.get("name") == "坏脚本" for r in G.load_settings()["status_rules"]))
    check("内置 AI 助手和「AI 助手」模板共用同一份人设和工具清单",
          G._BUILTIN_RULES[-1]["source"]["tools"] == mt.AI_TOOLS
          and mt.BY_KEY["ai"].build("x")["source"]["tools"] == mt.AI_TOOLS
          and mt.BY_KEY["ai"].build("x")["source"]["system_prompt"] == mt.AI_SYSTEM_PROMPT)

    # ===== 二、顺手修的旧 bug =====
    print("\n--- 二、旧模板的 bug ---")
    data = {"rates": {"CNY": 7.1}, "a": {"b": 42}}
    for k, p, want in (("exchange", {}, "7.1"),
                       ("json_api", {"url": "https://example.com/x", "path": "a.b"}, "42")):
        rp = sm.RuleProvider(mt.BY_KEY[k].build("t", p))
        try:
            got = rp._transform(data)
        except Exception as e:
            got = repr(e)
        check("%s 模板能取到字段（以前永远显示失败文字）" % k, got == want, got)
    legacy = {"name": "旧汇率", "transform": "rates.CNY",
              "source": {"type": "http", "url": "https://open.er-api.com/v6/latest/USD"}}
    try:
        got = sm.RuleProvider(legacy)._transform(data)
    except Exception as e:
        got = repr(e)
    check("用户以前按旧模板建的（transform 存成字符串）也恢复正常", got == "7.1", got)
    check("天气模板带 plain_text（否则 wttr.in 回一整张网页）",
          mt.BY_KEY["weather"].build("t")["source"].get("plain_text") is True)
    rp = sm.RuleProvider(mt.BY_KEY["countdown"].build("t", {"target": "00:00",
                                                             "done_text": "下班啦"}))
    check("倒计时到点后显示自定义文字", rp._fetch_clock(rp.rule["source"]) == "下班啦")

    # ===== 三、认出已有模块 =====
    print("\n--- 三、按内容认出模板 ---")
    want = {"CPU": "builtin", "情绪": "builtin", "聚合AI": "webchat", "天气": "weather",
            "番茄钟": "tomato", "AI助手": "ai", "无限画布": "canvas"}
    got = {r["name"]: mt.match(r)[0].key for r in G._BUILTIN_RULES if r["name"] in want}
    check("内置模块认对了（CPU 这类内部脚本不当成自定义脚本摊给用户）", got == want, str(got))
    rt_bad = [t.key for t in mt.TEMPLATES if t.ui and t.key != "custom"
              and mt.match(t.build("x"))[0] is not t]
    check("每个模板建出来的模块都能认回自己", not rt_bad, str(rt_bad))
    ip = {"name": "公网IP", "source": {"type": "http", "url": "https://example.com/ip"},
          "transform": {"type": "text", "pattern": "^(.*)$", "replacement": "IP：$1"}}
    check("表单表示不了的（正则取值）→ 按自定义打开", mt.match(ip)[0] is mt.CUSTOM)
    check("城市里的中文能来回", mt.match(mt.BY_KEY["weather"].build("t", {"city": "北京"}))[1]
          == {"city": "北京"})

    # ===== 四、不改直接保存 = 规则不变 =====
    print("\n--- 四、打开再保存不改坏 ---")
    changed = []
    for r in list(G._BUILTIN_RULES) + OLD_PRESETS:
        d = ME.ModuleEditor(None, r, ST)
        out, err = d.compose()
        if err or norm(out) != norm(r):
            changed.append("%s(%s)" % (r["name"], err or mt.match(r)[0].key))
        d.deleteLater()
    check("内置 %d 个 + 旧预设 %d 个，打开再保存都原样" % (len(G._BUILTIN_RULES), len(OLD_PRESETS)),
          not changed, "、".join(changed))

    # ===== 五、编辑只改表单管的字段 =====
    print("\n--- 五、编辑不丢字段 ---")
    ai = copy.deepcopy(G._BUILTIN_RULES[-1])
    ai["max_chars"] = 99
    ai["source"]["api_key"] = "sk-mine-1234567890"
    d = ME.ModuleEditor(None, ai, ST)
    check("AI 助手认成「AI 助手」模板，表单里有开场白", d._tpl.key == "ai" and "greeting" in d._fields)
    check("模块自带 Key 时显示「本模块单独配置」", "单独配置" in d.ai_lab.text(), d.ai_lab.text())
    d._fields["greeting"].setPlainText("嗨")
    out, err = d.compose()
    check("改开场白 → 存下了", not err and out["greeting"] == "嗨")
    check("……工具清单 / 人设 / 自带 Key / max_chars / 内置标记都还在",
          out["source"]["tools"] == ai["source"]["tools"]
          and out["source"]["system_prompt"] == ai["source"]["system_prompt"]
          and out["source"]["api_key"] == "sk-mine-1234567890"
          and out["max_chars"] == 99 and out["builtin"] == "ai" and out["id"] == ai["id"])

    cpu = G._BUILTIN_RULES[0]
    d = ME.ModuleEditor(None, cpu, ST)
    check("内置 CPU：不显示代码框", not d._fields and d.h1.text() == "内置模块")
    check("2 秒刷新不在档位里 → 补一项「每 2 秒」", d.iv_combo.currentText() == "每 2 秒")
    d.iv_combo.setCurrentIndex(d.iv_combo.findData(60))
    out, _ = d.compose()
    check("改刷新间隔 → 存下了，代码没动", out["interval"] == 60
          and out["source"]["code"] == cpu["source"]["code"])

    # ===== 六、添加 =====
    print("\n--- 六、添加 ---")
    d = ME.ModuleEditor(None, None, ST)
    check("添加时默认选中第一个：AI 助手", d._tpl.key == "ai", d._tpl.key)
    check("AI 设置已配好 → 显示所用模型", "deepseek-chat" in d.ai_lab.text(), d.ai_lab.text())
    pick(d, "weather")
    check("换模板时名称跟着换（还没手动改过）", d.name_edit.text() == "天气")
    d.name_edit.setText("家里天气")
    d.name_edit.textEdited.emit("家里天气")
    pick(d, "hitokoto")
    check("手动改过名称 → 换模板不覆盖", d.name_edit.text() == "家里天气")
    pick(d, "weather")
    d._fields["city"].setText("北京")
    d._accept()
    r = d.rule
    check("添加天气：城市写进地址、带新 id、启用", r and "wttr.in/%E5%8C%97" in r["source"]["url"]
          and r["id"].startswith("r") and r["enabled"] and r["name"] == "家里天气",
          str(r and r["source"]))
    check("新建的模块能被运行时接受", not sm._normalize_rule(copy.deepcopy(r))[1])

    d = ME.ModuleEditor(None, None, ST)
    pick(d, "static")
    check("固定文本默认不显示刷新（不需要）", not d.iv_combo.isVisibleTo(d))
    d.pop_btn.setChecked(True)
    check("选「定时弹出」→ 出现弹出间隔和停留时间",
          d.iv_combo.isVisibleTo(d) and d.iv_label.text() == "弹出间隔"
          and d.dur_spin.isVisibleTo(d))
    d.dur_spin.setValue(8)
    out, _ = d.compose()
    check("……存成弹出、不嵌入", out["popup"] is True and out["embed"] is False
          and out["popup_duration"] == 8)

    # ===== 七、校验 / 预览 =====
    print("\n--- 七、校验与预览 ---")
    d = ME.ModuleEditor(None, None, ST)
    pick(d, "countdown")
    d._fields["target"].setText("六点")
    d._accept()
    check("时刻写错 → 不保存、给提示", d.rule is None and "HH:MM" in d.hint.text(), d.hint.text())
    pick(d, "json_api")
    d._accept()
    check("网页接口不填地址 → 不保存", d.rule is None and "http" in d.hint.text())
    pick(d, "static")
    d._fields["text"].setText("摸鱼中")
    d._run_preview()
    check("预览：固定文本马上显示出来", d.pv_value.text() == "摸鱼中", d.pv_value.text())
    pick(d, "anniversary")
    d._fields["date"].setText("2027-13-45")
    d._run_preview()
    check("预览：不存在的日期（13 月 45 日）→ 提示错误", "YYYY" in d.pv_status.text(),
          d.pv_status.text())
    pick(d, "script")
    check("脚本不会改一个字就自动跑（要点「试一下」）",
          d._tpl.preview == "manual" and d.pv_run.isVisibleTo(d))
    for name, k in (("便签", "notes"), ("AI 助手", "ai")):
        pick(d, k)
        check("%s 没有预览框（组件 / 对话不需要）" % name, not d.preview.isVisibleTo(d))

    others = [{"id": "x1", "name": "CPU"}]
    d = ME.ModuleEditor(None, None, ST, others=others)
    pick(d, "clock")
    d.name_edit.setText("CPU")
    d._accept()
    check("和已有模块重名 → 不保存", d.rule is None and "CPU" in d.hint.text())

    # ===== 八、高级：JSON =====
    print("\n--- 八、高级 JSON ---")
    d = ME.ModuleEditor(None, None, ST)
    pick(d, "static")
    d.adv_btn.setChecked(True)
    d._fields["text"].setText("表单写的")
    check("表单改动实时同步到 JSON", "表单写的" in d.json_edit.toPlainText())
    d.json_edit.setPlainText('{"source": {"type": "static", "text": "JSON 写的"}}')
    check("手动改 JSON → 表单锁住（名称除外）",
          not d._fields["text"].isEnabled() and d.name_edit.isEnabled()
          and d.json_row.isVisibleTo(d))
    out, _ = d.compose()
    check("……保存以 JSON 为准", out["source"]["text"] == "JSON 写的")
    d._undo_json()
    out, _ = d.compose()
    check("撤销 JSON 修改 → 回到表单的值、表单解锁",
          out["source"]["text"] == "表单写的" and d._fields["text"].isEnabled())
    d.json_edit.setPlainText("{坏的")
    d._accept()
    check("JSON 写坏 → 不保存、给提示", d.rule is None and "JSON" in d.hint.text())
    d = ME.ModuleEditor(None, ip, ST)
    check("自定义：JSON 直接展开、没有「高级」开关",
          d.json_edit.isVisibleTo(d) and not d.adv_btn.isVisibleTo(d))

    # ===== 九、尺寸 =====
    print("\n--- 九、尺寸 ---")
    a = ME.ModuleEditor(None, None, ST)
    e = ME.ModuleEditor(None, mt.BY_KEY["countdown"].build("下班", {}), ST)
    for w in (a, e):
        w.show()
        app.processEvents()
    check("编辑时不显示模板列表，窗口更窄", e.tpl_list is None and e.width() < a.width(),
          "%d vs %d" % (e.width(), a.width()))
    check("编辑窗口高度贴着内容（不留大片空白）",
          e.height() <= e.layout().totalSizeHint().height() + 2,
          "%d vs %d" % (e.height(), e.layout().totalSizeHint().height()))
    check("说明文字没被截断", e.desc.height() >= e.desc.heightForWidth(e.desc.width()))
    for w in (a, e):
        w.close()

    # ===== 十、设置窗入口 =====
    print("\n--- 十、设置窗里添加 / 编辑 ---")
    sd = G.SettingsDialog(G.load_settings())
    n0 = len(sd.temp_status_rules)
    sd._add_status_rule()
    dlg = G._RULE_DIALOG_REF[0]()
    check("「添加模块」打开新窗口", isinstance(dlg, ME.ModuleEditor) and dlg.tpl_list is not None)
    pick(dlg, "clock")
    dlg._accept()
    app.processEvents()
    check("添加后进了列表", len(sd.temp_status_rules) == n0 + 1
          and sd.temp_status_rules[-1]["source"]["mode"] == "time")
    idx = len(sd.temp_status_rules) - 1          # 新模块不一定在当前分页里，直接指定
    sd._current_rule_index = lambda: idx
    sd._edit_status_rule()
    dlg = G._RULE_DIALOG_REF[0]()
    check("「编辑」打开新窗口、认出是时钟", isinstance(dlg, ME.ModuleEditor)
          and dlg._tpl.key == "clock" and dlg.tpl_list is None)
    dlg.name_edit.setText("大钟")
    dlg._accept()
    app.processEvents()
    check("编辑后列表里那一项更新了", sd.temp_status_rules[idx]["name"] == "大钟")
    sd.close()
finally:
    shutil.rmtree(SAND, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
