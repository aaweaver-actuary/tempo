import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import ts from "typescript";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  EngineEvidenceSchema, ProbabilityEvidenceSchema, ProposalSchema, proposalDisplayDigest,
  type Proposal, type RepertoireChangeReceipt,
} from "../../app/features/product-api/contracts";
import { RepertoireReviewView } from "../../app/features/repertoire-review/RepertoireReviewView";
import type { RepertoireReviewClient, RepertoireReviewRead, ReviewFinding, ReviewPosition } from "../../app/features/repertoire-review/api";
import { decisionFor, RepertoireReviewSession } from "../../app/features/repertoire-review/state";

// The real retained Board UI renders; only the injected content/transport owner is fake.
const fixture = (name: string) => JSON.parse(readFileSync(`contracts/product-v02/fixtures/${name}.json`, "utf8")).payload;
const original = ProposalSchema.parse(fixture("proposal"));
const position: ReviewPosition = {
  ...original.engine_evidence[0].context, position_key: original.edits[0].position_key,
};
function receipt(proposal: Proposal, status: RepertoireChangeReceipt["status"] = "preview"): RepertoireChangeReceipt {
  return { owner_receipt_id: `receipt-${status}`, proposal_id: proposal.proposal_id, source_revision: proposal.source_revision,
    status, proposal, reason: null };
}
function finding(overrides: Partial<ReviewFinding> = {}): ReviewFinding {
  return { finding_id: "missing", kind: "missing_reply", label: "Missing reply example", position,
    proposal_id: original.proposal_id, reach_probability: 0.2, reply_uci: "a2a3", missing_mass: null,
    evidence_status: "current", probability_evidence: [ProbabilityEvidenceSchema.parse(fixture("probability-unknown-mass"))],
    similarity_differences: original.supporting_position_keys.map(key => ({ supporting_position_key: key, description: "Same pawn structure; different castling rights." })),
    existing_card: null, ...overrides };
}
function read(overrides: Partial<RepertoireReviewRead> = {}): RepertoireReviewRead {
  return { repertoire_id: original.repertoire_id, source_revision: original.source_revision, source_digest: original.source_digest,
    lines: [{ route_id: "line-a", label: "Main line", position }], conflicts: [], findings: [finding()], ...overrides };
}
async function changedProposal(move = "h2h3", source = original): Promise<Proposal> {
  const candidate = ProposalSchema.parse({ ...source, proposal_revision: source.proposal_revision + 1,
    edits: source.edits.map(edit => ({ ...edit, proposed_response_uci: move, displayed_pgn: `1. ${move} *` })),
    card_changes: source.card_changes.map(card => ({ ...card, moves: [move] })), engine_evidence: [] });
  return ProposalSchema.parse({ ...candidate, display_digest: await proposalDisplayDigest(candidate, webcrypto.subtle as SubtleCrypto) });
}
function fakeApi(data = read(), proposals: readonly Proposal[] = [original]): RepertoireReviewClient {
  return {
    review: vi.fn(async () => data), proposals: vi.fn(async () => proposals), evidence: vi.fn(async () => []),
    preview: vi.fn(async proposal => receipt(proposal)),
    prepareEdit: vi.fn(async ({ proposal, responses_uci }) => changedProposal(responses_uci[0], proposal)),
    decide: vi.fn(async decision => decision.action === "edit" ? receipt(decision.edited_proposal!) : receipt(proposals.find(proposal => proposal.proposal_id === decision.proposal_id) ?? original, decision.action === "accept" ? "accepted" : "dismissed")),
  };
}
async function mount(api = fakeApi(), extra: Partial<Parameters<typeof RepertoireReviewView>[0]> = {}) {
  const session = new RepertoireReviewSession(() => "operation-stable");
  const view = render(<RepertoireReviewView api={api} repertoireId="white" session={session} {...extra} />);
  await screen.findByRole("tab", { name: "Missing replies" });
  return { api, session, ...view };
}
async function previewAndApprove() {
  fireEvent.click(screen.getByRole("button", { name: "Preview proposal" }));
  const approval = screen.getByRole("checkbox");
  await waitFor(() => expect((approval as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(approval);
}
beforeEach(() => { vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); });

it("read mounts the retained board with no content command or edit", async () => {
  const { api } = await mount();
  expect(screen.getByRole("group", { name: "Repertoire target position" })).toBeTruthy();
  expect(api.preview).not.toHaveBeenCalled(); expect(api.prepareEdit).not.toHaveBeenCalled(); expect(api.decide).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(true);
});

it("conflict display excludes opponent branches and other repertoires", async () => {
  const conflict = { conflict_id: "own", repertoire_id: "white", position, affected_routes: ["route-a", "route-b"],
    study_block: { status: "blocked" as const, reason: "Competing own responses" },
    choices: [{ move_uci: "a2a3", proposal: original }, { move_uci: "d2d4", proposal: original }] };
  await mount(fakeApi(read({ conflicts: [conflict, { ...conflict, conflict_id: "foreign", repertoire_id: "other", choices: [{ move_uci: "e2e4", proposal: original }] },
    { ...conflict, conflict_id: "opponent", position: { ...position, learner_color: "black" }, choices: [{ move_uci: "b2b3", proposal: original }] }] })));
  expect(screen.getByText("Affected routes: route-a, route-b")).toBeTruthy();
  expect(screen.getByText(/Study block: blocked/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Choose e2e4" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Choose b2b3" })).toBeNull();
});

it("manual conflict choice previews precise affected content and requires explicit confirmation", async () => {
  const api = fakeApi(read({ conflicts: [{ conflict_id: "own", repertoire_id: "white", position, affected_routes: ["route-a"],
    study_block: { status: "blocked", reason: "Own response conflict" }, choices: [{ move_uci: "a2a3", proposal: original }] }] }));
  await mount(api);
  fireEvent.click(screen.getByRole("button", { name: "Choose a2a3" }));
  expect(screen.getByText("1. a3 *")).toBeTruthy();
  await previewAndApprove();
  expect(api.preview).toHaveBeenCalledWith(original); expect(api.decide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await screen.findByText("Owner status: accepted");
  expect(api.decide).toHaveBeenCalledWith(decisionFor(original, "accept"), { operation_id: "operation-stable" });
});

it("associated new cards are visible before approval and covered by the same display digest", async () => {
  const { api } = await mount();
  const cards = screen.getByRole("list", { name: "Associated card changes" });
  expect(within(cards).getByText("New card")).toBeTruthy();
  expect(within(cards).getByText("Moves: a2a3")).toBeTruthy();
  await previewAndApprove();
  expect(api.decide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await screen.findByText("Owner status: accepted");
  expect(vi.mocked(api.decide).mock.calls[0][0].display_digest).toBe(original.display_digest);
});

it("editing invalidates approval; owner revalidation returns a new preview requiring fresh acceptance", async () => {
  const { api } = await mount();
  await previewAndApprove();
  fireEvent.change(screen.getByLabelText("Proposed response 1 (UCI)"), { target: { value: "h2h3" } });
  expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.prepareEdit).not.toHaveBeenCalled(); expect(api.decide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Validate edited preview" }));
  await screen.findByText("Owner status: preview");
  expect(api.prepareEdit).toHaveBeenCalledWith({ proposal: original, responses_uci: ["h2h3"] });
  expect(vi.mocked(api.decide).mock.calls[0][0].action).toBe("edit");
  expect(screen.getByText("Moves: h2h3")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(2));
  const accepted = vi.mocked(api.decide).mock.calls[1][0];
  expect(accepted.action).toBe("accept"); expect(accepted.expected_proposal_revision).toBe(2);
  expect(accepted.display_digest).not.toBe(original.display_digest);
});

it("audit can mount before an inline edit owner is available and keeps the manual-editor intent", async () => {
  const api = fakeApi(); delete api.prepareEdit;
  const navigate = vi.fn(); await mount(api, { onNavigate: navigate });
  expect(screen.getByText(/Inline edit preview is unavailable/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Manual editor" }));
  expect(navigate).toHaveBeenCalledWith({ kind: "manual_edit", repertoire_id: "white" });
  await previewAndApprove();
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(false);
  expect(api.decide).not.toHaveBeenCalled();
});

it("an old pending preview cannot restore approval after edits", async () => {
  let finish!: (value: RepertoireChangeReceipt) => void;
  const api = fakeApi();
  vi.mocked(api.preview).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = await mount(api);
  fireEvent.click(screen.getByRole("button", { name: "Preview proposal" }));
  // A source generation change unmounts the old editor, even if transport ignores abort.
  const other = fakeApi(read({ source_revision: 2 }));
  view.rerender(<RepertoireReviewView api={other} repertoireId="white" />);
  await screen.findByText(/Stale proposal/);
  await act(async () => { finish(receipt(original)); });
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
  expect(other.decide).not.toHaveBeenCalled();
});

it("an edited draft cannot accept an owner response that reuses the old preview revision", async () => {
  const api = fakeApi(); vi.mocked(api.decide).mockResolvedValue(receipt(original));
  await mount(api); await previewAndApprove();
  fireEvent.change(screen.getByLabelText("Proposed response 1 (UCI)"), { target: { value: "h2h3" } });
  fireEvent.click(screen.getByRole("button", { name: "Validate edited preview" }));
  await screen.findByRole("alert");
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.decide).toHaveBeenCalledTimes(1);
});

it("stale proposal cannot preview or accept against another source revision", async () => {
  const { api } = await mount(fakeApi(read({ source_revision: 2 })));
  expect(screen.getByText(/Stale proposal/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Preview proposal" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  expect(api.decide).not.toHaveBeenCalled();
});

it("mismatched owner preview is rejected before approval", async () => {
  const api = fakeApi(); vi.mocked(api.preview).mockResolvedValue(receipt({ ...original, source_digest: "d".repeat(64) }));
  await mount(api); fireEvent.click(screen.getByRole("button", { name: "Preview proposal" }));
  await screen.findByRole("alert");
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true); expect(api.decide).not.toHaveBeenCalled();
});

it("duplicate accept coalesces; uncertain acknowledgment stays pending and retry reuses exact command/key", async () => {
  let fail!: (reason: Error) => void;
  const api = fakeApi(); vi.mocked(api.decide).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
  await mount(api); await previewAndApprove();
  const accept = screen.getByRole("button", { name: "Accept displayed changes" });
  fireEvent.click(accept); fireEvent.click(accept);
  await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1));
  expect(screen.getByText(/Pending acknowledgment/)).toBeTruthy();
  await act(async () => { fail(new Error("Lost acknowledgment")); });
  expect(screen.queryByText("Owner status: accepted")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry pending operation" }));
  await screen.findByText("Owner status: accepted");
  expect(vi.mocked(api.decide).mock.calls[0]).toEqual(vi.mocked(api.decide).mock.calls[1]);
});

it("pending operation survives changing categories and returning to the proposal", async () => {
  const api = fakeApi(); vi.mocked(api.decide).mockRejectedValue(new Error("offline"));
  await mount(api); await previewAndApprove();
  fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await screen.findByText("offline");
  fireEvent.click(screen.getByRole("tab", { name: "Lines & positions" }));
  fireEvent.click(screen.getByRole("tab", { name: "Missing replies" }));
  expect(screen.getByText(/Pending acknowledgment/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Accept displayed changes" }) as HTMLButtonElement).disabled).toBe(true);
});

it("dismissal calls the owner without accepting or creating cards", async () => {
  const { api } = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss proposal" }));
  await screen.findByText("Owner status: dismissed");
  expect(vi.mocked(api.decide).mock.calls[0][0].action).toBe("dismiss"); expect(api.preview).not.toHaveBeenCalled();
});

it.each(["conflict", "unavailable"] as const)("owner %s receipt is displayed without success", async status => {
  const api = fakeApi(); vi.mocked(api.decide).mockResolvedValue({ ...receipt(original, status), reason: "Refresh required" });
  await mount(api); await previewAndApprove(); fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await screen.findByText(`Owner status: ${status} · Refresh required`);
  expect(screen.queryByText("Owner status: accepted")).toBeNull();
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
});

it("30 CP, distinct supporting positions, settings and similarity differences are explicit", async () => {
  await mount();
  expect(screen.getByText(/Maximum loss: 30 CP/)).toBeTruthy();
  expect(screen.getByText(/1 distinct supporting repertoire positions/)).toBeTruthy();
  expect(screen.getAllByText(/different castling rights/).length).toBe(1);
  expect(screen.getByText(/depth 12, nodes 1000, time 100 ms; threads 1; hash 16 MB/)).toBeTruthy();
  expect(screen.getByText(/a2a3: 26 CP.*loss 24 CP/)).toBeTruthy();
});

it("mate scores and inconclusive evidence are never rendered as ordinary CP approval", async () => {
  const engine = EngineEvidenceSchema.parse(fixture("engine-mate"));
  const proposal = { ...original, engine_evidence: [engine, { ...engine, evidence_id: "unknown-engine", best: null, unavailable_reason: "budget_exhausted" }] };
  await mount(fakeApi(read(), [proposal]));
  expect(screen.getAllByText(/mate\/inconclusive: no ordinary CP comparison/).length).toBeGreaterThan(0);
  expect(screen.getByText("Inconclusive: budget_exhausted")).toBeTruthy();
});

it("coverage preserves unknown sample counts/mass and conditional likelihood provenance", async () => {
  await mount();
  expect(screen.getByText(/Position reach: 20.0%.*Missing reply mass: unknown/)).toBeTruthy();
  expect(screen.getByText(/Population: 1600–2000; rapid, classical/)).toBeTruthy();
  expect(screen.getByText(/Sample count: unknown/)).toBeTruthy();
  expect(screen.getByText(/Unknown mass:/)).toBeTruthy();
  expect(screen.queryByText(/100.*coverage/i)).toBeNull();
});

it.each(["stale", "unavailable"] as const)("coverage displays %s status and explicit unavailable reason", async evidence_status => {
  await mount(fakeApi(read({ findings: [finding({ evidence_status, reach_probability: null, probability_evidence: [ProbabilityEvidenceSchema.parse(fixture("probability-unavailable"))] })] })));
  expect(screen.getByText(`Evidence status: ${evidence_status}`)).toBeTruthy();
  expect(screen.getByText(/Unavailable:/)).toBeTruthy(); expect(screen.getByText(/Unknown mass: unknown/)).toBeTruthy();
});

it.each([true, false])("covered common blunder requests existing-card intent (introduced=%s), without a proposal command", async introduced => {
  const navigate = vi.fn(); const api = fakeApi(read({ findings: [finding({ kind: "common_blunder", label: "Covered blunder", existing_card: { presentation_id: "actual-existing", revision: 3, introduced } })] }));
  await mount(api, { onNavigate: navigate }); fireEvent.click(screen.getByRole("tab", { name: "Common blunders" }));
  fireEvent.click(screen.getByRole("button", { name: introduced ? "Practice existing card" : "Introduce existing card" }));
  expect(navigate).toHaveBeenCalledWith({ kind: introduced ? "practice_existing" : "introduce_existing", repertoire_id: "white", presentation_id: "actual-existing", expected_revision: 3 });
  expect(api.decide).not.toHaveBeenCalled(); expect(screen.queryByText("Associated cards — included in acceptance")).toBeNull();
});

it("missing common-blunder response remains a deliberate content proposal", async () => {
  await mount(fakeApi(read({ findings: [finding({ kind: "common_blunder", label: "Uncovered blunder" })] })));
  fireEvent.click(screen.getByRole("tab", { name: "Common blunders" }));
  expect(screen.getByRole("button", { name: "Preview proposal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Introduce existing card" })).toBeNull();
});

it("covered consistency finding keeps the current/proposed moves and distinct support visible", async () => {
  await mount(fakeApi(read({ findings: [finding({ kind: "consistency", label: "Covered outlier" })] })));
  fireEvent.click(screen.getByRole("tab", { name: "Consistency" }));
  expect(screen.getByRole("cell", { name: "d2d4" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: /a2a3/ })).toBeTruthy();
});

it("late read for another repertoire cannot mutate current screen selection", async () => {
  let finish!: (value: RepertoireReviewRead) => void;
  const api = fakeApi(); vi.mocked(api.review).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(read({ repertoire_id: "black", findings: [finding({ label: "Current black finding", proposal_id: null })] }));
  const view = render(<RepertoireReviewView api={api} repertoireId="white" />);
  view.rerender(<RepertoireReviewView api={api} repertoireId="black" />);
  await screen.findByRole("heading", { name: "Current black finding" });
  await act(async () => { finish(read()); });
  expect(screen.getByRole("heading", { name: "Current black finding" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: original.title })).toBeNull();
});

it("an old command acknowledgment cannot display success in another repertoire", async () => {
  let finish!: (value: RepertoireChangeReceipt) => void;
  const api = fakeApi(); vi.mocked(api.decide).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = await mount(api); await previewAndApprove(); fireEvent.click(screen.getByRole("button", { name: "Accept displayed changes" }));
  await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1));
  const nextApi = fakeApi(read({ repertoire_id: "black", findings: [] }), []);
  view.rerender(<RepertoireReviewView api={nextApi} repertoireId="black" session={view.session} />);
  await screen.findByText("Source revision 1 · black");
  await act(async () => { finish(receipt(original, "accepted")); });
  expect(screen.queryByText("Owner status: accepted")).toBeNull();
});

it("import/manual editing/settings remain navigation intents and a failed read is retryable", async () => {
  const navigate = vi.fn(); const api = fakeApi(); vi.mocked(api.review).mockRejectedValueOnce(new Error("owner down"));
  render(<RepertoireReviewView api={api} repertoireId="white" onNavigate={navigate} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Import lines" })); fireEvent.click(screen.getByRole("button", { name: "Manual editor" }));
  fireEvent.click(screen.getByRole("button", { name: "Likelihood settings" }));
  expect(navigate.mock.calls.map(([intent]) => intent.kind)).toEqual(["import", "manual_edit", "settings"]);
  fireEvent.click(screen.getByRole("button", { name: "Refresh review" }));
  await screen.findByRole("heading", { name: original.title }); expect(api.decide).not.toHaveBeenCalled();
});

it("line browsing uses supplied history and accessible category controls", async () => {
  const board = new (await import("chess.js")).Chess(); const start = board.fen(); board.move("e4");
  await mount(fakeApi(read({ lines: [{ route_id: "a", label: "Legal line", position: { ...position, starting_fen: start, history_uci: ["e2e4"], target_fen: board.fen() } }] })));
  fireEvent.click(screen.getByRole("tab", { name: "Lines & positions" }));
  fireEvent.click(screen.getByRole("button", { name: "Previous position" }));
  expect(screen.getByRole("group", { name: "History position 0" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Target position" }));
  expect(screen.getByRole("group", { name: "Repertoire target position" })).toBeTruthy();
});

it("invalid supplied history is reported without a fabricated target board", async () => {
  await mount(fakeApi(read({ lines: [{ route_id: "invalid", label: "Invalid line", position: { ...position, history_uci: ["e2e5"] } }] })));
  fireEvent.click(screen.getByRole("tab", { name: "Lines & positions" }));
  expect(screen.getByRole("alert").textContent).toContain("does not reach the target");
  expect(screen.queryByRole("group", { name: "Repertoire target position" })).toBeNull();
});

it("feature dependency boundary contains no Study controllers, live clients, private sibling or global state imports", () => {
  const files = ["RepertoireReviewView.tsx", "ConflictPanel.tsx", "ProposalPanel.tsx", "PositionViewer.tsx", "EvidencePanel.tsx", "api.ts", "state.ts"];
  for (const file of files) {
    const source = readFileSync(`app/features/repertoire-review/${file}`, "utf8");
    const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const imports = parsed.statements.filter(ts.isImportDeclaration).map(statement => (statement.moduleSpecifier as ts.StringLiteral).text);
    for (const path of imports) expect(path, `${file}: ${path}`).toMatch(/^(?:react|chess\.js|@tempo\/board-ui|\.\/(?:api|state|PositionViewer|EvidencePanel|ConflictPanel|ProposalPanel|styles\.css)|\.\.\/product-api\/contracts|\.\.\/\.\.\/components\/(?:ui|buttons\/BaseButton)|\.\.\/\.\.\/hooks\/use-board-history|\.\.\/\.\.\/\.\.\/packages\/board-ui\/styles\/board\.css)$/);
    expect(source).not.toMatch(/\bfetch\s*\(|localStorage|sessionStorage|useTrainingStore|home_view|training_view|tactics_view/);
  }
});
