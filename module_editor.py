# -*- coding: utf-8 -*-
"""添加 / 编辑气泡模块的窗口（v0.9.41 起取代 RuleDialog + TemplateGalleryDialog）。

- 添加：左边按分组选模板，右边只填这个模板要的几项，下面实时预览；
- 编辑：按内容自动认出模板（module_templates.match），只显示右半边。认不出的
  按「自定义」打开原始 JSON；内置的 CPU / 情绪等只给名称、刷新、显示方式。
- 编辑是在**原规则的副本上改表单管的那几个字段**，其余字段（开场白、max_chars、
  headers……）原样保留 —— 旧窗口只留 source/transform/fallback，会把它们弄丢。
- 原始 JSON 收在「高级」里：表单改动实时同步过去；手动改了 JSON 就以 JSON 为准、
  表单锁住（可撤销）。

尺寸都写逻辑像素，界面缩放（kit.install_ui_zoom）在控件 polish 时统一放大；
窗口显示之后才算的尺寸用 self._z() 自己换算。
"""
import copy
import json
import os
import threading
import time

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFormLayout, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

import module_templates as mt
from widgets import kit as _kit

LIST_W = 128      # 左侧模板列表宽
RIGHT_W = 330     # 右侧表单宽
ADD_MIN_H = 380   # 添加时的最小高度（列表太矮要来回滚）

_QSS = """
QLabel#h1{color:#eef2f8;font-size:15px;font-weight:bold;}
QLabel#desc{color:#8f9bb0;font-size:11px;}
QLabel#flab{color:#aab3c5;font-size:12px;}
QLabel#cap{color:#6f7b90;font-size:11px;}
QLabel#ok{color:#5fbf7f;font-size:11px;}
QLabel#bad{color:#f0a35e;font-size:11px;}
QLabel#pv_t{color:#8f9bb0;font-size:12px;}
QLabel#pv_v{color:#eef2f8;font-size:13px;}
QLineEdit,QComboBox,QPlainTextEdit,QSpinBox{background:#161b25;color:#dfe6f2;
  border:1px solid #39414f;border-radius:5px;padding:3px 6px;font-size:12px;
  font-family:'Microsoft YaHei';}
QLineEdit:disabled,QComboBox:disabled,QPlainTextEdit:disabled{color:#6f7b90;}
QPlainTextEdit#code{font-family:Consolas,'Microsoft YaHei';font-size:12px;}
QPushButton#seg{min-width:0;background:#1b212c;color:#aab3c5;border:1px solid #39414f;
  border-radius:0px;padding:3px 10px;font-size:12px;}
QPushButton#seg:checked{background:#2c4a73;color:#ffffff;border-color:#4a90e2;}
QPushButton#seg:disabled{color:#5a6475;}
QPushButton#link{min-width:0;background:transparent;border:none;color:#4a90e2;
  padding:0px;font-size:12px;text-align:left;}
QPushButton#link:hover{color:#7ab0f0;}
QListWidget#tpl{background:#171c26;border:1px solid #2c3442;border-radius:8px;
  outline:none;padding:3px;font-family:'Microsoft YaHei';font-size:12px;}
QListWidget#tpl::item{color:#c7d0e0;padding:3px 6px;border-radius:4px;}
QListWidget#tpl::item:selected{background:#2c4a73;color:#ffffff;}
QFrame#preview{background:#141922;border:1px solid #2c3442;border-radius:8px;}
"""


class _Bridge(QObject):
    done = pyqtSignal(int, str, str)      # (序号, 结果, 错误)


