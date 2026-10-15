"""Fresh installed-artifact execution; guard environmental APIs, never FSRS math."""
import dataclasses
import datetime
import json
import random
import sys
import time
import uuid
from importlib.abc import MetaPathFinder


class NoClockDatetime(datetime.datetime):
    @classmethod
    def now(cls, *args, **kwargs):
        raise AssertionError('ambient datetime.now')
    today = utcnow = now


class NoClockDate(datetime.date):
    @classmethod
    def today(cls):
        raise AssertionError('ambient date.today')


def reject_environment(*args, **kwargs):
    raise AssertionError('ambient clock/ID/sleep/randomness')


class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app', 'services', 'fastapi', 'celery', 'psycopg',
                'psycopg_pool', 'redis', 'httpx', 'requests', 'sqlite3', 'subprocess', 'torch'}:
            raise AssertionError('infrastructure import: ' + fullname)


def reject_initialization(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect', 'subprocess.Popen', 'os.system', 'os.fork'}:
        raise AssertionError('environmental initialization: ' + event)


if hasattr(time, 'tzset'):
    time.tzset()
datetime.datetime = NoClockDatetime
datetime.date = NoClockDate
time.time = time.time_ns = time.sleep = reject_environment
uuid.uuid1 = uuid.uuid4 = random.random = reject_environment
sys.meta_path.insert(0, RejectInfrastructure())
sys.addaudithook(reject_initialization)
if len(sys.argv) > 1:
    {'clock': datetime.datetime.now, 'sleep': time.sleep, 'identity': uuid.uuid4,
     'random': random.random}[sys.argv[1]]()

import tempo_scheduling
assert 'site-packages' in tempo_scheduling.__file__
assert all('/packages/scheduling/src' not in path for path in sys.path)
outputs = []
for case in json.load(sys.stdin):
    inputs = dict(case['inputs'])
    inputs['reviewed_at'] = datetime.datetime.fromisoformat(inputs['reviewed_at'])
    inputs['review_day'] = datetime.date.fromisoformat(inputs['review_day'])
    result = dataclasses.asdict(tempo_scheduling.schedule_review(**inputs))
    result['due_date'] = result['due_date'].isoformat()
    outputs.append(result)
print(json.dumps(outputs))
