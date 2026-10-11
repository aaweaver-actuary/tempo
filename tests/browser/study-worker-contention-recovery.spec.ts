import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { move } from "./product-fixtures";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

type FailureKind = "runtime" | "decode" | "protocol";
type RecoveryProbe = { injected: boolean; created: number; terminated: number; events: Array<{ id: number; kind: string; state: string }> };
type RecoveryWindow = Window & { contentionRecovery: RecoveryProbe };
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

for (const failureKind of ["runtime", "decode", "protocol"] as const) {
  test(`study_worker_${failureKind}_failure_during_index_requires_one_explicit_retry_and_preserves_replacement_ownership`, async ({ page }) => {
    await page.addInitScript((failureKind: FailureKind) => {
      const target = window as unknown as RecoveryWindow;
      const probe = target.contentionRecovery = { injected: false, created: 0, terminated: 0, events: [] } as RecoveryProbe;
      const NativeWorker = window.Worker;
      const taskKinds = new Map<number, string>();
      let obsoleteCallbacks: ((replacementId: number) => void) | undefined;
      class ObservedWorker extends NativeWorker {
        constructor(...args: ConstructorParameters<typeof Worker>) {
          super(...args);
          probe.created++;
          this.addEventListener("message", event => {
            const kind = taskKinds.get(event.data.id);
            if (!kind) return;
            const state = event.data.state === "running" ? "running" : "reply";
            probe.events.push({ id: event.data.id, kind, state });
            if (kind !== "repertoireAlternatives" || state !== "running" || probe.injected) return;
            probe.injected = true;
            const previousMessage = this.onmessage;
            const previousError = this.onerror;
            const previousDecode = this.onmessageerror;
            obsoleteCallbacks = replacementId => {
              previousMessage?.call(this, new MessageEvent("message", { data: { id: replacementId, result: ["obsolete-investigation-result"] } }));
              previousError?.call(this, new ErrorEvent("error", { message: "Obsolete investigation failure" }));
              previousDecode?.call(this, new MessageEvent("messageerror"));
            };
            if (failureKind === "runtime") previousError?.call(this, new ErrorEvent("error", { message: "Investigation injected runtime failure" }));
            else if (failureKind === "decode") previousDecode?.call(this, new MessageEvent("messageerror"));
            else previousMessage?.call(this, new MessageEvent("message", { data: { id: "invalid-investigation-reply" } }));
          });
        }
        postMessage(message: { id: number; task: { kind: string } }) {
          taskKinds.set(message.id, message.task.kind);
          probe.events.push({ id: message.id, kind: message.task.kind, state: "sent" });
          super.postMessage(message);
          if (probe.created > 1 && obsoleteCallbacks) {
            const callbacks = obsoleteCallbacks;
            obsoleteCallbacks = undefined;
            callbacks(message.id);
          }
        }
        terminate() {
          probe.terminated++;
          super.terminate();
        }
      }
      window.Worker = ObservedWorker;
    }, failureKind);
    const card = { id: "recovery-card", queue_entry_id: 19001, start_fen: startingFen, moves: ["e2e4"], content_type: "opening" as const, repertoire_id: "visual-repertoire", repertoire_name: "Recovery", repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
    const lines = Array.from({ length: 1000 }, (_, index) => ({ id: `recovery-line-${index}`, repertoire_id: "visual-repertoire", repertoire_name: "Recovery", name: "Recovery", trained_color: "white" as const, start_fen: startingFen, moves: index ? ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"] : ["d2d4"] }));
    await prepareVisualUI(page, false, [card], lines);
    const retry = page.getByRole("button", { name: "Retry loading lines" });
    await expect(retry).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "Repertoire lines unavailable:" })).toBeVisible();
    const failed = await page.evaluate(() => (window as unknown as RecoveryWindow).contentionRecovery);
    // Another legitimate foreground request may create the replacement before
    // Home's explicit evidence retry. It must not restart evidence by itself.
    expect(failed.created).toBeGreaterThanOrEqual(1);
    expect(failed.created).toBeLessThanOrEqual(2);
    expect(failed.terminated).toBe(1);
    expect(failed.events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
    await retry.click();
    await expect.poll(() => page.evaluate(() => (window as unknown as RecoveryWindow).contentionRecovery.events.filter(event => event.kind === "repertoireAlternatives" && event.state === "reply").length)).toBe(1);
    await expect(retry).toHaveCount(0);
    await move(page, "d2", "d4");
    await expect(page.getByText(/Valid repertoire move · follow the arrow/)).toBeVisible();
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", startingFen);
    const recovered = await page.evaluate(() => (window as unknown as RecoveryWindow).contentionRecovery);
    expect(recovered.created).toBe(2);
    expect(recovered.terminated).toBe(1);
    expect(recovered.events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(2);
    const directory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, `study-worker-${failureKind}-recovery.json`), `${JSON.stringify({ failureKind, ...recovered, scope: "Functional native-worker lifecycle through Home; injected handler fault, public repertoire, explicit retry and alternative acceptance. Not a statistical latency cohort." }, null, 2)}\n`);
  });
}
