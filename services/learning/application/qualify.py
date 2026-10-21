"""Installed proof from declared inputs, using the existing boundary checker."""
from pathlib import Path
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from qualification import controlled_environment, junit_destination, validate_inventory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INPUTS = ("pyproject.toml","uv.lock","src","README.md","REGRESSIONS.md","tests","qualify.py","qualification.py",
          "hatch_build.py","integration","run_suite.py",
          "database/tempo_legacy_database/tooling/learning_review",
          "database/tempo_legacy_database/tests/learning_review")


def main():
    junit_destination(os.environ.get("PYTEST_ADDOPTS", ""))
    environment = controlled_environment()
    environment.pop("PYTHONPATH", None)
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    report = {"revision":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
              "source_inputs":{},"commands":[],"consumers":{},"complete":False}
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    def run(arguments,cwd,expected=0):
        completed = subprocess.run([str(value) for value in arguments],cwd=cwd,
            capture_output=True,text=True,env=environment)
        index = len(report["commands"])
        (destination/f"command-{index:02d}.log").write_text(completed.stdout+completed.stderr)
        report["commands"].append({"command":[str(value) for value in arguments],"cwd":str(cwd),
                                   "exit_code":completed.returncode,"expected_exit":expected})
        if completed.returncode != expected:
            raise RuntimeError(f"Command {index} exited {completed.returncode}; see command-{index:02d}.log")
        return completed.stdout
    try:
        with tempfile.TemporaryDirectory(prefix="tempo224-installed-") as directory:
            temporary = Path(directory)
            build = temporary/"build"
            build.mkdir()
            for name in INPUTS:
                source = HERE/name
                if name.startswith("database/") and not source.is_dir():
                    source = ROOT/name
                if source.is_dir():
                    # Never copy interpreter caches or test-run artifacts.
                    shutil.copytree(source,build/name,ignore=shutil.ignore_patterns("__pycache__",".pytest_cache"))
                else:
                    shutil.copy2(source,build/name)
            for path in sorted(build.rglob("*")):
                if path.is_file(): report["source_inputs"][str(path.relative_to(build))] = digest(path)
            tree = ast.parse((build/"src/tempo_learning_review/__init__.py").read_text())
            exports = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(target,ast.Name) and target.id == "__all__" for target in node.targets))
            run([sys.executable,ROOT/"tools/component-build/check-python.py",build,"[]",json.dumps(exports)],build)
            run(["uv","sync","--locked","--no-editable","--python","3.14"],build)
            run([build/".venv/bin/python","-m","build","--no-isolation"],build)
            requirements = run(["uv","export","--locked","--no-emit-project","--no-hashes"],build)
            (temporary/"requirements.txt").write_text(requirements)
            wheels = list((build/"dist").glob("*.whl"))
            if len(wheels) != 1: raise RuntimeError("Expected exactly one wheel")
            artifacts = destination/"artifacts"
            artifacts.mkdir(exist_ok=True)
            report["artifacts"] = {}
            for artifact in (build/"dist").iterdir():
                shutil.copy2(artifact,artifacts/artifact.name)
                report["artifacts"][artifact.name] = digest(artifact)
            shutil.rmtree(build/"src")
            for version in ("3.12","3.14"):
                consumer = temporary/("consumer-"+version)
                consumer.mkdir()
                python = consumer/".venv/bin/python"
                run(["uv","venv","--python",version,consumer/".venv"],consumer)
                run(["uv","pip","install","--python",python,"-r",temporary/"requirements.txt",wheels[0]],consumer)
                shutil.copytree(build/"tests",consumer/"tests")
                shutil.copy2(build/"qualification.py", consumer/"qualification.py")
                shutil.copy2(build/"pyproject.toml", consumer/"pyproject.toml")
                guard = '''import sys
from importlib.abc import MetaPathFinder
class RejectServices(MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'app','fastapi','celery','psycopg','psycopg_pool','redis','httpx','sqlite3','chess','fsrs','torch','onnxruntime','transformers','openai'}:
            raise AssertionError('Unexpected runtime dependency: '+fullname)
sys.meta_path.insert(0,RejectServices())
def reject_connections(event,arguments):
    if event in {'socket.connect','socket.bind','sqlite3.connect'}:
        raise AssertionError('Unexpected service initialization: '+event)
sys.addaudithook(reject_connections)
import tempo_learning_review as review
assert 'site-packages' in review.__file__
assert all(REPOSITORY not in path for path in sys.path)
import runpy
runpy.run_path(HELPER, run_name="__main__")
'''.replace("REPOSITORY",repr(str(ROOT))).replace("HELPER", repr(str(consumer/"qualification.py")))
                junit = destination/("installed-"+version+".xml")
                collection = destination/("collected-"+version+".json")
                execution = destination/("executed-"+version+".json")
                arguments = ["-c", str(consumer/"pyproject.toml"), "-o", "addopts=", "-p", "no:cacheprovider", "tests"]
                run([python,"-I","-c",guard,collection,*arguments,"--collect-only","-q"],consumer)
                run([python,"-I","-c",guard,execution,*arguments,"-v","--junitxml="+str(junit)],consumer)
                identities = validate_inventory(json.loads(collection.read_text()), json.loads(execution.read_text()), junit)
                report["consumers"][version] = {"runtime":run([python,"--version"],consumer).strip(),
                    "passed":len(identities),"identities":identities,
                    "dependencies":run(["uv","pip","freeze","--python",python],consumer).splitlines()}
                run([python,"-I","-m","pytest","tests","-k","no_such_required_identity","-q"],consumer,expected=5)
            report["complete"] = True
    finally:
        report["temporary_workspace_removed"] = not Path(locals().get("directory","/unlikely224")).exists()
        (destination/"installed.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"complete":report["complete"],"passed":{version:data["passed"] for version,data in report["consumers"].items()}}))


if __name__ == "__main__": main()
