"""Reproducible tracked-source/import census; no runtime imports or database access."""
import ast, csv, json, subprocess, sys
from collections import defaultdict
from pathlib import Path
root=Path(__file__).resolve().parents[2]
ref=sys.argv[1] if len(sys.argv)>1 else 'HEAD'
paths=subprocess.check_output(['git','ls-tree','-r','--name-only',ref],cwd=root,text=True).splitlines()
source={'.py','.ts','.tsx','.js','.jsx','.mjs','.cjs','.rs','.html','.sql','.css','.sh','.toml','.yaml','.yml','.json','.md'}
files=[];modules={};trees={}
for p in paths:
    if Path(p).suffix not in source: continue
    try: data=subprocess.check_output(['git','show',f'{ref}:{p}'],cwd=root).decode()
    except (UnicodeDecodeError,subprocess.CalledProcessError): continue
    category='audit' if p.startswith('docs/architecture/reduction-pass2/') else 'tests' if '/test' in p or p.startswith(('tests/','test/')) else 'docs' if p.endswith('.md') or p.startswith('docs/') else 'runtime'
    files.append({'path':p,'category':category,'lines':len(data.splitlines())})
    if not p.endswith('.py') or category!='runtime':continue
    mod=(p.split('/src/',1)[1] if '/src/' in p else p)[:-3].replace('/','.')
    for prefix in ('backend.','database.'):
        if mod.startswith(prefix):mod=mod[len(prefix):]
    if mod.endswith('.__init__'):mod=mod[:-9]
    modules[mod]=p
    try:trees[mod]=ast.parse(data)
    except SyntaxError:pass
edges=set()
for mod,tree in trees.items():
    package=mod if modules[mod].endswith('__init__.py') else mod.rpartition('.')[0]
    for n in ast.walk(tree):
        targets=[]
        if isinstance(n,ast.Import):targets=[a.name for a in n.names]
        elif isinstance(n,ast.ImportFrom):
            base=n.module or ''
            if n.level:base='.'.join(package.split('.')[:len(package.split('.'))-n.level+1]+([base] if base else []))
            targets=[base]+[base+'.'+a.name for a in n.names]
        elif isinstance(n,ast.Call) and ((isinstance(n.func,ast.Attribute) and n.func.attr=='import_module') or (isinstance(n.func,ast.Name) and n.func.id=='__import__')) and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str):targets=[n.args[0].value]
        for target in targets:
            while target and target not in modules: target=target.rpartition('.')[0]
            if target and target!=mod:edges.add((mod,target))
adj=defaultdict(set)
for a,b in edges:adj[a].add(b)
index={};low={};stack=[];active=set();comps=[]
def visit(v):
    index[v]=low[v]=len(index);stack.append(v);active.add(v)
    for w in adj[v]:
        if w not in index:visit(w);low[v]=min(low[v],low[w])
        elif w in active:low[v]=min(low[v],index[w])
    if low[v]==index[v]:
        c=[]
        while True:
            w=stack.pop();active.remove(w);c.append(w)
            if w==v:break
        if len(c)>1:comps.append(sorted(c))
for m in modules:
    if m not in index:visit(m)
comps.sort(key=len,reverse=True)
result={'ref':ref,'graph_scope':'Tracked runtime/library Python; includes function-local and literal dynamic imports; src package roots normalized; computed imports and JavaScript graph excluded','files':files,'totals':{c:{'files':sum(f['category']==c for f in files),'lines':sum(f['lines'] for f in files if f['category']==c)} for c in ('runtime','tests','docs','audit')},'graph':{'modules':len(modules),'edges':len(edges),'cyclic_components':len(comps),'largest_component_size':len(comps[0]) if comps else 0,'components':comps},'legacy_namespace_imports':[list(e) for e in sorted(edges) if e[1].startswith('tempo_legacy_database') and not e[0].startswith('tempo_legacy_database')],'storage_namespace_imports':[list(e) for e in sorted(edges) if e[1].startswith('tempo_storage') and not e[0].startswith('tempo_storage')],'database_reverse_imports':[list(e) for e in sorted(edges) if e[0].startswith(('tempo_storage','tempo_legacy_database')) and e[1].startswith('app.')],'entrypoints':[p for p in paths if p in ['backend/app/main.py','backend/app/tasks.py','backend/app/celery_app.py'] or p.startswith('backend/app/command')],'edges':[list(e) for e in sorted(edges)]}
json.dump(result,sys.stdout,separators=(',',':'));print()