def _lab(text, name="flab", wrap=False):
    lb = QLabel(text)
    lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class ModuleEditor(_kit.DarkDialog):
    """rule=None 为添加。结果在 self.rule（dict）。others：用来查重名的现有模块。"""

    def __init__(self, parent=None, rule=None, settings=None, others=()):
        self._orig = copy.deepcopy(rule) if isinstance(rule, dict) else None
        add = self._orig is None
        super().__init__("添加模块" if add else "编辑模块 · %s" % self._orig.get("name", ""),
                         parent)
        import pet_gravity as G
        G._apply_dark_style(self, _QSS)
        self.add_help_button(self._open_doc, "打开《自定义模块开发指南》")
        self.settings = settings if settings is not None else {}
        self._others = list(others or ())
        self._rid = (self._orig or {}).get("id") or "r%d" % int(time.time() * 1000)
        self.rule = None
        self._tpl = None
        self._fields = {}           # 参数 key -> 输入控件
        self._auto_name = add       # 名称还没被用户改过 → 换模板时跟着换
        self._json_dirty = False
        self._syncing = False
        self._baseline = None
        self._seq = 0
        self._bridge = _Bridge(self)
        self._bridge.done.connect(self._on_preview_done)
        self._pv_timer = QTimer(self, singleShot=True, interval=500,
                                timeout=self._run_preview)
        self._tick = QTimer(self, interval=1000, timeout=self._run_preview)

        root = QHBoxLayout(self.body)
        root.setContentsMargins(10, 10, 12, 10)
        root.setSpacing(10)
        self.tpl_list = None
        if add:
            self.tpl_list = self._make_list()
            root.addWidget(self.tpl_list)

        right = QWidget()
        right.setFixedWidth(RIGHT_W)
        v = QVBoxLayout(right)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.h1 = _lab("", "h1")
        self.desc = _lab("", "desc", wrap=True)
        v.addWidget(self.h1)
        v.addWidget(self.desc)
        self._form_host = QVBoxLayout()
        self._form_host.setContentsMargins(0, 2, 0, 0)
        v.addLayout(self._form_host)
        v.addWidget(self._make_preview())
        self.adv_btn = QPushButton()
        self.adv_btn.setObjectName("link")
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(self._on_adv)
        v.addWidget(self.adv_btn)
        self.json_edit = QPlainTextEdit()
        self.json_edit.setObjectName("code")
        self.json_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.json_edit.setMinimumHeight(150)
        self.json_edit.textChanged.connect(self._on_json_edited)
        self.json_edit.hide()
        v.addWidget(self.json_edit)
        jrow = QHBoxLayout()
        jrow.setContentsMargins(0, 0, 0, 0)
        self.json_note = _lab("", "bad", wrap=True)
        self.json_undo = QPushButton("撤销 JSON 修改")
        self.json_undo.setObjectName("link")
        self.json_undo.clicked.connect(self._undo_json)
        jrow.addWidget(self.json_note, 1)
        jrow.addWidget(self.json_undo, 0, Qt.AlignTop)
        self.json_row = QWidget()
        self.json_row.setLayout(jrow)
        self.json_row.hide()
        v.addWidget(self.json_row)
        self.hint = _lab("", "bad", wrap=True)
        self.hint.hide()
        v.addWidget(self.hint)
        v.addStretch(1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        # 和设置窗同一套按钮（kit.btn 是气泡里的按钮，按气泡档位算，放进窗口会
        # 再被界面缩放放大一次，字比别处大一圈）
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("添加" if add else "保存")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(self.ok_btn)
        v.addLayout(btns)
        root.addWidget(right)

        if add:
            first = next(i for i in range(self.tpl_list.count())
                         if self.tpl_list.item(i).data(Qt.UserRole))
            self.tpl_list.setCurrentRow(first)      # → _on_pick
        else:
            t, params = mt.match(self._orig)
            self._load(t, params)

    # ------------------------------------------------------------ 构造
    def _make_list(self):
        lw = QListWidget()
        lw.setObjectName("tpl")
        lw.setFixedWidth(LIST_W)
        lw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        head_font = QFont(_kit._FONT)
        head_font.setBold(True)
        for group, items in mt.ui_groups():
            it = QListWidgetItem(group)
            it.setFlags(Qt.NoItemFlags)
            it.setFont(head_font)
            it.setForeground(QColor("#6f7b90"))
            lw.addItem(it)
            for t in items:
                it = QListWidgetItem("  " + t.title)
                it.setData(Qt.UserRole, t.key)
                it.setToolTip(t.desc)
                lw.addItem(it)
        lw.currentItemChanged.connect(self._on_pick)
        return lw

    def _make_preview(self):
        fr = QFrame()
        fr.setObjectName("preview")
        v = QVBoxLayout(fr)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(_lab("预览 · 气泡里会显示成这样", "cap"), 1)
        self.pv_run = QPushButton("试一下")
        self.pv_run.setObjectName("link")
        self.pv_run.clicked.connect(self._run_preview)
        top.addWidget(self.pv_run)
        v.addLayout(top)
        row = QHBoxLayout()
        self.pv_title = _lab("", "pv_t")
        self.pv_value = _lab("—", "pv_v", wrap=True)
        row.addWidget(self.pv_title, 0, Qt.AlignTop)
        row.addSpacing(8)
        row.addWidget(self.pv_value, 1)
        v.addLayout(row)
        self.pv_status = _lab("", "ok", wrap=True)
        v.addWidget(self.pv_status)
        self.preview = fr
        return fr

    def _build_form(self, t, params, name):
        """每换一次模板就整块重建表单（新控件 polish 时才会被界面缩放统一放大）。"""
        while self._form_host.count():
            w = self._form_host.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        box = QWidget()
        f = QFormLayout(box)
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(6)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_edit = QLineEdit(name)
        self.name_edit.textEdited.connect(self._on_name_edited)
        self.name_edit.textChanged.connect(self._changed)
        f.addRow(_lab("名称"), self.name_edit)

        self._fields = {}
        for p in t.params:
            if not p.form:
                continue
            val = params.get(p.key, p.default)
            if p.kind == "choice":
                w = QComboBox()
                for k, txt in p.choices:
                    w.addItem(txt, k)
                i = w.findData(val)
                w.setCurrentIndex(i if i >= 0 else 0)
                w.currentIndexChanged.connect(self._changed)
            elif p.kind in ("text", "code"):
                w = QPlainTextEdit(str(val))
                w.setPlaceholderText(p.hint)
                w.setTabChangesFocus(p.kind == "text")
                if p.kind == "code":
                    w.setObjectName("code")
                    w.setLineWrapMode(QPlainTextEdit.NoWrap)
                    w.setFixedHeight(120)
                else:
                    w.setFixedHeight(64)
                w.textChanged.connect(self._changed)
            else:
                w = QLineEdit(str(val))
                w.setPlaceholderText(p.hint)
                w.textChanged.connect(self._changed)
            f.addRow(_lab(p.label), w)
            self._fields[p.key] = w

        if t.key == "ai":
            mrow = QHBoxLayout()
            mrow.setContentsMargins(0, 0, 0, 0)
            self.ai_lab = _lab("")
            go = QPushButton("AI 设置…")
            go.setObjectName("link")
            go.clicked.connect(self._open_ai_settings)
            mrow.addWidget(self.ai_lab, 1)
            mrow.addWidget(go)
            mw = QWidget()
            mw.setLayout(mrow)
            f.insertRow(1, _lab("模型"), mw)
            self._refresh_ai_label()

        orig = self._orig or {}
        # 规则里没写 interval 时运行时按 60 秒算（module_core.module_spec），照实显示
        cur_iv = int((orig.get("interval", 60) if self._orig is not None
                      else t.base.get("interval", 60)) or 60)
        self._iv_initial = cur_iv
        # 可以下拉选常用档位，也可以直接手填（如 45 秒、2 分钟、1.5 小时）
        self.iv_combo = QComboBox()
        self.iv_combo.setEditable(True)
        self.iv_combo.setInsertPolicy(QComboBox.NoInsert)
        for sec, txt in mt.INTERVALS:
            self.iv_combo.addItem(txt, sec)
        if self.iv_combo.findData(cur_iv) < 0:
            self.iv_combo.addItem(mt.interval_text(cur_iv), cur_iv)
        self.iv_combo.setCurrentIndex(self.iv_combo.findData(cur_iv))
        self.iv_combo.lineEdit().setPlaceholderText("如 45 秒、2 分钟")
        self.iv_combo.setToolTip("可以直接手填：90、45 秒、5 分钟、1.5 小时、1 天")
        self.iv_combo.editTextChanged.connect(self._changed)
        self.iv_label = _lab("刷新")
        f.addRow(self.iv_label, self.iv_combo)

        self.pop_btn = None
        if t.popup:
            seg = QWidget()
            h = QHBoxLayout(seg)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            grp = QButtonGroup(seg)
            emb = QPushButton("显示在气泡里")
            self.pop_btn = QPushButton("定时弹出")
            for b in (emb, self.pop_btn):
                b.setObjectName("seg")
                b.setCheckable(True)
                b.setAutoDefault(False)
                grp.addButton(b)
                h.addWidget(b)
            emb.setStyleSheet("border-top-left-radius:5px;border-bottom-left-radius:5px;")
            self.pop_btn.setStyleSheet(
                "border-top-right-radius:5px;border-bottom-right-radius:5px;")
            popup = bool(orig.get("popup")) and not (orig.get("embed", False)
                                                     and orig.get("builtin") != "mood")
            (self.pop_btn if popup else emb).setChecked(True)
            h.addSpacing(10)
            self.dur_lab = _lab("停留")
            self.dur_spin = QSpinBox()
            self.dur_spin.setRange(1, 600)
            self.dur_spin.setSuffix(" 秒")
            self.dur_spin.setValue(int(orig.get("popup_duration", 3) or 3))   # 运行时默认 3 秒
            self.dur_spin.valueChanged.connect(self._changed)
            h.addWidget(self.dur_lab)
            h.addSpacing(4)
            h.addWidget(self.dur_spin)
            h.addStretch(1)
            self.pop_btn.toggled.connect(self._on_display)
            f.addRow(_lab("显示"), seg)
        self._form = f
        box.ensurePolished()
        self._form_host.addWidget(box)
        self._sync_display_rows()

    # ------------------------------------------------------------ 切换 / 载入
    def _on_pick(self, item, _prev=None):
        key = item.data(Qt.UserRole) if item is not None else None
        if not key:
            return
        t = mt.BY_KEY[key]
        name = t.title if self._auto_name else self.name_edit.text()
        self._load(t, t.defaults(), name)

    def _load(self, t, params, name=None):
        self._tpl = t
        self._json_dirty = False
        orig = self._orig or {}
        if t is mt.BUILTIN:
            self.h1.setText("内置模块")
            self.desc.setText("桌宠自带的「%s」。可以改名称、刷新间隔和显示方式，"
                              "其余在「高级」里。" % orig.get("name", ""))
        elif t is mt.CUSTOM and self._orig is not None:
            self.h1.setText("自定义")
            self.desc.setText("这个模块的配置没法用表单表示，下面是它的原始 JSON，"
                              "改完直接保存。")
        else:
            self.h1.setText(t.title)
            self.desc.setText(t.desc)
        self._build_form(t, params, name if name is not None else orig.get("name", t.title))
        custom = t is mt.CUSTOM
        self.adv_btn.setVisible(not custom)
        self._syncing = True
        self.json_edit.setPlainText(self._json_text(self._orig if custom and self._orig
                                                    else self._form_rule()))
        self._syncing = False
        self.adv_btn.blockSignals(True)
        self.adv_btn.setChecked(custom)
        self.adv_btn.blockSignals(False)
        self.json_edit.setVisible(custom)
        self._update_adv_text()
        self._lock_form(False)
        self.json_row.hide()
        self.hint.hide()
        self.preview.setVisible(t.preview is not None)
        self.pv_run.setVisible(t.preview == "manual")
        self.pv_title.setText(self.name_edit.text())
        self.pv_value.setText("—")
        self._set_status("改完点「试一下」看看效果" if t.preview == "manual" else "", True)
        if t.key in ("clock", "countdown"):
            self._tick.start()
        else:
            self._tick.stop()
        if t.preview == "auto":
            self._run_preview()
        self._baseline = self.compose(strict=False)[0]
        self._fit()

    # ------------------------------------------------------------ 规则组装
    def _values(self):
        out = {}
        for k, w in self._fields.items():
            if isinstance(w, QComboBox):
                out[k] = w.currentData()
            elif isinstance(w, QPlainTextEdit):
                out[k] = w.toPlainText()
            else:
                out[k] = w.text()
        return out

    def _popup_on(self):
        return self.pop_btn is not None and self.pop_btn.isChecked()

    def _form_rule(self):
        """表单 → 规则（不含 id / name）。编辑时在原规则副本上改，别的字段原样留着。"""
        t = self._tpl
        rule = copy.deepcopy(self._orig) if self._orig is not None else t.build("")
        t.write(rule, self._values())
        iv = self._iv_value() or self._iv_initial
        # 只在新建、原来就有、或用户改过时才写 —— 打开再保存不凭空多字段
        if (t.refresh or self._popup_on()) and (
                self._orig is None or "interval" in rule or iv != self._iv_initial):
            rule["interval"] = iv
        if t.popup:
            popup = self._popup_on()
            rule["popup"] = popup
            rule["embed"] = not popup
            # 没设过、也还是默认 3 秒就不写，免得打开再保存凭空多出一个字段
            if popup and ("popup_duration" in rule or self.dur_spin.value() != 3):
                rule["popup_duration"] = self.dur_spin.value()
        for k in mt.HIDDEN_KEYS:
            rule.pop(k, None)
        return rule

    def _iv_value(self):
        return mt.parse_interval(self.iv_combo.currentText())

    def is_dirty(self):
        """和刚打开（或刚换模板）时比有没有改过。切到别的模块前用来决定要不要问。"""
        if self._tpl is None:
            return False
        if (self._tpl.refresh or self._popup_on()) and self._iv_value() is None:
            return True
        rule, err = self.compose(strict=False)
        return bool(err) or rule != self._baseline

    def rule_id(self):
        return None if self._orig is None else self._rid

    def _json_text(self, rule):
        body = {k: v for k, v in (rule or {}).items() if k not in mt.HIDDEN_KEYS}
        return json.dumps(body, ensure_ascii=False, indent=2)

    def _json_mode(self):
        return self._tpl is mt.CUSTOM or self._json_dirty

    def compose(self, strict=True):
        """返回 (规则, 错误)。strict：保存前的完整校验（必填项 / 格式）。"""
        t = self._tpl
        name = self.name_edit.text().strip() or t.title
        if self._json_mode():
            try:
                body = json.loads(self.json_edit.toPlainText().strip() or "{}")
            except ValueError as e:
                return None, "JSON 格式不对：%s" % e
            if not isinstance(body, dict):
                return None, "JSON 最外层要是 { … } 对象"
        else:
            if strict:
                vals = self._values()
                for p in t.params:
                    if p.form:
                        err = p.error(str(vals.get(p.key, "")).strip())
                        if err:
                            return None, "%s：%s" % (p.label, err)
            if strict and (self._tpl.refresh or self._popup_on())                     and self._iv_value() is None:
                return None, "%s看不懂：写成 45 秒、5 分钟、1.5 小时这样" % self.iv_label.text()
            body = self._form_rule()
        rule = {"id": self._rid, "name": name}
        rule.update({k: v for k, v in body.items() if k not in mt.HIDDEN_KEYS})
        if (self._orig or {}).get("builtin"):
            rule["builtin"] = self._orig["builtin"]
        import status_monitor as sm
        _norm, err = sm._normalize_rule(copy.deepcopy(rule))
        if err:
            return None, err
        return rule, ""

    # ------------------------------------------------------------ 交互
    def _on_name_edited(self, _text):
        self._auto_name = False

    def _changed(self, *_):
        if self._tpl is None or not hasattr(self, "iv_combo"):
            return
        self.pv_title.setText(self.name_edit.text().strip() or self._tpl.title)
        self.hint.hide()
        if not self._json_mode():
            self._syncing = True
            self.json_edit.setPlainText(self._json_text(self._form_rule()))
            self._syncing = False
        if self._tpl.preview == "auto":
            self._pv_timer.start()

    def _on_display(self, _on):
        self._sync_display_rows()
        self._changed()
        self._fit()

    def _sync_display_rows(self):
        t = self._tpl
        popup = self._popup_on()
        show_iv = t.refresh or popup
        self.iv_combo.setVisible(show_iv)
        self.iv_label.setVisible(show_iv)
        self.iv_label.setText("弹出间隔" if popup else "刷新")
        if self.pop_btn is not None:
            self.dur_lab.setVisible(popup)
            self.dur_spin.setVisible(popup)

    def _on_adv(self, on):
        self.json_edit.setVisible(on)
        self.json_row.setVisible(on and self._json_dirty)
        self._update_adv_text()
        self._fit()

    def _update_adv_text(self):
        self.adv_btn.setText(("▾ " if self.adv_btn.isChecked() else "▸ ")
                             + "高级：原始配置 JSON")

    def _on_json_edited(self):
        if self._syncing or self._tpl is None or self._tpl is mt.CUSTOM:
            return
        if not self._json_dirty:
            self._json_dirty = True
            self._lock_form(True)
            self.json_note.setText("已手动改了 JSON，保存时以 JSON 为准（上面的表单已锁定）")
            self.json_row.show()
            self._fit()

    def _undo_json(self):
        self._json_dirty = False
        self._lock_form(False)
        self.json_row.hide()
        self._syncing = True
        self.json_edit.setPlainText(self._json_text(self._form_rule()))
        self._syncing = False
        self._fit()

    def _lock_form(self, locked):
        for i in range(self._form.rowCount()):
            fi = self._form.itemAt(i, QFormLayout.FieldRole)
            w = fi.widget() if fi is not None else None
            if w is not None and w is not self.name_edit:
                w.setEnabled(not locked)

    def _refresh_ai_label(self):
        import status_monitor as sm
        src = (self._orig or {}).get("source") or {}
        prof = self.settings.get("ai_profile") or {}
        if sm._valid_api_key(src.get("api_key")):
            text, ok = "%s（本模块单独配置）" % (src.get("model") or src.get("base_url", "")), True
        elif str(prof.get("base_url", "")).strip() and str(prof.get("api_key", "")).strip():
            text = " · ".join(str(x) for x in (prof.get("provider"), prof.get("model")) if x)
            text, ok = text or "已配置", True
        else:
            text, ok = "还没配置大模型", False
        self.ai_lab.setText(text)
        self.ai_lab.setObjectName("flab" if ok else "bad")
        self.ai_lab.setStyleSheet("")          # 换 objectName 后让样式重新生效

    def _open_ai_settings(self):
        import pet_gravity as G
        G.AISettingsDialog(self.settings, self).exec_()
        self._refresh_ai_label()

    def _open_doc(self):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "自定义模块开发指南.md")
        try:
            os.startfile(p)
        except Exception as e:
            self._show_hint("打开开发指南失败：%s" % e)

    def _show_hint(self, text):
        self.hint.setText(text)
        self.hint.show()
        self._fit()

    # ------------------------------------------------------------ 预览
    def _set_status(self, text, ok):
        self.pv_status.setText(text)
        self.pv_status.setObjectName("ok" if ok else "bad")
        self.pv_status.setStyleSheet("")
        self.pv_status.setVisible(bool(text))

    def _run_preview(self):
        if self._tpl is None or self._tpl.preview is None or not self.preview.isVisibleTo(self):
            return
        rule, err = self.compose(strict=True)
        self._seq += 1
        seq = self._seq
        if err:
            self.pv_value.setText("—")
            self._set_status(err, False)
            return
        stype = str((rule.get("source") or {}).get("type", ""))
        if stype in ("clock", "static", "disk"):     # 本地、瞬时：直接算，免得闪
            self._on_preview_done(seq, *self._collect(rule))
            return
        if seq == self._seq and not self._tick.isActive():
            self._set_status("取数中…", True)

        def work():
            val, perr = self._collect(rule)
            try:
                self._bridge.done.emit(seq, val, perr)
            except RuntimeError:          # 窗口已关
                pass
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _collect(rule):
        try:
            from status_monitor import RuleProvider
            rp = RuleProvider(rule)
            return str(rp.collect() or ""), str(rp._last_error or "")
        except Exception as e:
            return "", str(e)

    def _on_preview_done(self, seq, val, err):
        if seq != self._seq:
            return                         # 已经又改过了，这是旧结果
        if len(val) > 120:
            val = val[:120] + "…"
        self.pv_value.setText(val or "（空）")
        if err:
            self._set_status("✗ " + err[:100], False)
        elif not self._tick.isActive():
            self._set_status("✓ 刚刚取到", True)
        else:
            self._set_status("", True)
        self._fit()

    # ------------------------------------------------------------ 保存
    def _accept(self):
        rule, err = self.compose(strict=True)
        if err:
            self._show_hint(err)
            return
        for o in self._others:
            if (str(o.get("id")) != str(self._rid)
                    and str(o.get("name", "")).strip() == rule["name"]):
                self._show_hint("已经有叫「%s」的模块了（含内置），换个名字吧。" % rule["name"])
                return
        self.rule = rule
        self.accept()

    # ------------------------------------------------------------ 尺寸
    def _z(self, v):
        return _kit.ui_i(v) if self.property("oiZ") else v

    def _fit(self):
        """高度贴着内容走（少留白）；添加时给列表留个够用的最小高度。"""
        lay = self.layout()
        if lay is None:
            return
        # 控件刚 hide / show 时，父控件缓存的尺寸要等排队的 LayoutRequest 处理后
        # 才更新；直接取 sizeHint 拿到的还是展开时的高度 —— 收起「高级」后窗口
        # 留一大片空白就是这么来的
        # 每层父控件都排着一个 LayoutRequest，逐层处理掉（嵌套几层就要几轮）
        for _ in range(4):
            QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        lay.activate()
        w = lay.totalSizeHint().width()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() \
            else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height())
        if self.tpl_list is not None:
            h = max(h, self._z(ADD_MIN_H))
        self.setFixedSize(w, h)

    def showEvent(self, event):
        self._fit()                 # polish（界面缩放）之后再按真实尺寸定一次
        super().showEvent(event)
        if self._tpl is not None and self._tpl.preview == "auto":
            self._run_preview()

    def done(self, r):
        self._tick.stop()
        self._pv_timer.stop()
        self._seq += 1               # 后台还没回来的预览结果一律作废
        super().done(r)


