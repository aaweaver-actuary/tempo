"""Native test target fence; no infrastructure is provisioned by this module."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

import psycopg
from psycopg.conninfo import conninfo_to_dict


@dataclass(frozen=True)
class OwnedQueueFixture:
    name: str
    host: str
    port: str
    system_identifier: str
    database_oid: str
    admin_dsn: str
    reader_dsn: str
    writer_dsn: str


def owned_target(environment, *, now=None):
    """Reject missing/stale/live/indirect DSNs before opening any connection.

    The parent creates the receipt from its exact owned container/provisioned DB.
    Strings supplied as markers alone never authorize a target or select defaults.
    """
    now = time.time() if now is None else now
    name = environment.get('TEMPO_QUEUE_ADMISSION_FIXTURE', '')
    if not re.fullmatch(r'tempo_queue_admission_[0-9a-f]{32}', name):
        raise RuntimeError('Exact parent-owned queue fixture name required')
    if environment.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Disposable fixture marker required')
    if any(key.startswith('PG') for key in environment):
        raise RuntimeError('Ambient libpq configuration is forbidden')
    path = environment.get('TEMPO_QUEUE_ADMISSION_TARGET_FILE')
    if not path:
        raise RuntimeError('Parent-owned target receipt required')
    try:
        receipt = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise RuntimeError('Readable parent-owned target receipt required') from None
    if (not isinstance(receipt, dict) or receipt.get('version') != 1 or receipt.get('database') != name
            or receipt.get('host') != '127.0.0.1'
            or not re.fullmatch(r'[0-9a-f]{64}', str(receipt.get('container_id', '')))
            or not re.fullmatch(r'[0-9]+', str(receipt.get('system_identifier', '')))
            or not re.fullmatch(r'[0-9]+', str(receipt.get('database_oid', '')))):
        raise RuntimeError('Invalid owned target identity receipt')
    port = str(receipt.get('port', ''))
    if not port.isdigit() or not 1 <= int(port) <= 65535 or int(port) == 5432:
        raise RuntimeError('Explicit owned ephemeral loopback port required')
    created, deadline = receipt.get('created_at'), receipt.get('deadline')
    if (not isinstance(created, (int, float)) or not isinstance(deadline, (int, float))
            or not created <= now < deadline or not 0 < deadline-created <= 600):
        raise RuntimeError('Expired or invalid owned target receipt')
    keys = ('TEMPO_QUEUE_ADMISSION_ADMIN', 'TEMPO_DATABASE_READ_URL', 'TEMPO_DATABASE_WRITE_URL')
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'connect_timeout', 'sslmode', 'application_name'}
    urls = []
    for key, role in zip(keys, ('postgres', 'tempo_reader', 'tempo_writer')):
        dsn = environment.get(key, '')
        try:
            target = conninfo_to_dict(dsn)
        except psycopg.Error:
            raise RuntimeError('Invalid fixture connection configuration') from None
        if (not target or set(target)-allowed
                or target.get('host') != receipt['host'] or target.get('port') != port
                or target.get('dbname') != name or target.get('user') != role
                or not target.get('password') or target.get('sslmode') != 'disable'
                or target.get('connect_timeout') != '2'):
            raise RuntimeError('Every fixture DSN must match the owned endpoint, database and role')
        urls.append(dsn)
    return OwnedQueueFixture(name, receipt['host'], port, str(receipt['system_identifier']),
                             str(receipt['database_oid']), *urls)


def verify_owned_target(target):
    """Read-only identity verification, after all DSNs pass and before any write."""
    with psycopg.connect(target.admin_dsn, connect_timeout=2, autocommit=True,
                         options='-c default_transaction_read_only=on') as database:
        observed = database.execute(
            'SELECT current_database(),current_schema(),current_user,'
            '(SELECT system_identifier::text FROM pg_control_system()),'
            '(SELECT oid::text FROM pg_database WHERE datname=current_database())',
        ).fetchone()
    if tuple(observed or ()) != (target.name, 'public', 'postgres', target.system_identifier, target.database_oid):
        raise RuntimeError('Connected database identity differs from the owned fixture receipt')
