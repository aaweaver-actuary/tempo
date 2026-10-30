"""Qualification allocations; central budget owns selection and admission."""
import json
import math
import os
from pathlib import Path
import subprocess


def positive_cpu(value):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("CPU allocation must be positive and finite") from error
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError("CPU allocation must be positive and finite")
    quota = number * 100000
    if not quota.is_integer() or quota > 2**53 - 1:
        raise ValueError("CPU allocation requires an exact CPU quota")
    return number


def optional_cpu(value):
    return None if value is None else positive_cpu(value)


def cpu_arguments(value):
    return [] if value is None else ["--cpus", str(positive_cpu(value))]


def cpu_environment(helper, total):
    arguments = []
    for name, value in (("HELPER_CPUS", helper), ("CPUS", total)):
        if value is not None:
            arguments += ["--env", "TEMPO_QUALIFICATION_" + name + "=" + str(positive_cpu(value))]
    return arguments


def cpu_nanos(value):
    return 0 if value is None else round(positive_cpu(value) * 1_000_000_000)


def verify_cpu_max(text, expected):
    quota, period = text.split()
    if int(period) <= 0:
        return False
    if expected is None:
        return quota == "max"
    return quota != "max" and int(quota) > 0 and abs(int(quota) / int(period) - positive_cpu(expected)) <= 1 / int(period)


def selected_cpus(value=None):
    value = value if value is not None else os.environ.get("TEMPO_QUALIFICATION_CPUS")
    if value is None:
        return None
    options = {"cpus": positive_cpu(value)}
    budget = (Path(__file__).resolve().parent / "development-budget.mjs").as_uri()
    script = ("import {readDevelopmentCpuAllocation} from " + json.dumps(budget)
              + "; console.log(JSON.stringify(await readDevelopmentCpuAllocation("
              + json.dumps(options) + "))); ")
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            capture_output=True, text=True, check=True, timeout=15)
    allocation = positive_cpu(json.loads(result.stdout))
    print(json.dumps({"stage": "qualification-cpu-allocation", "cpus": allocation,
                      "source": "selected-docker-daemon" if value is None else "explicit-caller-allocation"}), flush=True)
    return allocation


def require_managed_docker():
    if not os.environ.get("TEMPO_DEVELOPMENT_BUDGET_TOKEN") or not os.environ.get("TEMPO_REAL_DOCKER"):
        raise ValueError("Use the managed qualification window; a CPU choice is not resource admission")


def partition_cpus(total, weights):
    """Divide a selected total, retaining the existing fixture-role proportions."""
    # Match the central reader's exact 100000-period CPU quota precision.
    if total is None:
        return {name: None for name in weights}
    total_quota = round(positive_cpu(total) * 100000)
    remaining = total_quota
    result = {}
    items = list(weights.items())
    for index, (name, weight) in enumerate(items):
        quota = remaining if index == len(items) - 1 else total_quota * weight // sum(weights.values())
        if quota <= 0:
            raise ValueError("CPU allocation is too small for the selected fixture roles")
        result[name] = quota / 100000
        remaining -= quota
    return result
