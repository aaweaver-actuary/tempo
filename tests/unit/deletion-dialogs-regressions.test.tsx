import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RepertoireDeleteDialog } from "../../app/components/repertoire-delete-dialog";
import CardEditor from "../../app/views/card_editor";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { Chess } from "chess.js";

vi.mock("../../app/components/chessboard", async () => {
  const { KeyboardTestBoard } = await import("./keyboard-board-fixture");
  return { Chessboard: (props: React.ComponentProps<typeof KeyboardTestBoard>) => <KeyboardTestBoard {...props} testId="deletion-board" /> };
});
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("repertoire deletion defaults to keeping learned cards and offers an explicit delete choice", () => {
  const confirm = vi.fn(async () => {});
  render(<RepertoireDeleteDialog name="Old opening" busy={false} error="" onClose={vi.fn()} onConfirm={confirm} />);
  expect((screen.getByRole("radio", { name: "Keep learned cards" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByText(/Shared cards remain/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Delete repertoire" }));
  expect(confirm).toHaveBeenLastCalledWith("keep");
  fireEvent.click(screen.getByRole("radio", { name: "Delete learned cards" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete repertoire" }));
  expect(confirm).toHaveBeenLastCalledWith("delete");
});

it("repertoire confirmation cancels without submission and locks the recovered policy", () => {
  const confirm = vi.fn(async () => {}), close = vi.fn();
  const view = render(<RepertoireDeleteDialog name="Old opening" busy={false} error="" onClose={close} onConfirm={confirm} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
  expect(confirm).not.toHaveBeenCalled();
  view.unmount();
  render(<RepertoireDeleteDialog name="Old opening" pendingPolicy="delete" busy={false} error="Still pending" onClose={close} onConfirm={confirm} />);
  expect((screen.getByRole("radio", { name: "Delete learned cards" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("radio", { name: "Keep learned cards" }).matches(":disabled")).toBe(true);
  expect(screen.getByRole("alert").textContent).toBe("Still pending");
  fireEvent.click(screen.getByRole("button", { name: "Check deletion" }));
  expect(confirm).toHaveBeenCalledWith("delete");
});

const card = { id: asCardId("delete-card"), backendId: asCardId("delete-card"), revision: 2, title: "Shared opening", subtitle: "Test", kind: "opening" as const, startingFen: asFenString(new Chess().fen()), moves: [asSanMove("e4")], userMoveTarget: 1 };
const preview = { card_id: card.backendId, revision: 2, repertoires: [{ id: "first", name: "First repertoire" }, { id: "second", name: "Second repertoire" }] };

it("the red card-delete button names every affected repertoire and cancellation preserves the editor", async () => {
  const close = vi.fn(), deleted = vi.fn(async () => {});
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(preview)));
  render(<CardEditor practiceCard={card} boardTheme="brown" pieceSet="cburnett" onClose={close} onSave={vi.fn()} onDelete={deleted} />);
  expect(screen.getByRole("button", { name: "Delete card" }).getAttribute("data-variant")).toBe("danger");
  fireEvent.click(screen.getByRole("button", { name: "Delete card" }));
  await screen.findByText(/Affected repertoires: First repertoire, Second repertoire/);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Validate & save" })).toBeTruthy();
  expect(close).not.toHaveBeenCalled(); expect(deleted).not.toHaveBeenCalled();
});

it("active card deletion closes only after its durable receipt and retains failures for recovery", async () => {
  let complete = false;
  const close = vi.fn(), deleted = vi.fn(async () => {});
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/deletion-preview")) return Response.json(preview);
    if (String(input).includes("/api/operations/")) return Response.json(complete ? { state: "complete", response: { deleted: true, card_id: card.backendId } } : { state: "pending" });
    return Response.json({ operation_id: (init?.headers as Record<string, string>)["Idempotency-Key"], state: "pending" }, { status: 202 });
  }));
  render(<CardEditor practiceCard={card} boardTheme="brown" pieceSet="cburnett" onClose={close} onSave={vi.fn()} onDelete={deleted} />);
  fireEvent.click(screen.getByRole("button", { name: "Delete card" }));
  await screen.findByRole("button", { name: "Permanently delete card" });
  fireEvent.click(screen.getByRole("button", { name: "Permanently delete card" }));
  await screen.findByRole("alert");
  expect(close).not.toHaveBeenCalled(); expect(deleted).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-card-delete-v1")).not.toBeNull();
  complete = true;
  fireEvent.click(screen.getByRole("button", { name: "Check deletion" }));
  await waitFor(() => expect(deleted).toHaveBeenCalledWith(card));
  expect(close).toHaveBeenCalledOnce();
});

it("card deletion refuses a preview from a newer revision", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...preview, revision: 3 })));
  render(<CardEditor practiceCard={card} boardTheme="brown" pieceSet="cburnett" onClose={vi.fn()} onSave={vi.fn()} onDelete={vi.fn(async () => {})} />);
  fireEvent.click(screen.getByRole("button", { name: "Delete card" }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/card changed/);
  expect(screen.queryByRole("button", { name: "Permanently delete card" })).toBeNull();
});
