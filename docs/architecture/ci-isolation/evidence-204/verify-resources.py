import json, subprocess
from pathlib import Path
root=Path(__file__).parent
projects=set()
for path in (root/"local-resources").rglob("ownership.json"):
    projects.add(json.loads(path.read_text())["project"])
for path in [root/"native-focused-1/native-provenance.json", root/"native-focused-2/native-provenance.json", root/"refresh-main-baseline/native-provenance.json"]:
    projects.add(json.loads(path.read_text())["project"])
def run(*args):
    return subprocess.check_output(["docker",*args],text=True).strip().splitlines()
result={"projects":sorted(projects),"remaining":{},"mounted_checkout_containers":[]}
for project in sorted(projects):
    result["remaining"][project]={
      "containers":run("ps","-aq","--filter",f"label=com.docker.compose.project={project}"),
      "volumes":run("volume","ls","-q","--filter",f"label=com.docker.compose.project={project}"),
      "networks":run("network","ls","-q","--filter",f"label=com.docker.compose.project={project}"),
      "tagged_images":run("image","ls","--format","{{.Repository}}:{{.Tag}} {{.ID}}", "--filter", f"reference={project}*")}
ids=run("ps","-aq")
if ids:
    for container in json.loads(subprocess.check_output(["docker","inspect",*ids],text=True)):
        if any("/issue-204-scheduling-store" in mount.get("Source","") for mount in container.get("Mounts",[])):
            result["mounted_checkout_containers"].append({"id":container["Id"],"name":container["Name"]})
result["success"]=not result["mounted_checkout_containers"] and all(not items for group in result["remaining"].values() for items in group.values())
(root/"resource-cleanup-verification.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result,indent=2))
