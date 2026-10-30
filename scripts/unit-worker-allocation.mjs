import { availableParallelism } from "node:os";
import { readDevelopmentCpuAllocation } from "./development-budget.mjs";

/** Partition this invocation's CPU grant; nested pools receive only one share. */
export async function unitWorkerAllocation(environment = process.env, parallelism = availableParallelism()) {
  if (!Number.isSafeInteger(parallelism) || parallelism < 1)
    throw new Error("Unit workers require positive integer available parallelism");
  const supplied = environment.TEMPO_QUALIFICATION_CPUS;
  if (supplied !== undefined && (typeof supplied !== "string" || !supplied.trim()))
    throw new Error("TEMPO_QUALIFICATION_CPUS must contain the actual positive CPU grant");
  // Omission is unrestricted for managed and unmanaged callers alike. Size to
  // this process's available parallelism; never query the Docker daemon total.
  // Explicit grants retain validation through the canonical CPU reader.
  const cpus = await readDevelopmentCpuAllocation({ cpus: supplied === undefined ? parallelism : Number(supplied) });
  // A fractional grant below one CPU still needs one throttled worker. The
  // owning admission/quota and memory safeguards remain responsible for limits.
  const capacity = Math.min(cpus, parallelism);
  const maxWorkers = Math.max(1, Math.floor(capacity));
  return { maxWorkers, env: { TEMPO_QUALIFICATION_CPUS: String(Math.min(capacity, 1)) } };
}
