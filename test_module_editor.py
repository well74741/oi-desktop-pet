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
from widgets import kit                              # noqa: E402
kit.install_ui_zoom(app)          # 和真程序一样开界面缩放：字号 / 尺寸放大两次的问题只在这时出现
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

    # ===== 十一、第二轮反馈 =====
    print("\n--- 十一、按钮字号 / 窗口尺寸 ---")
    from PyQt5.QtWidgets import QPushButton
    import update_ui
    import updater

    def pump(n=5):
        for _ in range(n):
            app.processEvents()

    sd = G.SettingsDialog(G.load_settings())
    sd.show()
    pump()
    ref = next(b for b in sd.findChildren(QPushButton) if b.text() == "确定")
    ref_px = ref.font().pixelSize()
    check("模块列表第二栏改名为「已添加模块」", sd._tab_custom.text() == "已添加模块")
    e = ME.ModuleEditor(None, None, ST)
    e.show()
    pump()
    check("添加模块窗口的按钮和设置窗一样大（以前是气泡按钮，被放大两次）",
          e.ok_btn.font().pixelSize() == ref_px,
          "%d vs %d" % (e.ok_btn.font().pixelSize(), ref_px))
    e.close()
    _mode = updater.install_mode
    updater.install_mode = lambda: "installed"
    try:
        u = update_ui.UpdateDialog(None, None, {
            "version": "9.9.9", "tag": "v9.9.9", "title": "v9.9.9",
            "notes": "- 改动一\n- 改动二", "asset": {"name": "setup.exe", "size": 1}})
        u.show()
        pump()
        btns = [b for b in u.findChildren(QPushButton) if b.isVisible() and b.text()]
        check("检查更新窗口不再巨大（以前 380 被放大两次，实际 855 宽）",
              u.width() <= 540, "宽 %d" % u.width())
        check("检查更新窗口的按钮和设置窗一样大",
              btns and all(b.font().pixelSize() == ref_px for b in btns),
              str([(b.text(), b.font().pixelSize()) for b in btns]))
        u.close()
    finally:
        updater.install_mode = _mode

    d = ME.ModuleEditor(None, mt.BY_KEY["countdown"].build("下班", {}), ST)
    d.show()
    pump()
    h0 = d.height()
    d.adv_btn.setChecked(True)
    h1 = d.height()
    d.adv_btn.setChecked(False)
    check("展开「高级」再收起，窗口缩回原来的高度（以前留一大片空白）",
          h1 > h0 and d.height() == h0, "%d -> %d -> %d" % (h0, h1, d.height()))
    d.close()

    print("\n--- 十二、间隔可以手填 ---")
    cases = {"90": 90, "45 秒": 45, "5分钟": 300, "1.5 小时": 5400, "2天": 172800,
             "每 30 分钟": 1800, "每小时": 3600, "每秒": 1, "10s": 10, "abc": None, "0": None}
    bad = {k: mt.parse_interval(k) for k, v in cases.items() if mt.parse_interval(k) != v}
    check("间隔文字解析（90 / 45 秒 / 5分钟 / 1.5 小时 / 下拉档位 / 乱填）", not bad, str(bad))
    d = ME.ModuleEditor(None, None, ST)
    pick(d, "static")
    d.pop_btn.setChecked(True)
    check("间隔框可以直接打字", d.iv_combo.isEditable())
    d.iv_combo.setEditText("45 秒")
    out, err = d.compose()
    check("弹出间隔手填 45 秒 -> 存成 45", not err and out["interval"] == 45, err)
    d.iv_combo.setEditText("一会儿")
    d._accept()
    check("填了看不懂的 -> 不保存、给提示", d.rule is None and "看不懂" in d.hint.text(),
          d.hint.text())

    print("\n--- 十三、编辑窗口开着时点别的模块 ---")
    sd._switch_rules_tab("builtin")
    rows = [sd.temp_status_rules[i] for i in sd._view_indices]
    sd.status_rules_list.setCurrentRow(0)
    sd._edit_status_rule()
    ed = ME.live_editor(G._RULE_DIALOG_REF[0])
    check("先打开第 1 个模块", ed is not None and ed.rule_id() == rows[0]["id"])
    sd.status_rules_list.setCurrentRow(1)
    pump()
    ed = ME.live_editor(G._RULE_DIALOG_REF[0])
    check("没改动 -> 点第 2 个，编辑窗口直接切过去、不问",
          ed is not None and ed.rule_id() == rows[1]["id"])
    asked = []
    _ask = ME.ask_save
    try:
        old_name = rows[1]["name"]
        ed.name_edit.setText("改了名字A")
        ME.ask_save = lambda parent, name: (asked.append(name), "cancel")[1]
        sd.status_rules_list.setCurrentRow(2)
        pump()
        ed2 = ME.live_editor(G._RULE_DIALOG_REF[0])
        check("有改动 -> 先问；选「取消」-> 留在原模块、改动还在、列表选中项退回",
              asked and ed2 is ed and ed.name_edit.text() == "改了名字A"
              and sd._current_rule_index() == sd._view_indices[1])
        ME.ask_save = lambda parent, name: "discard"
        sd.status_rules_list.setCurrentRow(2)
        pump()
        ed2 = ME.live_editor(G._RULE_DIALOG_REF[0])
        check("选「不保存」-> 切到第 3 个，第 2 个原样",
              ed2 is not None and ed2.rule_id() == rows[2]["id"]
              and next(r for r in sd.temp_status_rules
                       if r["id"] == rows[1]["id"])["name"] == old_name)
        ed2.name_edit.setText("改了名字B")
        ME.ask_save = lambda parent, name: "save"
        sd.status_rules_list.setCurrentRow(3)
        pump()
        ed3 = ME.live_editor(G._RULE_DIALOG_REF[0])
        check("选「保存」-> 改动存进列表，再切到第 4 个",
              ed3 is not None and ed3.rule_id() == rows[3]["id"]
              and next(r for r in sd.temp_status_rules
                       if r["id"] == rows[2]["id"])["name"] == "改了名字B"
              and sd._current_rule_index() == sd._view_indices[3])
    finally:
        ME.ask_save = _ask
    ed = ME.live_editor(G._RULE_DIALOG_REF[0])
    if ed is not None:
        ed.close()

    print("\n--- 十四、设置窗测试区 ---")
    sd._on_rule_test_done("拼豆", "", "", sm.RuleProvider(mt.BY_KEY["perler"].build("拼豆")))
    first = sd.rules_result_view.host._cards[0]
    sd._on_rule_test_done("计数", "", "", sm.RuleProvider(mt.BY_KEY["counter"].build("计数")))
    second = sd.rules_result_view.host._cards[0]
    check("组件按气泡里的宽度摆（以前硬压成 184，工具栏按钮挤在一起）",
          second.width() == kit.bubble_widget_width(),
          "%d vs %d" % (second.width(), kit.bubble_widget_width()))
    check("换测一个模块，上一个组件当场撤掉（以前叠在一起）",
          first.isHidden() and first.parent() is None)
    sd.rules_result_view.setFixedSize(225, 90)
    pump()
    check("测试区比组件小 -> 出滚动条，不硬压",
          sd.rules_result_view.host.horizontalScrollBar().maximum() > 0
          and sd.rules_result_view.host.verticalScrollBar().maximum() > 0)
    sd.close()
    st = G.load_settings()
    st["status_rules"] = [dict(mt.BY_KEY["counter"].build("计数"), id="t_counter")]
    G.save_settings(st)
    pet = G.GravityPet(G.load_settings())
    pet.move(-3000, 300)
    pet.show()
    pet.status_bubble._do_show()
    for _ in range(40):
        pump(1)
    from widgets import ModuleWidget
    real = [w.width() for w in pet.status_bubble.findChildren(ModuleWidget) if w.isVisible()]
    check("……这个宽度和真气泡里组件的宽度一致",
          real and real[0] == kit.bubble_widget_width(),
          "%s vs %d" % (real, kit.bubble_widget_width()))
    pet.close()

    print("\n--- 十五、弹出气泡的位置 ---")
    scr = (0, 0, 1920, 1040)
    sizes = [(165, 40), (130, 40)]
    pet_top = 600
    side = (245, 190, 560, 1000)            # 用户截图：放大后的气泡栏在桌宠右边，很高
    got = sm.popup_slots(sizes, 160, pet_top, scr, side)
    check("气泡栏在旁边 -> 弹出气泡就在桌宠头顶（以前被顶到气泡栏上缘 190 以上）",
          all(y > 450 and y + h <= pet_top for (x, y), (w, h) in zip(got, sizes)), str(got))
    check("  ……而且不压着气泡栏", all(x + w <= side[0] for (x, y), (w, h) in zip(got, sizes)))
    above = (60, 150, 375, 590)             # 气泡栏正好在桌宠头顶
    got = sm.popup_slots(sizes, 220, pet_top, scr, above)
    check("气泡栏在头顶 -> 往旁边让开，高度仍贴着桌宠",
          all((x + w <= above[0] or x >= above[2]) and y > 450
              for (x, y), (w, h) in zip(got, sizes)), str(got))
    wide = (0, 150, 1920, 590)              # 两边都没地方
    got = sm.popup_slots(sizes, 220, pet_top, scr, wide)
    check("两边都放不下 -> 才叠到气泡栏上方",
          all(y + h <= wide[1] for (x, y), (w, h) in zip(got, sizes)), str(got))
    got = sm.popup_slots(sizes, 220, pet_top, scr, None)
    check("没有气泡栏 -> 叠在桌宠头顶、互不重叠",
          got[0][1] + 40 <= pet_top and got[1][1] + 40 <= got[0][1], str(got))
finally:
    shutil.rmtree(SAND, ignore_errors=True)

print("\n通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))

# 这个套件会真建桌宠 / 气泡 / 设置窗和一堆 Qt 弹窗。跑完解释器正常退出时，Qt 拆这些
# 窗口会段错误（退出码 139 / 3221225477；测试本身 79 项全过）。结果已经打印完了，
# 直接退，不跑那套析构 —— 退出码才是真实结论。
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if FAIL else 0)
