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

files=[l.strip() for l in open('/home/alfred/.claude/jobs/222b9b69/tmp/backport.txt') if l.strip()]
tot=0
for f in files:
    p=f"/tmp/erpnext-1633/{f}"
    if not pathlib.Path(p).exists(): continue
    r=check(p)
    if r:
        print(f"  ❌ {f}")
        for nm,ln in r[:6]: print(f"       L{ln}  {nm}")
        tot+=len(r)
print(f"\nnombres potencialmente indefinidos: {tot}")
