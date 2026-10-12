"""Static audit inventory. Run with Python; never imports the application."""
import ast
import collections
import json
import pathlib
import re
import sys
import subprocess

root = pathlib.Path(__file__).resolve().parents[3]
subprocess.run(['git', 'diff', '--exit-code', '02b65f229aaccc91ae7c03fd11efc8f3aedd2883', '--', 'backend', 'scripts', 'app', 'tests', '.github'], cwd=root, check=True, stdout=subprocess.DEVNULL)
output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent
output.mkdir(parents=True, exist_ok=True)
modules = {}
for path in sorted((root / 'backend/app').rglob('*.py')):
    module = '.'.join(path.relative_to(root / 'backend').with_suffix('').parts)
    if module.endswith('.__init__'):
        module = module[:-9]
    modules[module] = (path, ast.parse(path.read_text()))
application_modules = set(modules)
for path in sorted((root / 'scripts').rglob('*.py')):
    modules['scripts.' + '.'.join(path.relative_to(root / 'scripts').with_suffix('').parts)] = (path, ast.parse(path.read_text()))
edges, functions, calls, writers, dynamic = [], [], [], [], []
mutation = re.compile(r'\b(?:INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO|UPDATE|DELETE\s+FROM)\s+([a-z_][a-z_0-9]*)', re.I)
class Inventory(ast.NodeVisitor):
    def __init__(self, module, path):
        self.module, self.path = module, str(path.relative_to(root))
        self.scope = []
    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        functions.append({'module':self.module,'file':self.path,'name':'.'.join(self.scope),'start':node.lineno,'end':node.end_lineno})
        self.generic_visit(node)
        self.scope.pop()
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_Import(self, node):
        for alias in node.names:
            self.edge(node, alias.name)
    def visit_ImportFrom(self, node):
        package = self.module.split('.')[:-1]
        if self.path.endswith('/__init__.py'):
            package = self.module.split('.')
        base = '.'.join(package[:len(package)-node.level+1]) if node.level else ''
        target = '.'.join(filter(None,[base,node.module]))
        for alias in node.names:
            candidate = target + '.' + alias.name
            self.edge(node, candidate if candidate in modules else target, alias.name)
    def edge(self,node,target,symbol=None):
        edges.append({'source':self.module,'target':target,'symbol':symbol,'file':self.path,'line':node.lineno,'scope':'.'.join(self.scope) or '<module>'})
    def visit_Call(self,node):
        callee = ast.unparse(node.func)
        calls.append({'module':self.module,'file':self.path,'caller':'.'.join(self.scope) or '<module>','callee':callee,'line':node.lineno,'end':node.end_lineno})
        if callee.endswith('import_module') or callee == '__import__':
            dynamic.append(calls[-1])
        self.generic_visit(node)
    def visit_Constant(self,node):
        if isinstance(node.value,str):
            for match in mutation.finditer(node.value):
                writers.append({'module':self.module,'file':self.path,'function':'.'.join(self.scope) or '<module constant>','start':node.lineno,'end':node.end_lineno,'table':match.group(1).lower(),'sql':node.value})
for module,(path,tree) in modules.items():
    Inventory(module,path).visit(tree)
graph=collections.defaultdict(set)
for edge in edges:
    if edge['target'] in modules: graph[edge['source']].add(edge['target'])
def closure(source):
    reached=set(); pending=list(graph[source])
    while pending:
        target=pending.pop()
        if target not in reached:
            reached.add(target); pending.extend(graph[target]-reached)
    return sorted(reached)
roots={'app.main','app.tasks','app.celery_app'}
reverse=[edge for edge in edges if edge['target'] in roots and edge['source'] in application_modules]
transitive={module: closure(module) for module in modules}
sql=[]
for path in sorted(root.rglob('*.sql')):
    if '.dev-copies' in path.parts: continue
    text=path.read_text()
    for match in mutation.finditer(text):
        sql.append({'file':str(path.relative_to(root)),'line':text[:match.start()].count('\n')+1,'table':match.group(1).lower(),'statement':text[match.start():match.start()+180]})
data={'limitations':['Static syntax graph includes all branches, not actual execution.','Calls are syntactic, not fully resolved; aliases, decorators, SQL constants and dynamic dispatch require manual trace.','SQL writer inventory includes SQL-looking text; manual classification distinguishes executable mutations from constants/comments.','Python graph excludes JS imports; client trace is documented manually.'],'imports':edges,'functions':functions,'calls':calls,'sql_mutation_sites':writers,'migration_mutation_sites':sql,'dynamic_imports':dynamic,'reverse_root_imports':reverse,'transitive_imports':transitive}
(output/'inventory.json').write_text(json.dumps(data,indent=2)+'\n')
lines=['# Reverse composition-root imports','', '| Source | Scope | Target | Location |','|---|---|---|---|']
sha='02b65f229aaccc91ae7c03fd11efc8f3aedd2883'
for edge in reverse:
    link=f"https://github.com/aaweaver-actuary/tempo/blob/{sha}/{edge['file']}#L{edge['line']}"
    lines.append(f"| {edge['source']} | {edge['scope']} | {edge['target']} | [line {edge['line']}]({link}) |")
