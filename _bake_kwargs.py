# -*- coding: utf-8 -*-
"""补烤自缩放区（气泡/桌宠/组件）里漏掉的 kit 包装函数参数。

## 为什么还有漏的
上一轮的烘焙器是**按行**正则匹配的，`kit.col(` 和 `spacing=3, margins=(...)`
不在同一行时就匹配不上；`margins=(...)` 这种元组参数更是从头到尾没进规则。
这些字面量最终都喂给 `bs()`，所以少乘的那 1.5 会直接变成少几个像素的边距。

## 判据：拿 HEAD 做对照，不靠猜
对每个「(文件, 函数, 参数) 站点」同时取 HEAD 和工作区的值：
  * 两边相等        → 这轮没烤到，要乘 1.5
  * 工作区 == HEAD×1.5 → 已经烤过，跳过
这样既不会漏、也不会二次放大，可以反复跑。

## 为什么乘 1.5 之后保留小数
`bs(v) = max(1, int(round(v * 气泡档位 × 1.5)))`（旧）
`bs(v) = max(1, int(round(v * 气泡档位)))`        （新）
取新值 v' = v×1.5 则两个表达式**逐字相同**，像素必然一致。提前取整反而会差 1px
（例：旧 bs(5)=round(7.5)=8，若烤成 7 则新 bs(7)=7）。

用法：python _bake_kwargs.py [--apply]
"""
import ast
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# {函数名: {参数名}}；参数值最终经过 bs()，所以都要乘 1.5
SCALED = {
    "row": {"spacing", "margins"},
    "col": {"spacing", "margins"},
    "btn": {"fixed_w"},
    "ghost_btn": {"fixed_w", "fixed_h"},
    "progress": {"height"},
    "scroll": {"max_h"},
    "lab": {"size"},
    "caption": set(),
    "bs": {"__arg0__"},
    "ps": {"__arg0__"},
    # bubble_ui.py 把 kit.bs 包了一层叫 _bs，上一轮的正则只认 bs/ps/_kit_sc_b…
    # 这类**本地别名**整片漏掉了（聊天输入框的自动长高就是这么失效的）。
    "_bs": {"__arg0__"},
    "_kit_sc_b": {"__arg0__"},
    "_kit_sc_p": {"__arg0__"},
    "_u": {"__arg0__"},
    "font_pt": {"__arg0__", "size"},
    "text_height": {"__arg0__", "size"},
}

FILES = ["widgets/calc.py", "widgets/canvas.py", "widgets/countdown.py",
         "widgets/counter.py", "widgets/health.py", "widgets/launcher.py",
         "widgets/mytemplate.py", "widgets/notes.py", "widgets/panel.py",
         "widgets/perler.py", "widgets/stats.py", "widgets/todo.py",
         "widgets/tokenmeter.py", "widgets/tomato.py", "widgets/webchat.py",
         "widgets/__init__.py", "widgets/kit.py",
         "bubble_layout.py", "bubble_ui.py", "h5_cards.py", "status_monitor.py"]


def fname(node):
    """取调用的函数名（kit.col → col，_kit.bs → bs，col → col）。"""
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def num(node):
    """字面量数字 → float；不是纯数字字面量返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    return None


def sites(src):
    """返回 [(序号键, 值, 节点)]；序号键 = (函数名, 参数名, 该组合第几次出现)。"""
    tree = ast.parse(src)
    seen, out = {}, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = fname(node)
        if fn not in SCALED:
            continue
        params = SCALED[fn]
        cand = []
        if "__arg0__" in params and node.args:
            cand.append(("__arg0__", node.args[0]))
        for kw in node.keywords:
            if kw.arg in params:
                cand.append((kw.arg, kw.value))
        for pname, vnode in cand:
            if isinstance(vnode, ast.Tuple):
                for i, el in enumerate(vnode.elts):
                    v = num(el)
                    if v is None:
                        continue
                    key = (fn, "%s[%d]" % (pname, i))
                    seen[key] = seen.get(key, -1) + 1
                    out.append(((fn, "%s[%d]" % (pname, i), seen[key]), v, el))
            else:
                v = num(vnode)
                if v is None:
                    continue
                key = (fn, pname)
                seen[key] = seen.get(key, -1) + 1
                out.append(((fn, pname, seen[key]), v, vnode))
    return out


def head_src(rel):
    return subprocess.check_output(["git", "show", "HEAD:" + rel],
                                   cwd=HERE).decode("utf-8")


def fmt(f):
    """乘 1.5 后的写法：能整除写整数，否则保留小数（最多 4 位）。"""
    return str(int(f)) if abs(f - int(f)) < 1e-9 else ("%g" % round(f, 4))


def main():
    apply = "--apply" in sys.argv
    total, skipped = 0, 0
    for rel in FILES:
        path = os.path.join(HERE, rel)
        if not os.path.exists(path):
            continue
        cur = io.open(path, encoding="utf-8").read()
        try:
            base = {k: v for k, v, _ in sites(head_src(rel))}
        except subprocess.CalledProcessError:
            print("  ⚠ %s 不在 HEAD 里，跳过" % rel)
            continue
        todo = []                       # (起止偏移, 原文, 新文)
        lines = cur.split("\n")
        starts = [0]
        for ln in lines:
            starts.append(starts[-1] + len(ln) + 1)
        for key, val, node in sites(cur):
            if key not in base:
                continue
            b = base[key]
            if abs(val - b * 1.5) < 1e-6 and abs(b) > 1e-9:
                skipped += 1
                continue                # 已经烤过
            if abs(val - b) > 1e-9:
                print("  ⚠ %s %s HEAD=%s 现=%s 既非相等也非 1.5 倍，人工看"
                      % (rel, key, b, val))
                continue
            if val == 0:
                continue                # 0 乘什么都是 0
            a = starts[node.lineno - 1] + node.col_offset
            z = starts[node.end_lineno - 1] + node.end_col_offset
            todo.append((a, z, cur[a:z], fmt(val * 1.5), key))
        if not todo:
            continue
        print("  %-24s %d 处" % (rel, len(todo)))
        for _a, _z, old, new, key in todo:
            print("        %-28s %-8s → %s" % ("%s.%s[%d]" % key, old, new))
        total += len(todo)
        if apply:
            for a, z, _old, new, _k in sorted(todo, reverse=True):
                cur = cur[:a] + new + cur[z:]
            ast.parse(cur)              # 写之前先保证还能编译
            io.open(path, "w", encoding="utf-8", newline="").write(cur)
    print("\n合计 %d 处要改，%d 处已烤过跳过%s"
          % (total, skipped, "（已写入）" if apply else "（干跑）"))


if __name__ == "__main__":
    main()
