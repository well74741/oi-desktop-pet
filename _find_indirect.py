# -*- coding: utf-8 -*-
"""找「字面量先进变量、再喂给缩放函数」的漏网之鱼。

烘焙器都是看调用点：`bs(4)` 能认，`for k in (4, 6, 8): bs(k)` 认不出来 ——
字面量在元组里，调用点看到的只是变量名。手柄三点就是这么漏的。

做法：用 AST 找出所有「实参是局部变量名」的缩放函数调用，再在同一个函数体里
回溯那个名字是从哪些字面量来的（赋值 / for 循环的元组或列表），打出来人工判。
范围有意放宽、宁可多报，所以输出需要逐条看，不要当成"待自动修改列表"。

用法：python _find_indirect.py
"""
import ast
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

SCALE = {"bs", "ps", "font_pt", "text_height", "_kit_sc_b", "_kit_sc_p", "_kv",
         "ui", "ui_i", "scale_qss"}

FILES = [f for f in os.listdir(HERE) if f.endswith(".py")
         and not f.startswith("_") and not f.startswith("test_")]
FILES += [os.path.join("widgets", f) for f in os.listdir(os.path.join(HERE, "widgets"))
          if f.endswith(".py")]


def fname(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def nums_of(node):
    """从节点里摘出所有数字字面量。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            out.append(n.value)
    return out


def scan(rel):
    path = os.path.join(HERE, rel)
    try:
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        # 这个函数里，哪些名字被当作缩放函数的实参
        wanted = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and fname(node) in SCALE:
                for a in node.args:
                    if isinstance(a, ast.Name):
                        wanted.add(a.id)
        if not wanted:
            continue
        # 这些名字是从哪些字面量来的
        for node in ast.walk(fn):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                    and node.target.id in wanted \
                    and isinstance(node.iter, (ast.Tuple, ast.List)):
                ns = nums_of(node.iter)
                if ns:
                    hits.append((node.lineno, "for %s in %s" % (node.target.id, ns)))
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id in wanted:
                ns = nums_of(node.value)
                if ns:
                    hits.append((node.lineno, "%s = ...%s" % (node.targets[0].id, ns)))
    return hits


total = 0
for rel in sorted(set(FILES)):
    hits = scan(rel)
    if not hits:
        continue
    print("%s" % rel)
    for ln, what in sorted(hits):
        print("    L%-5d %s" % (ln, what))
        total += 1
print("\n共 %d 处需要人工确认（字面量是否已含那 1.5）" % total)
