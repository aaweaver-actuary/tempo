"""AST boundary validation: includes conditional/function-local imports, without importing code."""
import ast
import json
import re
import tomllib
from pathlib import Path
import sys


def boundary_facts(root, files):
    """Source facts for the existing component guard; never import application code."""
    root = Path(root).resolve()
    results = []
    for name in files:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"{name}: source escape")
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        imported, aliases, constants = [], {}, {}
        def module_binding(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    return False
            return True
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append(dict(module=alias.name, level=0, names=[], line=node.lineno))
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                imported.append(dict(module=node.module or '', level=node.level,
                                     names=[alias.name for alias in node.names], line=node.lineno,
                                     bindings=[dict(name=alias.name, local=alias.asname or alias.name,
                                                    module_level=module_binding(node)) for alias in node.names]))
                for alias in node.names:
                    aliases[alias.asname or alias.name] = '.' * node.level + '.'.join(filter(None, [node.module, alias.name]))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        constants.setdefault(target.id, []).append(node.value)

        def type_names(annotation):
            if annotation is None:
                return set()
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                try: annotation = ast.parse(annotation.value, mode='eval')
                except SyntaxError: return set()
            def qualified(node):
                if isinstance(node, ast.Name): return aliases.get(node.id, node.id)
                if isinstance(node, ast.Attribute): return qualified(node.value) + '.' + node.attr
                return ''
            if isinstance(annotation, (ast.Name, ast.Attribute)):
                return {qualified(annotation)}
            if isinstance(annotation, ast.Subscript):
                arguments = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
                kind = qualified(annotation.value).split('.')[-1]
                # Annotated metadata/validators and Literal values are not
                # unpublished parameter/return types.
                arguments = arguments[:1] if kind == 'Annotated' else [] if kind == 'Literal' else arguments
                return type_names(annotation.value) | set().union(*(type_names(item) for item in arguments))
            if isinstance(annotation, ast.Call):
                return type_names(annotation.func)
            return set().union(*(type_names(child) for child in ast.iter_child_nodes(annotation)))

        def signature_types(node):
            annotations = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotations = [arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
                annotations += [node.returns, node.args.vararg.annotation if node.args.vararg else None,
                                node.args.kwarg.annotation if node.args.kwarg else None]
            elif isinstance(node, ast.ClassDef):
                annotations = list(node.bases) + [child.annotation for child in node.body if isinstance(child, ast.AnnAssign)]
                return sorted(set().union(*(type_names(annotation) for annotation in annotations),
                    *(set(signature_types(child)) for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and (not child.name.startswith('_') or child.name == '__init__'))))
            elif isinstance(node, ast.AnnAssign):
                annotations = [node.annotation]
            elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Subscript, ast.BinOp, ast.Attribute)):
                annotations = [node.value]
            return sorted(set().union(*(type_names(annotation) for annotation in annotations)))

        symbols = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols[node.name] = dict(types=signature_types(node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name): symbols[target.id] = dict(types=signature_types(node))

        def string_value(node, seen=()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id not in seen and len(constants.get(node.id, [])) == 1:
                return string_value(constants[node.id][0], (*seen, node.id))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                left, right = string_value(node.left, seen), string_value(node.right, seen)
                return left + right if left is not None and right is not None else None
            if isinstance(node, ast.JoinedStr) and all(isinstance(item, ast.Constant) for item in node.values):
                return ''.join(str(item.value) for item in node.values)
            return None

        def dotted(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return dotted(node.value) + '.' + node.attr
            return '<dynamic>'

        calls, references, functions, loaders = [], [], [], []
        class Visitor(ast.NodeVisitor):
            def __init__(self): self.scope = []
            def enter(self, node):
                self.scope.append(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append('.'.join(self.scope))
                self.generic_visit(node)
                self.scope.pop()
            visit_FunctionDef = enter
            visit_AsyncFunctionDef = enter
            visit_ClassDef = enter
            def visit_Call(self, node):
                callee = dotted(node.func)
                if callee.startswith('self.') and len(self.scope) > 1:
                    callee = self.scope[0] + callee[4:]
                call = dict(callee=callee, function='.'.join(self.scope), line=node.lineno)
                # Comparing frozen fact fields does not invoke a capability.
                if callee == 'getattr' and isinstance(parents.get(node), ast.Compare):
                    call['data_access'] = True
                if callee.split('.')[-1] in {'execute', 'execute_native', 'executemany', 'executescript', 'query', 'prepare'} and node.args:
                    value = string_value(node.args[0])
                    database_receiver = set(callee.lower().split('.')) & {'db', 'database', 'connection', 'conn', 'cursor', 'pool', 'writer', 'sqlite3', 'psycopg'}
                    if database_receiver or callee.split('.')[-1] in {'execute_native', 'executemany', 'executescript'} or value is not None and re.search(r'\b(?:SELECT|WITH|INSERT|UPDATE|DELETE|TRUNCATE|MERGE|REPLACE|COPY|CREATE|ALTER|DROP)\b', value, re.I):
                        call['sql'] = value
                calls.append(call)
                self.generic_visit(node)
            def visit_Name(self, node):
                if node.id in {'__import__', 'eval', 'exec'}: loaders.append(node.lineno)
            def visit_Attribute(self, node):
                if node.attr in {'import_module', 'exec_module', 'load_module'}: loaders.append(node.lineno)
                references.append(dict(callee=dotted(node), function='.'.join(self.scope), line=node.lineno))
                self.generic_visit(node)
        Visitor().visit(tree)
        exports = None
        if '__all__' in constants:
            try:
                exports = ast.literal_eval(constants['__all__'][0]) if len(constants['__all__']) == 1 else None
            except (ValueError, TypeError): pass
        results.append(dict(path=name, imports=imported, calls=calls, functions=functions,
                            references=references, exports=exports, symbols=symbols, loaders=sorted(set(loaders))))
    return results


def check_python(root, dependencies, exports, artifact=None, build_inputs=()):
    root = Path(root).resolve()
    sources = root / "src"
    allowed_external = set(dependencies)
    standard_library = set(sys.stdlib_module_names)
    package_directories = [path for path in sources.iterdir() if path.is_dir()]
    if len(package_directories) != 1:
        raise ValueError("Expected one owned import root")
    package = package_directories[0]
    owned_packages = {package.name: package}
    for item in build_inputs:
        candidate = (root / item).resolve()
        if candidate.is_dir() and candidate.parent.name == "database" and (candidate / "__init__.py").is_file():
            owned_packages[candidate.name] = candidate
    problems = []
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    if artifact and any(metadata["project"][key] != artifact[key] for key in ("name", "version")):
        problems.append("Python artifact identity differs from manifest")
    declared_dependencies = {re.split(r"[<>=!~;\[]", requirement)[0].replace("-", "_") for requirement in metadata["project"].get("dependencies", [])}
    if declared_dependencies != allowed_external:
        problems.append("Dependencies differ from manifest")
    source_files = sorted(source for owner in owned_packages.values() for source in owner.rglob("*.py"))
    for source in source_files:
        owner = next(owner for owner in owned_packages.values() if source.is_relative_to(owner))
        if source.is_symlink() or not source.resolve().is_relative_to(owner):
            problems.append(f"{source}: source escape")
            continue
        tree = ast.parse(source.read_text())
        module_parts = (owner.name, *source.relative_to(owner).with_suffix("").parts)
        current_package = module_parts[:-1]
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = current_package[:len(current_package) - node.level + 1]
                    if not parent:
                        problems.append(f"{source}:{node.lineno}: relative source escape")
                        continue
                    imported = [".".join((*parent, *(node.module or "").split("."))).rstrip(".")]
                else:
                    imported = [node.module or ""]
            if isinstance(node, ast.Name) and node.id in {"__import__", "eval", "exec"}:
                problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in {"import_module", "exec_module", "load_module"}:
                problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {node.attr}")
            for imported_module in imported:
                import_root = imported_module.split(".")[0]
                if import_root in {"importlib", "runpy"}:
                    problems.append(f"{source}:{node.lineno}: unresolved dynamic loader {imported_module}")
                if import_root in owned_packages:
                    module_path = owned_packages[import_root].joinpath(*imported_module.split(".")[1:])
                    if not module_path.with_suffix(".py").is_file() and not (module_path / "__init__.py").is_file():
                        problems.append(f"{source}:{node.lineno}: unavailable local module {imported_module}")
                elif import_root not in standard_library and import_root not in allowed_external:
                    problems.append(f"{source}:{node.lineno}: undeclared dependency {imported_module}")
    public_tree = ast.parse((package / "__init__.py").read_text())
    declared_exports = next((ast.literal_eval(node.value) for node in public_tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)), None)
    if declared_exports != exports:
        problems.append("Python public exports differ from manifest")
    if problems:
        raise ValueError("\n".join(problems))
    return {"files": len(source_files), "exports": declared_exports, "package": package.name}


def check_consumer(source, package_name, exports):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(package_name + "."):
                    raise ValueError("Private Python implementation import: " + alias.name)
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == package_name or node.module.startswith(package_name + ".")):
            if node.module != package_name or any(alias.name not in exports for alias in node.names):
                raise ValueError("Private/unavailable Python export: " + node.module)

if __name__ == "__main__":
    try:
        if sys.argv[1] == "boundary-facts":
            print(json.dumps(boundary_facts(sys.argv[2], json.load(sys.stdin))))
        elif sys.argv[1] == "consumers":
            for source_path in Path(sys.argv[2]).rglob("*.py"):
                check_consumer(source_path.read_text(), sys.argv[3], json.loads(sys.argv[4]))
            print(json.dumps({"consumers": "valid"}))
        elif sys.argv[1] == "consumer":
            check_consumer(Path(sys.argv[2]).read_text(), sys.argv[3], json.loads(sys.argv[4]))
            print(json.dumps({"consumer": "valid"}))
        else:
            print(json.dumps(check_python(sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3]), json.loads(sys.argv[4]) if len(sys.argv) > 4 else None, json.loads(sys.argv[5]) if len(sys.argv) > 5 else ())))
    except (ValueError, SyntaxError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
