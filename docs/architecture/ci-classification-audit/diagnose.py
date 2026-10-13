#!/usr/bin/env python3
"""Read-only failed-job diagnostics; preserve logs separately from accounting."""
import argparse
import gzip
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from audit import Evidence, PREFIX, write_json, csv_rows


def diagnose(evidence, output):
    jobs = json.loads((output / 'jobs.json').read_text())
    attempts = json.loads((output / 'attempts.json').read_text())
    def inspect(job):
        failures = [step['name'] for step in job['steps'] if step['conclusion'] == 'failure']
        row = {key: job[key] for key in ('pr','primary','run_id','attempt','id','name','conclusion','url')}
        row['failed_steps'] = failures
        try:
            destination = output / 'logs' / f'{job["id"]}.txt.gz'
            log = gzip.decompress(destination.read_bytes()) if destination.exists() else evidence.get(f'{PREFIX}/actions/jobs/{job["id"]}/logs', binary=True)
            text = log.decode(errors='replace')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(gzip.compress(log, mtime=0))
            # A compact navigation index; the full compressed log remains authoritative.
            row['error_lines'] = [line for line in text.splitlines() if re.search(r'##\[error\]|AssertionError|FAILED |Error:|stale|differs|Draft|closed|cancelled', line, re.I)][-24:]
        except RuntimeError as error:
            row['log_gap'] = str(error)
            row['error_lines'] = []
        row['category'] = ('execution_failure' if any('Run isolated verification layer' in step or 'Run browser shard' in step or 'Run selected head qualification' in step for step in failures)
                           else 'qualification_or_aggregation_rejection' if job['name'] in ('merge-gate','quality','development','browser / verify')
                           else 'planning_or_setup_failure')
        return row
    failed = [job for job in jobs if job['conclusion'] == 'failure']
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, failed))
    write_json(output / 'failures.json', rows)
    csv_rows(output / 'failures.csv', rows)
    print(f'Indexed {len(rows)} failed jobs; log gaps {sum("log_gap" in row for row in rows)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    diagnose(Evidence(options.evidence), options.output)
