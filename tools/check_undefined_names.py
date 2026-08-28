"""Report names loaded but never bound in a Python file.

A missing import is a runtime NameError, not a syntax error, so py_compile will
not catch it. Run this against the patched tree and against an unpatched one:
only names that appear in the first and not the second were introduced.
"""

import ast, builtins, pathlib, sys
BUILT=set(dir(builtins))
def check(path):
    src=pathlib.Path(path).read_text()
    tree=ast.parse(src)
    bound=set()
    for n in ast.walk(tree):
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            for a in n.names: bound.add((a.asname or a.name).split('.')[0])
        elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): bound.add(n.name)
        elif isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)): bound.add(n.id)
        elif isinstance(n,ast.arg): bound.add(n.arg)
        elif isinstance(n,ast.ExceptHandler) and n.name: bound.add(n.name)
        elif isinstance(n,ast.Global): bound.update(n.names)
        elif isinstance(n,ast.alias): bound.add((n.asname or n.name).split('.')[0])
    bad=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load):
            if n.id not in bound and n.id not in BUILT: bad.add((n.id,n.lineno))
    return sorted(bad)

if len(sys.argv) < 2:
    sys.exit("usage: check_undefined_names.py <erpnext-app-dir> [file ...]\n"
             "Reports Name loads that are never bound in the file. Compare the output\n"
             "against an unpatched checkout: only names the patch ADDS are a problem.")
root = pathlib.Path(sys.argv[1])
targets = sys.argv[2:] or [str(x.relative_to(root)) for x in root.rglob("*.py")
                           if not x.name.startswith("test_")]
tot=0
for f in targets:
    p = root / f
    if not p.exists(): continue
    r=check(p)
    if r:
        print(f"  ❌ {f}")
        for nm,ln in r[:6]: print(f"       L{ln}  {nm}")
        tot+=len(r)
print(f"\nnombres potencialmente indefinidos: {tot}")
