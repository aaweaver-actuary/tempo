import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within, act } from "@testing-library/react";
import fixture from "../fixtures/prefix-comparison/structural.json";
import transitionFixture from "../fixtures/prefix-comparison/transition.json";
import { readPrefixTransition } from "../../app/lib/prefix-transition-command";
import { PrefixComparisonDialog } from "../../app/components/prefix-comparison-dialog";
import { waitForPrefixIdle, readPrefixSource } from "../../app/lib/prefix-comparison";
import { candidateDepths, exactRouteLines, prefixSourceSchema, prefixComparisonSchema } from "../../app/domain/prefix-comparison";

const source = prefixSourceSchema.parse(fixture.source);
const comparisons = fixture.comparisons as Record<string, unknown>;
it("issue81_confirmation_matches_exact_scope_and_counts", async () => {
  transitionFetch(); await reviewShortening();
  const consequences = screen.getByRole("region", { name: "Exact transition consequences" });
  expect(consequences.textContent).toContain("Selected source routes: 2");
  expect(consequences.textContent).toContain("a · learner-decision depth 2 → 2");
  expect(consequences.textContent).toContain("b · learner-decision depth 3 → 2");
  expect(consequences.textContent).not.toContain("qgd");
  for (const [label, count] of [["Unchanged", 4], ["Reused", 0], ["Added", 1], ["Retired", 1], ["Retained shared", 0]])
    expect(within(consequences).getByText(String(label), { selector: "dt" }).nextElementSibling?.textContent).toBe(String(count));
  expect(consequences.textContent).toContain("no review history or learning evidence transfers");
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
function stubDiagnosticFetch(fetcher: typeof fetch) {
  vi.stubGlobal("fetch", (url: string | URL | Request, options?: RequestInit) => String(url).endsWith("/system/foreground-active")
    ? Promise.resolve(Response.json({ active: false })) : fetcher(url, options));
}
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>(accept => { resolve = accept; });
  return { promise, resolve };
}
function fetchFixture() {
  const fetcher = vi.fn(async (_url: string | URL | Request, options?: RequestInit) => {
    if (options?.method !== "POST") return Response.json(source);
    const body = JSON.parse(String(options.body)) as { selected_line_ids: string[]; candidate_depths: Record<string, number> };
    const key = body.selected_line_ids.join(",") + ":" + Object.values(body.candidate_depths)[0];
    expect(comparisons[key], key).toBeDefined();
    return Response.json(comparisons[key]);
  });
  stubDiagnosticFetch(fetcher); return fetcher;
}
async function mount() {
  const mounted = render(<PrefixComparisonDialog repertoireId="rep" repertoireName="Combined Black" onClose={() => {}} />);
  await screen.findByText("Selected source lines: 0");
  return mounted;
}
function selectLines(...ids: string[]) {
  for (const id of ids) fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`^${id} · saved depth`) }));
}
function compareDepths(text = "2, 3") {
  fireEvent.change(screen.getByLabelText("Candidate learner-decision depths"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
}

function transitionFetch(kind: "ready" | "reused" | "shared" | "blocked" = "ready") {
  const control = { state: "complete", admitted: false, lostResponse: false, planChanged: false, sourceChanged: false,
    refreshFailed: false, offlineAfterDelivery: false, deferPreflight: false, unreadablePreflightStatus: null as number | null,
    planReads: 0, deliveries: [] as { operationId: string; body: unknown }[], retries: 0 };
  stubDiagnosticFetch(vi.fn(async (url, options?: RequestInit) => {
    const path = String(url);
    if (path.endsWith("/prefix-transition/plan")) {
      ++control.planReads;
      if (control.unreadablePreflightStatus && control.planReads === 2) return new Response("Unreadable service response", { status: control.unreadablePreflightStatus });
      if (control.deferPreflight && control.planReads === 2) return Response.json({ detail: { code: "evaluation_busy",
        message: "Study work is active. Retry the diagnostic when study is idle." } }, { status: 503, headers: { "Retry-After": "1" } });
      if (control.sourceChanged && control.planReads > 1) return Response.json({ detail: { code: "stale_snapshot", message: "Source changed concurrently. Refresh the source." } }, { status: 409 });
      return Response.json(control.planChanged && control.planReads > 1 ? { ...transitionFixture[kind], plan_id: "changed" } : transitionFixture[kind]);
    }
    if (path.endsWith("/prefix-transition/apply")) {
      control.admitted = true;
      control.deliveries.push({ operationId: new Headers(options?.headers).get("Idempotency-Key")!, body: JSON.parse(String(options?.body)) });
      if (control.lostResponse) throw new TypeError("Lost HTTP response");
      return Response.json({ operation_id: control.deliveries.at(-1)!.operationId, state: "pending" }, { status: 202 });
    }
    if (path.endsWith("/retry")) { ++control.retries; control.state = "complete"; return Response.json({ state: "blocked" }, { status: 202 }); }
    if (path.includes("/operations/")) {
      const operationId = path.split("/").at(-1)!;
      if (!control.admitted) return Response.json({ operation_id: operationId, state: "unknown" });
      if (control.offlineAfterDelivery) throw new TypeError("Connection offline after delivery");
      return Response.json({ operation_id: operationId, state: control.state,
        transition: { state: control.state === "complete" ? "complete" : control.state === "blocked" ? "recovery_required" : "staging",
          graph_generation: 2, queue_date: control.state === "complete" ? transitionFixture.result.queue_date : null,
          recovery: control.state === "blocked" ? "The activated graph remains committed. Retry this operation to resume publication." : null },
        response: { ...transitionFixture.result, plan_id: transitionFixture[kind].plan_id, operation_id: operationId },
        error: control.state === "failed" ? { detail: { code: "staging_failed", message: "Staging failed before activation. Review a fresh plan." } } : undefined });
    }
    const published = control.admitted && control.state === "complete";
    if (options?.method !== "POST") {
      if (published && control.refreshFailed) throw new Error("Refresh offline");
      return Response.json(published ? transitionFixture.fresh_source : source);
    }
    const body = JSON.parse(String(options.body));
    return Response.json(published ? transitionFixture.fresh_comparison : comparisons[body.selected_line_ids.join(",") + ":" + Object.values(body.candidate_depths)[0]]);
  }));
  return control;
}
async function reviewShortening() {
  const mounted = await mount(); selectLines("a", "b"); compareDepths("2");
  const candidate = await screen.findByRole("region", { name: "Candidate depth 2" });
  fireEvent.click(within(candidate).getByRole("button", { name: "Review shortening" }));
  await screen.findByRole("button", { name: "Confirm shortening" });
  return mounted;
}

it.each(["reused", "shared"] as const)("issue81_confirmation_uses_%s_planner_dispositions", async kind => {
  transitionFetch(kind); await reviewShortening();
  const label = kind === "reused" ? "Reused" : "Retained shared";
  expect(screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent).toBe("1");
});
it("issue81_confirm_waits_for_published_completion", async () => {
  const control = transitionFetch(); control.state = "pending"; await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Prefix transition in progress");
  expect(screen.queryByText("Prefix transition published")).toBeNull();
  expect(readPrefixTransition("rep")?.result).toBeUndefined();
  control.state = "complete";
  await screen.findByText(/Prefix transition published\. Graph/, {}, { timeout: 3000 });
  await screen.findByRole("region", { name: "Candidate depth 2" });
  expect(screen.getByText("No change from saved depths.")).toBeTruthy();
  expect(readPrefixTransition("rep")).toBeNull();
});
it("issue81_double_click_creates_one_operation_in_confirmation", async () => {
  const control = transitionFetch(); await reviewShortening();
  const confirm = screen.getByRole("button", { name: "Confirm shortening" }); fireEvent.click(confirm); fireEvent.click(confirm);
  await screen.findByText(/Prefix transition published\. Graph/);
  expect(control.deliveries).toHaveLength(1);
  expect(control.planReads).toBe(2);
  expect(control.deliveries[0].body).toEqual(transitionFixture.request);
});
it("issue81_recovery_status_receives_focus_after_confirmation", async () => {
  const control = transitionFetch(); control.state = "blocked"; await reviewShortening();
  const confirm = screen.getByRole("button", { name: "Confirm shortening" });
  confirm.focus(); fireEvent.click(confirm);
  const recoveryHeading = await screen.findByRole("heading", { name: "Prefix transition needs recovery" });
  await waitFor(() => expect(document.activeElement).toBe(recoveryHeading));
  expect(control.deliveries).toHaveLength(1);
});
it("issue81_preflight_foreground_deferral_revalidates_the_same_plan", async () => {
  const control = transitionFetch(); await reviewShortening(); control.deferPreflight = true;
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await waitFor(() => expect(control.planReads).toBe(2)); expect(control.deliveries).toHaveLength(0);
  await screen.findByText(/Prefix transition published\. Graph/, {}, { timeout: 3000 });
  expect(control.planReads).toBe(3); expect(control.deliveries).toHaveLength(1);
  expect(control.deliveries[0].body).toEqual(transitionFixture.request);
});
it("issue81_plan_changed_after_foreground_deferral_requires_new_confirmation", async () => {
  const control = transitionFetch(); await reviewShortening(); control.deferPreflight = true; control.planChanged = true;
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Stale transition", {}, { timeout: 3000 });
  expect(control.planReads).toBe(3); expect(control.deliveries).toHaveLength(0);
});
it.each([200, 500])("issue81_unreadable_preflight_%s_fails_closed", async status => {
  const control = transitionFetch(); await reviewShortening(); control.unreadablePreflightStatus = status;
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  const alert = await screen.findByRole("alert"); expect(alert.textContent).toContain(`unreadable response (HTTP ${status})`);
  expect(alert.textContent).toContain("Check the local service and retry"); expect(control.deliveries).toHaveLength(0);
  expect(readPrefixTransition("rep")).toBeNull();
});
it("issue81_pending_survives_remount_in_confirmation", async () => {
  const control = transitionFetch(); control.state = "pending"; const mounted = await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await waitFor(() => expect(control.deliveries).toHaveLength(1));
  const operationId = readPrefixTransition("rep")!.operationId; mounted.unmount();
  render(<PrefixComparisonDialog repertoireId="rep" repertoireName="Combined Black" onClose={() => {}} />);
  await screen.findByText("Prefix transition in progress");
  expect(screen.getByText(/Selected source routes: 2/)).toBeTruthy();
  expect(readPrefixTransition("rep")!.operationId).toBe(operationId);
  expect(screen.queryByLabelText("Candidate learner-decision depths")).toBeNull();
  expect(control.deliveries).toHaveLength(1);
});
it("issue81_lost_response_reload_recovers_original_operation_in_confirmation", async () => {
  const control = transitionFetch(); control.state = "pending"; control.lostResponse = true;
  const mounted = await reviewShortening(); fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await waitFor(() => expect(control.deliveries).toHaveLength(1));
  const operationId = readPrefixTransition("rep")!.operationId; mounted.unmount(); control.state = "complete";
  render(<PrefixComparisonDialog repertoireId="rep" repertoireName="Combined Black" onClose={() => {}} />);
  await screen.findByText(/Prefix transition published\. Graph/);
  await screen.findByRole("region", { name: "Candidate depth 2" });
  expect(control.deliveries.map(delivery => delivery.operationId)).toEqual([operationId]);
});
it.each(["online", "focus"])("issue81_%s_resumes_uncertain_delivery_without_reconfirmation", async eventName => {
  const control = transitionFetch(); control.offlineAfterDelivery = true; await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByRole("heading", { name: "Delivery unconfirmed" });
  const operationId = readPrefixTransition("rep")!.operationId;
  control.offlineAfterDelivery = false;
  await act(async () => { window.dispatchEvent(new Event(eventName)); });
  await screen.findByRole("region", { name: "Candidate depth 2" });
  expect(control.deliveries.map(delivery => delivery.operationId)).toEqual([operationId]);
  expect(readPrefixTransition("rep")).toBeNull();
});
it("issue81_stale_plan_never_mutates", async () => {
  const control = transitionFetch(); await reviewShortening(); control.planChanged = true;
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Stale transition");
  expect((screen.getByRole("button", { name: "Confirm shortening" }) as HTMLButtonElement).disabled).toBe(true);
  expect(control.deliveries).toHaveLength(0); expect(readPrefixTransition("rep")).toBeNull();
});
it("issue81_concurrent_source_change_rejects_confirmation", async () => {
  const control = transitionFetch(); await reviewShortening(); control.sourceChanged = true;
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Stale transition"); expect(control.deliveries).toHaveLength(0);
});
it("issue81_unsupported_shortening_preserves_original_repertoire", async () => {
  const control = transitionFetch("blocked"); await reviewShortening();
  expect(screen.getByText("Unsupported or blocked shortening")).toBeTruthy();
  expect(screen.getByText(/original repertoire remains available/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Confirm shortening" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Back to comparison" }));
  await screen.findByRole("region", { name: "Candidate depth 2" }); expect(control.deliveries).toHaveLength(0);
});
it("issue81_terminal_failure_offers_actionable_recovery_in_confirmation", async () => {
  const control = transitionFetch(); control.state = "failed"; await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Prefix transition failed");
  expect(screen.getByText(/Staging failed before activation/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Review a fresh comparison" }));
  await screen.findByText("Selected source lines: 0"); expect(readPrefixTransition("rep")).toBeNull();
});
it("issue81_blocked_publication_recovers_without_reapplying", async () => {
  const control = transitionFetch(); control.state = "blocked"; await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText(/activated graph remains committed/);
  fireEvent.click(screen.getByRole("button", { name: "Retry publication recovery" }));
  await screen.findByRole("region", { name: "Candidate depth 2" });
  expect(control.retries).toBe(1); expect(control.deliveries).toHaveLength(1);
});
it("issue81_published_success_survives_comparison_refresh_failure", async () => {
  const control = transitionFetch(); control.refreshFailed = true; await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await screen.findByText("Prefix transition published");
  await screen.findByText(/Refresh offline/); expect(readPrefixTransition("rep")?.state).toBe("published");
  control.refreshFailed = false; fireEvent.click(screen.getByRole("button", { name: "Refresh published comparison" }));
  await screen.findByRole("region", { name: "Candidate depth 2" }); expect(control.deliveries).toHaveLength(1);
});

it("issue78_exact_route_selection_resolves_deterministic_source_ids", async () => {
  const prefix = { ...source.lines[0], moves: ["e2e4", "c7c6"] };
  const transposed = { ...source.lines[0], id: "transposed", moves: ["d2d4", "d7d5", "e2e4", "c7c6"] };
  const customRoot = { ...source.lines[0], id: "custom", start_fen: "different exact root" };
  expect(exactRouteLines([...source.lines].reverse().concat(transposed, customRoot), prefix).map(line => line.id)).toEqual(["a", "alias", "b"]);
  fetchFixture(); await mount();
  fireEvent.change(screen.getByLabelText("Starting position and trained color"), { target: { value: JSON.stringify([prefix.start_fen, "black"]) } });
  expect(screen.getByText("Selected source lines: 0")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Move 1"), { target: { value: "e2e4" } });
  fireEvent.change(screen.getByLabelText("Move 2"), { target: { value: "c7c6" } });
  expect(screen.getByText("Selected source lines: 3")).toBeTruthy();
  expect((screen.getByRole("checkbox", { name: /^qgd ·/ }) as HTMLInputElement).checked).toBe(false);
  selectLines("alias");
  fireEvent.change(screen.getByLabelText("Filter source lines by name, ID or UCI moves"), { target: { value: "qgd" } });
  expect(screen.getByText("Selected source lines: 2")).toBeTruthy();
});

it("issue78_mixed_saved_depths_display_without_global_default", async () => {
  fetchFixture(); await mount(); selectLines("a", "b");
  expect(screen.getByText("Current saved depths: Depth 2: 1 lines · Depth 3: 1 lines")).toBeTruthy();
  compareDepths("2, 4");
  await screen.findByRole("region", { name: "Candidate depth 4" });
  expect(screen.getByText((_text, element) => element?.tagName === "LI" && element.textContent === "a: saved 2, requested 4; effective 2 → 3")).toBeTruthy();
});

it("issue78_caro_scope_comparison_preserves_unselected_qgd", async () => {
  const fetcher = fetchFixture(); await mount(); selectLines("a", "b"); compareDepths();
  await screen.findByRole("region", { name: "Candidate depth 3" });
  const posts = fetcher.mock.calls.filter(([, options]) => options?.method === "POST");
  expect(posts).toHaveLength(2);
  for (const [, options] of posts) {
    const body = JSON.parse(String(options?.body));
    expect(body.selected_line_ids).toEqual(["a", "b"]);
    expect(Object.keys(body.candidate_depths)).toEqual(["a", "b"]);
    expect(body.snapshot_id).toBe(source.snapshot_id);
  }
  for (const payload of Object.values(comparisons)) {
    const result = prefixComparisonSchema.parse(payload);
    const qgdSteps = (kind: "current" | "proposed") => result.whole_repertoire[kind].steps.filter(step => step.line_id === "qgd");
    if (!result.selected_line_ids.includes("qgd")) expect(qgdSteps("proposed")).toEqual(qgdSteps("current"));
  }
});

it("issue78_whole_repertoire_counts_preserve_shared_cards", async () => {
  fetchFixture(); await mount(); selectLines("a", "b"); compareDepths("2");
  const candidate = await screen.findByRole("region", { name: "Candidate depth 2" });
  const selected = within(candidate).getByRole("region", { name: "Selected scope" });
  const whole = within(candidate).getByRole("region", { name: "Whole repertoire" });
  expect(within(selected).getByText("Cards added: 1 · removed: 1 · unchanged: 2")).toBeTruthy();
  expect(within(whole).getByText("Cards added: 1 · removed: 1 · unchanged: 4")).toBeTruthy();
  const row = within(whole).getByRole("row", { name: /Distinct cards/ });
  expect(row.textContent).toBe("Distinct cards550");
  compareDepths("3");
  const longer = await screen.findByRole("region", { name: "Candidate depth 3" });
  expect(within(within(longer).getByRole("region", { name: "Selected scope" })).getByText("Cards added: 1 · removed: 2 · unchanged: 1")).toBeTruthy();
  expect(within(within(longer).getByRole("region", { name: "Whole repertoire" })).getByText("Cards added: 0 · removed: 2 · unchanged: 3")).toBeTruthy();
});

it("issue78_source_change_invalidates_late_preview", async () => {
  const delayed = deferred(); let reads = 0;
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method === "POST") return delayed.promise;
    return Response.json(++reads === 1 ? source : { ...source, snapshot_id: "changed" });
  }));
  await mount(); selectLines("a", "b"); compareDepths("2");
  fireEvent.click(screen.getByRole("button", { name: "Refresh source" }));
  await waitFor(() => expect(reads).toBe(2));
  await act(async () => delayed.resolve(Response.json(comparisons["a,b:2"])));
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  expect(screen.getByText("Selected source lines: 0")).toBeTruthy();
});

