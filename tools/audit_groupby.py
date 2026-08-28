import ast, re, pathlib
AGG={"Sum","Max","Min","Count","Avg","GroupConcat","Coalesce","IfNull","ConstantColumn",
     "Case","Cast","Abs","Round","Extract","Concat","Locate","Sqrt","Floor","Ceil","Date","Timestamp"}
ATTR=re.compile(r'^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$')

def seg(src,n):
    try: return (ast.get_source_segment(src,n) or "").strip()
    except Exception: return ""
def has_agg(n):
    for d in ast.walk(n):
        if isinstance(d,ast.Call):
            f=d.func; nm=f.attr if isinstance(f,ast.Attribute) else (f.id if isinstance(f,ast.Name) else "")
            if nm in AGG: return True
    return False

def spine(call):
    """calls del receiver chain hacia abajo desde `call`"""
    out=[];cur=call
    while isinstance(cur,ast.Call):
        out.append(cur)
        f=cur.func
        cur=f.value if isinstance(f,ast.Attribute) else None
    return out

def analyze_file(path,src,tree,res):
    parent={}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): parent[c]=n
    aggvars=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Assign) and has_agg(n.value):
            for t in n.targets:
                if isinstance(t,ast.Name): aggvars.add(t.id)

    # localizar cada groupby y subir al Call mas externo de SU cadena
    for n in ast.walk(tree):
        if not isinstance(n,ast.Call): continue
        f=n.func
        nm=f.attr if isinstance(f,ast.Attribute) else ""
        if nm not in ("groupby","group_by"): continue
        top=n
        while True:
            p=parent.get(top)
            if isinstance(p,ast.Attribute) and parent.get(p).__class__ is ast.Call and parent[p].func is p:
                top=parent[p]; continue
            break
        calls=spine(top)
        grps=set(); pk=set(); sels=[]
        for c in calls:
            cf=c.func; cn=cf.attr if isinstance(cf,ast.Attribute) else ""
            if cn in ("groupby","group_by"):
                for a in c.args:
                    s=seg(src,a).strip('"\' ')
                    for part in re.split(r'\s*,\s*',s):
                        p2=part.strip().strip('"\'`')
                        if not p2: continue
                        m=ATTR.match(p2)
                        if m:
                            grps.add(m.group(2))
                            if m.group(2)=="name": pk.add(m.group(1))
                        else:
                            col=p2.split('.')[-1]; grps.add(col)
                            if col=="name": pk.add("*")
            elif cn=="select":
                for a in c.args: sels.append((c.lineno,a))
        if not grps: continue
        for ln,a in sels:
            if has_agg(a) or isinstance(a,(ast.Dict,ast.Starred)): continue
            if isinstance(a,ast.Name):
                if a.id in aggvars: continue
                res.append((path,ln,a.id,sorted(grps))); continue
            s=seg(src,a)
            if not s or "(" in s or "*" in s: continue
            base=re.sub(r'\s+as\s+\w+$','',s.strip().strip('"\'')).strip('`')
            m=ATTR.match(base)
            if m:
                if m.group(1) in pk or "*" in pk or m.group(2) in grps: continue
                res.append((path,ln,base,sorted(grps)))
            elif re.match(r'^\w+$',base):
                if "*" in pk or base in grps: continue
                res.append((path,ln,base,sorted(grps)))

# ---- raw SQL ----
def raw_sql(path,src,res2):
    for m in re.finditer(r'select\s+(.+?)\s+from\s+(.+?)\s+group\s+by\s+([^;"\']*?)(?:\s+order\s+by|\s+having|\s*""")',src,re.I|re.S):
        sel,frm,grp=m.group(1),m.group(2),m.group(3)
        if len(sel)>2000: continue
        ln=src[:m.start()].count('\n')+1
        gset=set(); pk=set()
        for x in grp.split(','):
            x=x.strip().strip('`')
            if not x: continue
            col=x.split('.')[-1].strip('`'); gset.add(col)
            if col=="name":
                pk.add(x.split('.')[0].strip('`') if '.' in x else "*")
        bad=[]
        for item in re.split(r',(?![^(]*\))',sel):
            it=item.strip()
            if not it or '(' in it or '*' in it or '%' in it: continue
            it=re.sub(r'\s+as\s+[\w`]+$','',it,flags=re.I).strip().strip('`')
            if not re.match(r'^[\w`]+(\.[\w`]+)?$',it): continue
            tbl=it.split('.')[0].strip('`') if '.' in it else None
            col=it.split('.')[-1].strip('`')
            if "*" in pk or (tbl and tbl in pk) or col in gset: continue
            bad.append(it)
        if bad: res2.append((path,ln,bad,grp.strip()))

res=[];res2=[]
files=[p for p in pathlib.Path('/tmp/erpnext/erpnext').rglob('*.py') if not p.name.startswith('test_')]
for p in files:
    try: src=p.read_text(); tree=ast.parse(src)
    except Exception: continue
    rel=str(p).replace('/tmp/erpnext/','')
    analyze_file(rel,src,tree,res); raw_sql(rel,src,res2)

print(f"archivos: {len(files)}")
print(f"\n===== QUERY BUILDER: {len(res)} candidatos =====")
for r in res: print(f"{r[0]}:{r[1]}  {r[2]}   groupby={r[3]}")
print(f"\n===== RAW SQL: {len(res2)} candidatos =====")
for r in res2: print(f"{r[0]}:{r[1]}  cols={r[2]}   GROUP BY {r[3]}")
