import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within, act } from "@testing-library/react";
import fixture from "../fixtures/prefix-comparison/structural.json";
import { PrefixComparisonDialog } from "../../app/components/prefix-comparison-dialog";
import { waitForPrefixIdle, readPrefixSource } from "../../app/lib/prefix-comparison";
import { candidateDepths, exactRouteLines, prefixSourceSchema, prefixComparisonSchema } from "../../app/domain/prefix-comparison";

const source = prefixSourceSchema.parse(fixture.source);
const comparisons = fixture.comparisons as Record<string, unknown>;
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
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
