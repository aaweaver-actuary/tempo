"""Package-local wheel/sdist install proof using standard tools and owning tests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SERVICE=Path(__file__).resolve().parents[1]
ROOT=SERVICE.parents[1]


def main():
    destination=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else SERVICE/"test-results"/"artifacts"
    destination.mkdir(parents=True,exist_ok=True)
    environment={key:value for key,value in os.environ.items()
                 if not key.startswith(("PG","TEMPO_","PYTHONPATH","VIRTUAL_ENV","PYTHONHOME","PYTEST_"))}
    environment.update(UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR",str(Path(tempfile.gettempdir())/"tempo-game-evidence-41-uv-cache")),
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    report={"complete":False,"commands":[],"artifacts":{},"cases":{},"source_inputs":{}}
    def run(arguments, cwd, label):
        report["commands"].append({"label":label,"argv":[str(arg) for arg in arguments]})
        result=subprocess.run([str(arg) for arg in arguments],cwd=cwd,env=environment,capture_output=True,text=True)
        (destination/(label+".log")).write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(label+" failed; see its log")
        return result.stdout
    try:
        report["revision"]=run(["git","rev-parse","HEAD"],ROOT,"revision").strip()
        report["dirty"]=run(["git","status","--short"],ROOT,"source-status").strip()
        for relative in ["services/game-evidence","packages/chess-core","contracts/content-publication","contracts/game-evidence"]:
            for path in (ROOT/relative).rglob("*"):
                if path.is_file() and not any(part in {".venv","__pycache__","test-results","dist",".pytest_cache"} for part in path.parts):
                    report["source_inputs"][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="tempo-game-evidence-artifacts-") as temporary:
            workspace=Path(temporary)
            distributions=destination/"distributions"
            distributions.mkdir(exist_ok=True)
            # Each build contains only its owning package's declared source.
            for label,source in [("chess-core",ROOT/"packages/chess-core"),("content-contract",ROOT/"contracts/content-publication"),
                                 ("evidence-contract",ROOT/"contracts/game-evidence"),("service",SERVICE)]:
                copied=workspace/(label+"-build")
                shutil.copytree(source,copied,ignore=shutil.ignore_patterns(".venv","__pycache__","test-results","dist",".pytest_cache"))
                run([sys.executable,"-m","build","--no-isolation","--outdir",distributions],copied,label+"-build")
            requirements=workspace/"requirements.txt"
            requirements.write_text(run(["uv","export","--locked","--no-emit-project","--no-emit-local","--no-hashes","--no-header"],SERVICE,"locked-requirements"))
            dependencies=[path for path in distributions.glob("*.whl") if not path.name.startswith("tempo_game_evidence-0")]
            service_wheel=next(distributions.glob("tempo_game_evidence-0*.whl"))
            service_sdist=next(distributions.glob("tempo_game_evidence-0*.tar.gz"))
            for path in distributions.iterdir():
                report["artifacts"][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
            for version in ("3.12","3.14"):
                for kind,artifact in (("wheel",service_wheel),("sdist",service_sdist)):
                    label="python-"+version+"-"+kind
                    consumer=workspace/label
                    consumer.mkdir()
                    run(["uv","venv","--python",version,consumer/".venv"],workspace,label+"-venv")
                    python=consumer/".venv/bin/python"
                    run(["uv","pip","install","--python",python,"-r",requirements,*dependencies],consumer,label+"-dependencies")
                    run(["uv","pip","install","--python",python,"--no-build-isolation",artifact],consumer,label+"-install")
                    shutil.copytree(SERVICE/"tests",consumer/"tests",ignore=shutil.ignore_patterns("__pycache__"))
                    shutil.copytree(SERVICE/"fixtures",consumer/"fixtures")
                    guard="import pathlib,sys; import tempo_game_evidence as p; assert 'site-packages' in p.__file__; assert pathlib.Path(p.__file__).with_name('migrations').joinpath('001.sql').is_file(); assert not any('backend' in x for x in sys.path)"
                    run([python,"-I","-c",guard],consumer,label+"-installed-guard")
                    junit=destination/(label+".xml")
                    run([python,"-I","-m","pytest","tests","--import-mode=importlib","-q","--junitxml="+str(junit)],consumer,label+"-tests")
                    cases=ET.parse(junit).getroot().findall(".//testcase")
                    if not cases or any(case.find(tag) is not None for case in cases for tag in ("failure","error","skipped")):
                        raise RuntimeError("missing_or_nonpassing_installed_cases")
                    report["cases"][label]=len(cases)
                    run([consumer/".venv/bin/tempo-game-evidence","--help"],consumer,label+"-cli")
        report["complete"]=True
    except Exception as error:
        report["error"]=str(error)
        print(report["error"],file=sys.stderr)
    finally:
        (destination/"artifacts.json").write_text(json.dumps(report,indent=2)+"\n")
    return 0 if report["complete"] else 1


if __name__=="__main__":
    raise SystemExit(main())