it("issue78_newer_selection_wins_over_inflight_result", async () => {
  const delayed = deferred();
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method !== "POST") return Response.json(source);
    const body = JSON.parse(String(options.body));
    return body.selected_line_ids.length === 2 ? delayed.promise : Response.json(comparisons["a:2"]);
  }));
  await mount(); selectLines("a", "b"); compareDepths("2"); selectLines("b");
  fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
  await screen.findByRole("region", { name: "Candidate depth 2" });
  await act(async () => delayed.resolve(Response.json(comparisons["a,b:2"])));
  expect(screen.getByText("Selected source lines: 1")).toBeTruthy();
  expect(screen.queryByText(/b: saved 3, requested/)).toBeNull();
});

it("issue78_empty_noop_unsupported_and_stale_are_distinct", async () => {
  const fetcher = fetchFixture(); await mount();
  expect(screen.getByText(/Empty selection/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Compare depths" }) as HTMLButtonElement).disabled).toBe(true);
  selectLines("a"); compareDepths("2"); await screen.findByText("No change from saved depths.");
  fetcher.mockImplementation(async (_url, options) => options?.method === "POST"
    ? Response.json({ detail: { code: "unsupported_source", message: "Saved depths are missing." } }, { status: 409 }) : Response.json(source));
  compareDepths("3"); await screen.findByText("Unsupported source");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  fetcher.mockImplementation(async (_url, options) => options?.method === "POST"
    ? Response.json({ detail: { code: "stale_snapshot", message: "Graph changed." } }, { status: 409 }) : Response.json(source));
  compareDepths("3"); await screen.findByText("Stale preview");
  expect(screen.queryByRole("button", { name: "Compare depths" })).toBeNull();
});

it("issue78_comparison_performs_no_writes", async () => {
  const fetcher = fetchFixture(); await mount(); selectLines("a", "b"); compareDepths("1,2,3,4");
  await screen.findByRole("region", { name: "Candidate depth 4" });
  expect(fetcher.mock.calls.every(([url, options]) => String(url).includes("/prefix-evaluation/")
    && (options?.method === undefined || String(url).endsWith("/evaluate")))).toBe(true);
  expect(screen.queryByRole("button", { name: /apply|save|recommend|keep current/i })).toBeNull();
});

it("issue78_final_source_check_prevents_mixed_snapshot_publication", async () => {
  let reads = 0;
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => options?.method === "POST"
    ? Response.json(comparisons["a,b:2"]) : Response.json(++reads === 1 ? source : { ...source, graph_generation: 2, snapshot_id: "changed" })));
  await mount(); selectLines("a", "b"); compareDepths("2"); await screen.findByText("Stale preview");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
});

