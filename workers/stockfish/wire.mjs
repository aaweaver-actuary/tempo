import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';

const models = ['StockfishJob', 'StockfishResult', 'JobStatus', 'CancellationRequest', 'CancellationAcknowledgement'];
const ajv = new Ajv2020({ allErrors: true, strict: true });
// Pydantic's discriminator is an annotation; oneOf/const enforce the score union.
ajv.addKeyword({ keyword: 'discriminator', schemaType: 'object', valid: true });
const validators = new Map(models.map((name) => [name, ajv.compile(JSON.parse(readFileSync(new URL(`./schemas/v1/${name}.schema.json`, import.meta.url), 'utf8')))]));

export class WireError extends Error {
  constructor(code, message) { super(message); this.name = 'WireError'; this.code = code; this.retryable = false; }
}

function timestampMicroseconds(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match || match[0] !== value) throw new WireError('invalid_timestamp', 'Invalid timestamp form');
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const offsetHours = Number(match[9] ?? 0), offsetMinutes = Number(match[10] ?? 0);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || hour > 23 || minute > 59 || second > 59 || offsetHours > 23 || offsetMinutes > 59) throw new WireError('invalid_timestamp', 'Timestamp components are out of bounds');
  const precedingYears = year - 1;
  const precedingDays = precedingYears * 365 + Math.floor(precedingYears / 4) - Math.floor(precedingYears / 100) + Math.floor(precedingYears / 400) + days.slice(0, month - 1).reduce((total, count) => total + count, 0) + day - 1;
  const wholeSeconds = BigInt(precedingDays * 86400 + hour * 3600 + minute * 60 + second);
  const offset = BigInt(offsetHours * 3600 + offsetMinutes * 60) * (match[8] === '-' ? -1n : 1n);
  return (wholeSeconds - offset) * 1000000n + BigInt((match[7] ?? '').padEnd(6, '0'));
}

function checkJsonNumbers(value) {
  if (typeof value === 'number' && (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value)))) throw new WireError('invalid_request', 'Nonfinite or noninteroperable JSON number');
  if (Array.isArray(value)) value.forEach(checkJsonNumbers);
  else if (value && typeof value === 'object') Object.values(value).forEach(checkJsonNumbers);
}

export function validatePayload(model, payload) {
  const validator = validators.get(model);
  if (!validator) throw new WireError('unknown_model', 'Unknown protocol model');
  checkJsonNumbers(payload);
  if (!validator(payload)) throw new WireError('invalid_request', `${model} failed schema validation: ${ajv.errorsText(validator.errors)}`);
  const moves = model === 'StockfishJob' ? [...payload.moves, ...payload.search_moves] : model === 'StockfishResult' ? payload.variations.flatMap(item => item.moves) : [];
  if (moves.some(move => /^[a-h][1-8][a-h][1-8][qrbn]?$/.exec(move)?.[0] !== move)) throw new WireError('invalid_request', 'Invalid complete UCI move');
  if (model === 'StockfishResult') {
    if (payload.variations.some((item, index) => item.rank !== index + 1)) throw new WireError('invalid_engine_output', 'Ranks must be contiguous');
    if (timestampMicroseconds(payload.provenance.finished_at) < timestampMicroseconds(payload.provenance.started_at)) throw new WireError('invalid_engine_output', 'Execution finishes before start');
  }
  if (model === 'JobStatus' && ((payload.retryable && payload.status !== 'failed') || (payload.status === 'failed' && payload.reason === null))) throw new WireError('invalid_request', 'Invalid status retryability/reason');
  if (model === 'CancellationAcknowledgement') timestampMicroseconds(payload.acknowledged_at);
  return payload;
}

export function validateRecord(record, { input = false } = {}) {
  if (!record || typeof record !== 'object' || Array.isArray(record) || Object.keys(record).sort().join(',') !== 'model,payload') throw new WireError('invalid_record', 'Record must contain exactly model and payload');
  if (input && !['StockfishJob', 'CancellationRequest'].includes(record.model)) throw new WireError('unknown_model', 'Only StockfishJob and CancellationRequest are input records');
  validatePayload(record.model, record.payload);
  return record;
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

export function fingerprintJob(job) {
  const semanticPayload = { ...job };
  delete semanticPayload.execution_generation;
  return createHash('sha256').update(canonicalJson(semanticPayload)).digest('hex');
}

export function identityOf(payload) {
  return { contract_version: 1, workspace_id: payload.workspace_id, job_id: payload.job_id, request_revision: payload.request_revision, execution_generation: payload.execution_generation };
}

export function stockfishInputDigest(job) {
  const payload = { ...job };
  delete payload.input_digest;
  delete payload.execution_generation;
  return 'sha256:' + createHash('sha256').update('tempo.stockfish.input.v1\n' + canonicalJson(payload)).digest('hex');
}

export function validateResultForJob(job, result) {
  validatePayload('StockfishResult', result);
  for (const field of ['workspace_id', 'job_id', 'request_revision', 'execution_generation', 'engine_version']) if (result[field] !== job[field]) throw new WireError('invalid_engine_output', `Result ${field} mismatch`);
  if (result.provenance.input_digest !== job.input_digest || result.variations.length > job.multi_pv) throw new WireError('invalid_engine_output', 'Result digest/count mismatch');
  return result;
}
