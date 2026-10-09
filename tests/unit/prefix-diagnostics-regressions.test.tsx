import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PrefixDiagnostics } from "../../app/components/prefix-diagnostics";
import { prefixDiagnosticsDetailSchema } from "../../app/domain/prefix-diagnostics";
import manifest from "../fixtures/opening-evidence-manifest.json";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const listing = { version: 1, repertoire_id: manifest.repertoire_id, graph_generation: 1,
  prefixes: [{ card_id: manifest.card_id, presentation_san: "1. e4 e5 2. Nf3 Nc6 3. Bb5", manifest, unavailable_reason: null }], next_card_id: null };
const detail = { version: 1, read_only: true, graph_generation: 1, manifest,
  window: { attempt_limit: 100, attempt_count: 100, older_attempts_excluded: true,
    newest_started_at: "2026-09-30T12:00:00Z", oldest_started_at: "2026-09-28T12:00:00Z" },
  decisions: manifest.decisions.map((decision, index) => ({ ...decision, coverage: ["strong", "weak", "unknown"][index],
    reached_observations: index === 2 ? 0 : 3, first_responses: index === 2 ? 0 : 3,
    first_response_failures: 0, unassisted_first_responses: index === 2 ? 0 : 3,
    unassisted_first_response_failures: 0, clean_successes: index === 2 ? 0 : 3, assistance_before_response: 0,
    assistance_categories: {}, manual_failures: 0, corrections: 0, reveals: 0, distinct_clean_days: index === 2 ? 0 : index === 0 ? 3 : 1,
    distinct_unassisted_response_days: index === 2 ? 0 : index === 0 ? 3 : 1, recent_outcomes: [] })),
};
function mount() { return render(<PrefixDiagnostics repertoireId={manifest.repertoire_id} onBack={() => {}} />); }
async function inspect() { fireEvent.click(await screen.findByRole("button", { name: /^Inspect prefix/ })); }

it("PD-82 diagnostics load on demand with background GETs and distinguish unknown weak strong", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    expect(options?.method).toBeUndefined();
    expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe("background");
    return Response.json(url.includes("manifest_id=") ? detail : listing);
  });
  vi.stubGlobal("fetch", fetcher);
  expect(fetcher).not.toHaveBeenCalled();
  mount(); await screen.findByRole("button", { name: /^Inspect prefix/ });
  expect(fetcher).toHaveBeenCalledTimes(1);
  await inspect();
  await screen.findByText("Strong evidence");
  expect(screen.getByText("Weak evidence")).toBeTruthy();
  expect(screen.getByText("Unknown")).toBeTruthy();
  expect(screen.getByText("No observations for this decision.")).toBeTruthy();
  expect(screen.getByText(/Older attempts are excluded/)).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /apply|recommend|shorten/i })).toBeNull();
});

it("PD-82 prefix pages use the observed generation and clear previous detail", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return Response.json(url.includes("manifest_id=") ? detail : url.includes("after_card_id=")
      ? { ...listing, prefixes: [], next_card_id: null } : { ...listing, next_card_id: manifest.card_id });
  }));
  mount(); await inspect(); await screen.findByText("Strong evidence");
  fireEvent.click(screen.getByRole("button", { name: "Next prefixes" }));
  await screen.findByText("No current multi-decision opening prefixes.");
  expect(screen.queryByText("Strong evidence")).toBeNull();
  const page = new URL(urls[2], "http://localhost");
  expect(page.searchParams.get("after_card_id")).toBe(manifest.card_id);
  expect(page.searchParams.get("graph_generation")).toBe("1");
});

it("PD-82 stale presentation responses are rejected without displaying evidence", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(url.includes("manifest_id=")
    ? { ...detail, manifest: { ...manifest, card_revision: manifest.card_revision + 1 } } : listing)));
  mount(); await inspect();
  await screen.findByText(/prefix presentation changed/);
  expect(screen.queryByText("Strong evidence")).toBeNull();
});

it("PD-82 cancelled diagnostics cannot publish after refresh or unmount", async () => {
  let resolve!: (response: Response) => void;
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("manifest_id=")) {
      signal = options?.signal ?? undefined;
      return new Promise<Response>(complete => { resolve = complete; });
    }
    return Response.json(listing);
  }));
  const panel = mount(); await inspect();
  panel.unmount();
  expect(signal?.aborted).toBe(true);
  resolve(Response.json(detail));
  await waitFor(() => expect(screen.queryByText("Strong evidence")).toBeNull());
});

it("PD-82 failures show an actionable error and never fabricate success", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Wait for the repertoire graph to finish publishing." }, { status: 409 })));
  mount(); await screen.findByText("Wait for the repertoire graph to finish publishing.");
  expect(screen.queryByText("Strong evidence")).toBeNull();
  expect(screen.getByRole("button", { name: "Refresh prefixes" })).toBeTruthy();
});

it("PD-82 diagnostic contract rejects unsupported versions and excessive windows", () => {
  expect(prefixDiagnosticsDetailSchema.safeParse(detail).success).toBe(true);
  expect(prefixDiagnosticsDetailSchema.safeParse({ ...detail, read_only: false }).success).toBe(false);
  expect(prefixDiagnosticsDetailSchema.safeParse({ ...detail, window: { ...detail.window, attempt_count: 101 } }).success).toBe(false);
});

it("PD-82 prefix selectors distinguish identical learner moves across opponent branches", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...listing, prefixes: [
    { ...listing.prefixes[0], presentation_san: "1. e4 e5 2. Nf3" },
    { ...listing.prefixes[0], card_id: "sicilian", presentation_san: "1. e4 c5 2. Nf3" },
  ] })));
  mount();
  await screen.findByRole("button", { name: /Inspect prefix: 1\. e4 e5 2\. Nf3/ });
  expect(screen.getByRole("button", { name: /Inspect prefix: 1\. e4 c5 2\. Nf3/ })).toBeTruthy();
});

it("PD-82 foreground admission denial resumes the evidence read instead of leaving a permanent error", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Waiting for foreground activity" }, { status: 503, headers: { "Retry-After": "0.001" } }))
    .mockResolvedValueOnce(Response.json(listing));
  vi.stubGlobal("fetch", fetcher);
  mount();
  await screen.findByRole("button", { name: /^Inspect prefix/ });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
