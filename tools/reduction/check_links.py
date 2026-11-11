"""Find unresolved internal imports and module attributes without importing code."""
from pathlib import Path
import ast,json
ROOT=Path(__file__).resolve().parents[2]
modules={};trees={}
for base,prefix in [('backend/app','app'),('database/tempo_storage','tempo_storage')]:
 for p in (ROOT/base).rglob('*.py'):
  name=prefix+'.'+str(p.relative_to(ROOT/base))[:-3].replace('/','.')
  if name.endswith('.__init__'):name=name[:-9]
  modules[name]=p;trees[name]=ast.parse(p.read_text())
exports={}
for m,t in trees.items():
 names=set()
 for n in t.body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):names.add(n.name)
  elif isinstance(n,(ast.Import,ast.ImportFrom)):names.update(a.asname or a.name.split('.')[0] for a in n.names)
  elif isinstance(n,(ast.Assign,ast.AnnAssign)):
   targets=n.targets if isinstance(n,ast.Assign) else [n.target]
   names.update(k.id for target in targets for k in ast.walk(target) if isinstance(k,ast.Name))
 exports[m]=names
issues=[]
for m,t in trees.items():
 package=m if modules[m].name=='__init__.py' else m.rpartition('.')[0]
 def inspect(nodes,inherited):
  bindings=dict(inherited)
  for n in nodes:
   if isinstance(n,ast.Import):
    for a in n.names:
     if a.name in modules:bindings[a.asname or a.name]=a.name
   if isinstance(n,ast.ImportFrom):
    base=n.module or ''
    if n.level:base='.'.join(package.split('.')[:len(package.split('.'))-n.level+1]+([base] if base else []))
    if base.startswith(('app','tempo_storage')) and base not in modules and not any(k.startswith(base+'.') for k in modules):issues.append((m,n.lineno,'missing_module',base))
    for a in n.names:
     child=base+'.'+a.name
     if child in modules:bindings[a.asname or a.name]=child
     elif base in modules and a.name not in exports[base] and a.name!='*':issues.append((m,n.lineno,'missing_import',child))
  for n in nodes:
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
    local={k:v for k,v in bindings.items() if k not in {a.arg for a in [*n.args.posonlyargs,*n.args.args,*n.args.kwonlyargs]}}
    inspect(n.body,local)
   elif isinstance(n,ast.ClassDef):inspect(n.body,bindings)
   else:
    for k in ast.walk(n):
     if isinstance(k,ast.Attribute) and isinstance(k.value,ast.Name) and k.value.id in bindings:
      owner=bindings[k.value.id]
      if k.attr not in exports.get(owner,set()) and owner+'.'+k.attr not in modules:issues.append((m,k.lineno,'missing_attribute',owner+'.'+k.attr))
 inspect(t.body,{})
for item in sorted(set(issues)):print(':'.join(map(str,item)))
print(f'{len(set(issues))} unresolved internal source links')
