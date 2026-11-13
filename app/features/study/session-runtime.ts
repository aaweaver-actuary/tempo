import type { IntentEnvelope, IntentRecord, IntentStore } from "@tempo/browser-intents";
import { createStudySession, type LearningClient, type StudySession } from "@tempo/study-session";
import { HeldStudyIntentError, STUDY_INTENT_KINDS, type StudyAdapterEntry, type StudyIntentKind, type StudyRuntimeOptions } from "./session-contracts";

/** Construct an inert session factory for one explicitly supplied authority. */
export function createStudyRuntime(options: StudyRuntimeOptions) {
  const adapters = new Map<string, StudyAdapterEntry>();
  for (const entry of options.adapters) {
    if (!STUDY_INTENT_KINDS.includes(entry.kind) || adapters.has(entry.kind)) throw new Error(`Invalid or duplicate Study adapter: ${entry.kind}`);
    if (entry.disposition === "deliver" ? typeof entry.adapter?.deliver !== "function" :
        entry.disposition !== "held" || typeof entry.reason !== "string" || !entry.reason.trim())
      throw new Error(`Invalid Study adapter: ${entry.kind}`);
    adapters.set(entry.kind, entry.disposition === "held" ? { ...entry } : { ...entry, adapter: {
      deliver: entry.adapter.deliver.bind(entry.adapter),
      ...(entry.adapter.checkReceipt ? { checkReceipt: entry.adapter.checkReceipt.bind(entry.adapter) } : {}),
    } });
  }
  for (const kind of STUDY_INTENT_KINDS) if (!adapters.has(kind)) throw new Error(`Missing Study adapter: ${kind}`);
  let epoch = 0;
  let active: { store: IntentStore; session: StudySession; facade: StudySession } | undefined;
  function invalidate() { epoch++; active?.session.dispose(); active = undefined; }
  async function session(): Promise<StudySession> {
    const requested = epoch;
    const store = await options.store();
    if (requested !== epoch || !options.isCurrent(store)) throw new Error("Study authority changed. Recover the original request with its original service.");
    if (active?.store === store) return active.facade;
    if (active) invalidate();
    const generation = epoch;
    const ensure = () => {
      if (generation !== epoch || !options.isCurrent(store)) throw new Error("Study authority changed. Recover the original request with its original service.");
    };
    // The session package owns claims and settlement. Guard its borrowed store,
    // including the update callback, so a rotated authority cannot settle there.
    const guarded = new Proxy(store, { get(target, property, receiver) {
      const member = Reflect.get(target, property, receiver);
      if (typeof member !== "function") return member;
      if (property === "release") return (...args: Parameters<IntentStore["release"]>) => target.release(...args);
      if (property === "update") return (key: string, change: Parameters<IntentStore["update"]>[1]) => {
        ensure(); return target.update(key, record => { ensure(); return change(record); });
      };
      return (...args: unknown[]) => { ensure(); return Reflect.apply(member, target, args); };
    } }) as IntentStore;
    const entryFor = (record: IntentRecord) => {
      const entry = adapters.get(record.kind);
      if (!entry) throw new HeldStudyIntentError(record, "unknown retained kind");
      if (entry.disposition === "held") throw new HeldStudyIntentError(record, entry.reason);
      return entry.adapter;
    };
    async function requireDeliverableExisting(key: string) {
      const record = await guarded.get(key);
      ensure();
      if (record) entryFor(record);
    }
    const learning: LearningClient = {
      async deliver(record, signal) { ensure(); const result = await entryFor(record).deliver(record, signal); ensure(); return result; },
      async checkReceipt(record, signal) { ensure(); const result = await entryFor(record).checkReceipt?.(record, signal); ensure(); return result; },
    };
    const inner = createStudySession({ store: guarded, learning });
    // Reserve before the asynchronous retained-kind check, so overlapping callers
    // join the same original recovery even when that check yields.
    const recovery = new Map<string, ReturnType<StudySession["checkSave"]>>();
    function recover(key: string, retry: boolean) {
      ensure();
      const existing = recovery.get(key);
      if (existing) return existing;
      const pending = (async () => {
        await requireDeliverableExisting(key);
        ensure();
        return retry ? inner.retrySave(key) : inner.checkSave(key);
      })();
      recovery.set(key, pending);
      void pending.finally(() => { if (recovery.get(key) === pending) recovery.delete(key); }).catch(() => undefined);
      return pending;
    }
    const facade: StudySession = {
      getSnapshot: inner.getSnapshot,
      subscribe(listener) { ensure(); return inner.subscribe(listener); },
      start(key) { ensure(); inner.start(key); },
      async submit(envelope: IntentEnvelope, data, submission) {
        ensure();
        if (!adapters.has(envelope.kind as StudyIntentKind)) throw new Error(`Unknown new Study intent kind: ${envelope.kind}`);
        await requireDeliverableExisting(envelope.key);
        return inner.submit(envelope, data, submission);
      },
      checkSave(key) { return recover(key, false); },
      retrySave(key) { return recover(key, true); },
      async retryNext(key, acquire, stillCurrent, retryable) {
        ensure(); await requireDeliverableExisting(key);
        return inner.retryNext(key, acquire, () => options.isCurrent(store) && generation === epoch && stillCurrent(), retryable);
      },
      dispose() { if (active?.session === inner) invalidate(); else inner.dispose(); },
    };
    active = { store, session: inner, facade };
    return facade;
  }
  return { session, invalidate };
}