def ask_save(parent, name):
    """编辑窗口里有没保存的修改、又要切到别的模块时问一句。
    返回 "save" / "discard" / "cancel"。"""
    d = _kit.DarkDialog("切换模块", parent)
    import pet_gravity as G
    G._apply_dark_style(d)
    v = QVBoxLayout(d.body)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(10)
    lb = QLabel("「%s」改了还没保存，要先保存吗？" % name)
    lb.setWordWrap(True)
    lb.setStyleSheet("color:#dfe6f2;font-size:12px;")
    v.addWidget(lb)
    row = QHBoxLayout()
    row.addStretch(1)
    out = ["cancel"]
    for text, key, primary in (("取消", "cancel", False), ("不保存", "discard", False),
                               ("保存", "save", True)):
        b = QPushButton(text)
        if primary:
            b.setObjectName("primary")
            b.setDefault(True)
        else:
            b.setAutoDefault(False)
        b.clicked.connect(lambda _=False, k=key: (out.__setitem__(0, k), d.accept()))
        row.addWidget(b)
    v.addLayout(row)
    d.setFixedWidth(300)
    _kit.place_near(d, parent)
    d.exec_()
    return out[0]


def live_editor(ref):
    """取还开着的那个编辑窗口（_RULE_DIALOG_REF 里存的弱引用）；没有返回 None。"""
    try:
        d = ref() if ref else None
        if d is not None and not d.isHidden():
            return d
    except RuntimeError:          # 底层已删
        pass
    return None


def release(dlg):
    """要在这个编辑窗口里换别的模块：有改动先问要不要保存。
    返回 True = 窗口已关，可以开新的；False = 用户取消，或保存时没通过校验（窗口留着、显示原因）。"""
    if dlg.is_dirty():
        ans = ask_save(dlg, dlg.name_edit.text().strip() or "这个模块")
        if ans == "cancel":
            return False
        if ans == "save":
            dlg._accept()
            return dlg.rule is not None
    dlg.reject()
    return True