it("issue78_candidate_depth_validation_is_bounded_and_does_not_rank", () => {
  expect(candidateDepths("4, 2, 2")).toEqual([2, 4]);
  for (const invalid of ["", "0", "21", "2.5", "true", "1,2,3,4,5", "2,"]) expect(() => candidateDepths(invalid)).toThrow();
});

it("issue78_periodic_and_focus_checks_invalidate_changed_sources", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const fetcher = fetchFixture(); await mount(); selectLines("a"); compareDepths("2");
  await screen.findByRole("region", { name: "Candidate depth 2" });
  fetcher.mockImplementation(async () => Response.json({ ...source, snapshot_id: "external-change" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(screen.getByText("Stale preview")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  vi.useRealTimers();
  fireEvent.click(screen.getByRole("button", { name: "Refresh source" }));
  await screen.findByText("Selected source lines: 0");
  fetcher.mockImplementation(async () => Response.json({ ...source, snapshot_id: "external-change-again" }));
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  await screen.findByText("Stale preview");
});

it("issue78_unmount_and_repertoire_switch_discard_old_callbacks", async () => {
  const delayed = deferred();
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => options?.method === "POST" ? delayed.promise : Response.json(source)));
  const mounted = await mount(); selectLines("a"); compareDepths("2");
  mounted.rerender(<PrefixComparisonDialog repertoireId="other" repertoireName="Other" onClose={() => {}} />);
  await screen.findByText("Unsupported source");
  await act(async () => delayed.resolve(Response.json(comparisons["a:2"])));
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  mounted.unmount();
});

it("issue78_old_failure_cannot_clear_a_newer_success", async () => {
  const delayed = deferred();
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method !== "POST") return Response.json(source);
    const body = JSON.parse(String(options.body));
    return body.selected_line_ids.length === 2 ? delayed.promise : Response.json(comparisons["a:2"]);
  }));
  await mount(); selectLines("a", "b"); compareDepths("2"); selectLines("b");
  fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
  await screen.findByRole("region", { name: "Candidate depth 2" });
  await act(async () => delayed.resolve(Response.json({ detail: { code: "stale_snapshot", message: "Old selection expired." } }, { status: 409 })));
  expect(screen.getByRole("region", { name: "Candidate depth 2" })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("issue78_candidate_edit_cancels_publication_and_batch_failure_returns_no_partial_metrics", async () => {
  const delayed = deferred(); let posts = 0;
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method !== "POST") return Response.json(source);
    if (++posts === 1) return delayed.promise;
    return posts === 2 ? Response.json(comparisons["a,b:2"])
      : Response.json({ detail: { code: "evaluation_busy", message: "Retry when study is idle." } }, { status: 503 });
  }));
  await mount(); selectLines("a", "b"); compareDepths("2");
  fireEvent.change(screen.getByLabelText("Candidate learner-decision depths"), { target: { value: "2,3" } });
  await act(async () => delayed.resolve(Response.json(comparisons["a,b:2"])));
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
  await screen.findByText("Study work is active");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
});