(output/'reverse-imports.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'modules':len(modules),'imports':len(edges),'functions':len(functions),'calls':len(calls),'sql_mutation_sites':len(writers),'reverse_root_imports':len(reverse),'dynamic_imports':len(dynamic)}))

# Durable, inspectable companion artifacts from the same syntax inventory.
import gzip
(output / 'inventory.json.gz').write_bytes(gzip.compress((output / 'inventory.json').read_bytes(), mtime=0))
dot = ['digraph python_imports {', 'rankdir=LR;']
for edge in edges:
    if edge['target'] in transitive and edge['source'].startswith('app.'):
        dot.append(json.dumps(edge['source']) + ' -> ' + json.dumps(edge['target']) +
                   ' [label=' + json.dumps(str(edge['line']) + ' ' + edge['scope']) + '];')
dot.append('}')
(output / 'imports.dot').write_text('\n'.join(dot) + '\n')

def source_link(path, start, end=None):
    return f'[{path}:{start}–{end or start}](https://github.com/aaweaver-actuary/tempo/blob/{sha}/{path}#L{start}-L{end or start})'

function_index = {(entry['file'], entry['name']): entry for entry in functions}
state_groups = {
    'Review history': ['reviews', 'review_schedule_snapshots', 'opening_card_schedule_seeds'],
    'FSRS / scheduling / progression candidates': ['cards'],
    'Logical review receipts': ['review_attempt_receipts'],
    'Operation receipts': ['operation_receipts'],
    'Daily queue and ordering': ['daily_queue', 'daily_queue_days', 'queue_projection_diagnostics', 'queue_projections'],
    'Queue origins / issuance': ['queue_attempt_origins'],
    'Opening evidence completion / presentation': ['opening_evidence_attempts', 'opening_evidence_events', 'opening_evidence_observations', 'opening_evidence_queue_contexts', 'opening_evidence_presentations'],
    'Priority requests / publication': ['repertoire_priority_jobs', 'analysis_refresh_requests', 'repertoire_priority_publications', 'repertoire_card_priority_generations', 'repertoire_card_introduction_priorities'],
    'Task generations / checkpoints': ['background_tasks', 'background_task_events'],
    'Specialized review attempts': ['study_attempts', 'guided_review_sessions', 'guided_review_attempts', 'defense_attempts'],
}
writer_lines = ['# State-writer inventory', '',
    'Generated from AST SQL literals and SQL migration syntax. This is an exhaustive **syntactic candidate inventory within the scanned source**, not proof that every listed branch executes. Module constants are statements consumed by functions; dynamic SQL and trigger cascades require the report’s manual trace. All `cards` writers are included to avoid falsely treating metadata/reset/deletion helpers as pure. Application writers, historical initialization, maintenance scripts and fixture code are separated. Prepared continuation has no independent table: its persisted carrier is logical/operation result JSON, plus head issuance in retained origins.', '']
for label, tables in state_groups.items():
    writer_lines += ['## ' + label, '', '| Kind | Function / statement | Tables | Definition | SQL sites |', '|---|---|---|---|---|']
    grouped = collections.defaultdict(list)
    for writer in writers:
        if writer['table'] in tables:
            grouped[(writer['file'], writer['function'])].append(writer)
    for (file, name), group in sorted(grouped.items()):
        function = function_index.get((file, name))
        kind = ('script: fixture/maintenance' if file.startswith('scripts/') else
                'historical initialization' if file == 'backend/app/database.py' or name == 'initialize_sqlite_origins' else 'application')
        definition = source_link(file, function['start'], function['end']) if function else 'module SQL constant; inspect call inventory'
        sites = sorted({(writer['start'], writer['end']) for writer in group})
        writer_lines.append(f"| {kind} | `{name}` | {', '.join(sorted({writer['table'] for writer in group}))} | {definition} | " +
                            ', '.join(source_link(file, start, end) for start, end in sites) + ' |')
    writer_lines += ['', '### Migration SQL mutation sites', '']
    for writer in sql:
        if writer['table'] in tables:
            writer_lines.append('- ' + source_link(writer['file'], writer['line']) + ' — `' + writer['table'] + '`')
(output / 'state-writers.md').write_text('\n'.join(writer_lines) + '\n')
