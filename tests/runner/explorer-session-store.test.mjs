import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { load } from 'js-yaml';

test('disposable Explorer workload receives dedicated volatile session storage at the schema runner boundary', () => {
  const configuration=load(readFileSync(new URL('../../docker-compose.postgres.test.yml',import.meta.url),'utf8'));
  const schema=configuration.services.schema;
  assert.equal(schema.environment.TEMPO_EXPLORER_SESSION_REDIS_URL,'redis://explorer-session-store:6379/0');
  assert.equal(schema.depends_on['explorer-session-store'].condition,'service_healthy');
  const storage=configuration.services['explorer-session-store'];
  assert.deepEqual(storage.command,['redis-server','--save','','--appendonly','no']);
  assert.deepEqual(storage.tmpfs,['/data']);
  assert.equal(storage.volumes,undefined);
  assert.equal(storage.ports,undefined);
});