it("issue78_failed_freshness_check_hides_unverified_metrics", async () => {
  const fetcher = fetchFixture(); await mount(); selectLines("a"); compareDepths("2");
  await screen.findByRole("region", { name: "Candidate depth 2" });
  fetcher.mockImplementation(async () => { throw new Error("Source service unavailable."); });
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  await screen.findByText("Service unavailable");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  expect(screen.getByText(/Last freshness check: unverified/)).toBeTruthy();
});

it.each([
  ["service_error", "focus"], ["evaluation_busy", "periodic"],
] as const)("issue78_freshness_recovery_clears_%s_without_restoring_results", async (errorCode, failureTrigger) => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  vi.setSystemTime(new Date("2026-10-07T14:00:00Z"));
  const fetcher = fetchFixture(); await mount(); selectLines("a"); compareDepths("2");
  await screen.findByRole("region", { name: "Candidate depth 2" });
  const initialCheck = screen.getByText(/Last freshness check:/).textContent;
  fetcher.mockResolvedValueOnce(Response.json({ detail: { code: errorCode, message: "Temporary freshness failure." } }, { status: 503 }));
  await act(async () => {
    if (failureTrigger === "focus") window.dispatchEvent(new Event("focus"));
    else await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(screen.getByRole("alert").textContent).toContain("Temporary freshness failure.");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  expect(screen.getByText(/Last freshness check: unverified/)).toBeTruthy();
  expect(screen.getByText("Selected source lines: 1")).toBeTruthy();
  vi.setSystemTime(new Date("2026-10-07T14:02:00Z"));
  const readsBeforeRecovery = fetcher.mock.calls.length;
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(fetcher).toHaveBeenCalledTimes(readsBeforeRecovery + 1);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(/Last freshness check:/).textContent).toContain(new Date().toLocaleTimeString());
  expect(screen.getByText(/Last freshness check:/).textContent).not.toBe(initialCheck);
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it.each([
  ["snapshot", { snapshot_id: "external-change" }], ["graph_generation", { graph_generation: source.graph_generation + 1 }],
] as const)("issue78_stale_%s_requires_explicit_refresh_and_reselection", async (_changedField, sourceChange) => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const fetcher = fetchFixture(); await mount(); selectLines("a"); compareDepths("2");
  await screen.findByRole("region", { name: "Candidate depth 2" });
  fetcher.mockResolvedValueOnce(Response.json({ ...source, ...sourceChange }));
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByRole("alert").textContent).toContain("Refresh the source and select again.");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Compare depths" })).toBeNull();
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  const readsAfterInvalidation = fetcher.mock.calls.length;
  await act(async () => { window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(30_000); });
  expect(fetcher).toHaveBeenCalledTimes(readsAfterInvalidation);
  expect(screen.getByText("Stale preview")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh source" }));
  await screen.findByText("Selected source lines: 0");
  expect(screen.queryByRole("alert")).toBeNull();
  expect((screen.getByRole("button", { name: "Compare depths" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  selectLines("a"); fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
  await screen.findByRole("region", { name: "Candidate depth 2" });
});

it.each(["validation", "comparison", "source"] as const)("issue78_older_freshness_success_preserves_newer_%s_error", async newerOperation => {
  const olderFreshness = deferred();
  const fetcher = fetchFixture(); await mount(); selectLines("a");
  fireEvent.change(screen.getByLabelText("Candidate learner-decision depths"), { target: { value: "2" } });
  fetcher.mockImplementationOnce(() => olderFreshness.promise);
  const readsBeforeFreshness = fetcher.mock.calls.length;
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(fetcher).toHaveBeenCalledTimes(readsBeforeFreshness + 1);
  if (newerOperation === "validation") {
    compareDepths("0");
    expect(screen.getByText("Invalid selection")).toBeTruthy();
    expect(fetcher.mock.calls.at(-1)?.[1]?.signal?.aborted).toBe(false);
  } else if (newerOperation === "comparison") {
    fetcher.mockResolvedValueOnce(Response.json({ detail: { code: "service_error", message: "Newer comparison failed." } }, { status: 503 }));
    fireEvent.click(screen.getByRole("button", { name: "Compare depths" }));
    await screen.findByText("Service unavailable");
  } else {
    fetcher.mockRejectedValueOnce(new Error("Newer source operation failed."));
    fireEvent.click(screen.getByRole("button", { name: "Refresh source" }));
    await screen.findByText("Service unavailable");
  }
  const newerError = screen.getByRole("alert").textContent;
  await act(async () => { olderFreshness.resolve(Response.json(source)); });
  expect(screen.getByRole("alert").textContent).toBe(newerError);
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
});

it("issue78_freshness_success_preserves_unrelated_comparison_error", async () => {
  const fetcher = fetchFixture(); await mount(); selectLines("a");
  fetcher.mockResolvedValueOnce(Response.json({ detail: { code: "service_error", message: "Comparison service unavailable." } }, { status: 503 }));
  compareDepths("2"); await screen.findByText("Service unavailable");
  const comparisonError = screen.getByRole("alert").textContent;
  const readsBeforeFreshness = fetcher.mock.calls.length;
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(fetcher).toHaveBeenCalledTimes(readsBeforeFreshness + 1);
  expect(screen.getByRole("alert").textContent).toBe(comparisonError);
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
});

it("issue78_freshness_success_preserves_incompatible_source_error", async () => {
  const fetcher = fetchFixture(); await mount(); selectLines("a"); compareDepths("2");
  await screen.findByRole("region", { name: "Candidate depth 2" });
  fetcher.mockResolvedValueOnce(Response.json({ ...source, repertoire_id: "incompatible" }));
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByText("Unsupported source")).toBeTruthy();
  const incompatibleError = screen.getByRole("alert").textContent;
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByRole("alert").textContent).toBe(incompatibleError);
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh source" }));
  await screen.findByText("Selected source lines: 0");
  expect(screen.queryByRole("alert")).toBeNull();
  expect((screen.getByRole("button", { name: "Compare depths" }) as HTMLButtonElement).disabled).toBe(true);
});

it("issue78_incompatible_response_bindings_cannot_publish", async () => {
  stubDiagnosticFetch(vi.fn(async (_url, options?: RequestInit) => options?.method === "POST"
    ? Response.json({ ...prefixComparisonSchema.parse(comparisons["a,b:2"]), selected_line_ids: ["a", "qgd"] }) : Response.json(source)));
  await mount(); selectLines("a", "b"); compareDepths("2");
  await screen.findByText("Stale preview");
  expect(screen.queryByRole("region", { name: "Candidate depth 2" })).toBeNull();
});

it("issue78_browser_activity_lease_yields_before_readonly_preview", async () => {
  vi.useFakeTimers();
  let admissionReads = 0;
  const fetcher = vi.fn(async (url: string | URL | Request, options?: RequestInit) => {
    if (String(url).endsWith("/system/foreground-active")) {
      expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe("background");
      return Response.json({ active: ++admissionReads < 3 });
    }
    expect(admissionReads).toBe(3);
    return Response.json(source);
  });
  vi.stubGlobal("fetch", fetcher);
  const pending = readPrefixSource("rep", new AbortController().signal);
  await vi.advanceTimersByTimeAsync(250);
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/prefix-evaluation/source"))).toBe(false);
  await vi.advanceTimersByTimeAsync(250);
  expect(await pending).toEqual(source);
  expect(fetcher).toHaveBeenCalledTimes(4);
});

it("issue78_idle_admission_is_cancelled_without_evaluator_retry", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => Response.json({ active: true }));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const pending = waitForPrefixIdle(controller.signal);
  const rejected = expect(pending).rejects.toThrow();
  await vi.advanceTimersByTimeAsync(250);
  controller.abort(); await rejected;
  const reads = fetcher.mock.calls.length;
  await vi.advanceTimersByTimeAsync(2000);
  expect(fetcher).toHaveBeenCalledTimes(reads);
});

it("issue78_source_pagination_preserves_explicit_selection", async () => {
  stubDiagnosticFetch(vi.fn(async () => Response.json({ ...source, lines: [...source.lines,
    ...Array.from({ length: 60 }, (_, index) => ({ ...source.lines[0], id: `extra-${index}`, name: `extra-${index}` }))] })));
  await mount(); selectLines("a");
  expect(screen.getAllByRole("checkbox")).toHaveLength(50);
  fireEvent.click(screen.getByRole("button", { name: "Next source lines" }));
  selectLines("extra-50");
  expect(screen.getByText("Selected source lines: 2")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Filter source lines by name, ID or UCI moves"), { target: { value: "qgd" } });
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  expect(screen.getByText("Selected source lines: 2")).toBeTruthy();
});

it("issue78_longer_requested_depth_can_have_no_structural_change", async () => {
  fetchFixture(); await mount(); selectLines("b"); compareDepths("4");
  await screen.findByText("No structural change; requested depth differs from saved depth.");
  expect(screen.getByText((_text, element) => element?.tagName === "LI" && element.textContent === "b: saved 3, requested 4; effective 3 → 3")).toBeTruthy();
});

beforeEach(() => { installPrefixTransitionTestLocks(); });

it("audit187_published_comparison_waits_for_clear_before_changing_recovery_props", async () => {
  const lockRequests = installPrefixTransitionTestLocks(); const nativeRequest = lockRequests.getMockImplementation()!;
  let releaseClearing!: () => void; const clearingGate = new Promise<void>(resolve => { releaseClearing = resolve; });
  let clearingQueued = false;
  lockRequests.mockImplementation((name, options, callback) => {
    if (!clearingQueued && readPrefixTransition("rep")?.state === "published") {
      clearingQueued = true; return clearingGate.then(() => nativeRequest(name, options, callback));
    }
    return nativeRequest(name, options, callback);
  });
  const control = transitionFetch(); await reviewShortening();
  fireEvent.click(screen.getByRole("button", { name: "Confirm shortening" }));
  await waitFor(() => expect(clearingQueued).toBe(true));
  await act(async () => { releaseClearing(); });
  await screen.findByRole("region", { name: "Candidate depth 2" });
  expect(readPrefixTransition("rep")).toBeNull(); expect(control.deliveries).toHaveLength(1);
});
