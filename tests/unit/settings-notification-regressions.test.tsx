import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import SettingsView from "../../app/views/settings_view";
import { NotificationCenter, NotificationViewport } from "../../app/components/notification-center";
import { clearNotificationHistory, notificationNeedsAttention, notificationToastIds, notifications } from "../../app/lib/notifications";
import { migrateSqliteToBrowser } from "../../app/lib/sqlite-migration";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: vi.fn(async () => Response.json({ initial_depth: 6 })),
}));
vi.mock("../../app/lib/sqlite-migration", () => ({ migrateSqliteToBrowser: vi.fn() }));

beforeEach(() => {
  clearNotificationHistory();
  vi.mocked(migrateSqliteToBrowser).mockReset();
});

async function startTransfer() {
  render(<>
    <SettingsView theme="brown" pieceSet="cburnett" sound={false}
      onTheme={() => undefined} onPieces={() => undefined} onSound={() => undefined} />
    <NotificationCenter /><NotificationViewport />
  </>);
  await waitFor(() => expect(screen.getByRole("button", { name: "Save settings" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("tab", { name: "Data & backup" }));
  fireEvent.click(screen.getByRole("button", { name: "Transfer Docker data" }));
  expect(migrateSqliteToBrowser).toHaveBeenCalledExactlyOnceWith(true);
  const progress = notifications().find((record) => record.key === "settings-data-transfer")!;
  expect(progress).toMatchObject({ source: "settings", severity: "info", active: true,
    message: "Copying and verifying the local database…", resolvedAt: null });
  expect(notificationToastIds()).toHaveLength(0);
  return progress.id;
}

it("settings transfer unavailable warning remains actionable after progress ends", async () => {
  let finishTransfer!: (result: Awaited<ReturnType<typeof migrateSqliteToBrowser>>) => void;
  vi.mocked(migrateSqliteToBrowser).mockReturnValue(new Promise((resolve) => { finishTransfer = resolve; }));
  const progressId = await startTransfer();
  await act(async () => { finishTransfer({ status: "unavailable" }); });
  const transferRecords = notifications().filter((record) => record.source === "settings");
  expect(transferRecords).toHaveLength(1);
  expect(transferRecords[0]).toMatchObject({ id: progressId, key: "settings-data-transfer", severity: "warning",
    message: "Open Docker Tempo to transfer its local database.", active: false, resolvedAt: null });
  expect(notificationNeedsAttention(transferRecords[0])).toBe(true);
  expect(notificationToastIds()).toContain(progressId);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  const tray = within(screen.getByRole("region", { name: "Notifications" }));
  expect(tray.getByRole("button", { name: "Needs attention" }).getAttribute("aria-pressed")).toBe("true");
  expect(tray.getByText(transferRecords[0].message)).toBeTruthy();
});

it("settings transfer error remains actionable after progress ends", async () => {
  let failTransfer!: (error: Error) => void;
  vi.mocked(migrateSqliteToBrowser).mockReturnValue(new Promise((_, reject) => { failTransfer = reject; }));
  const progressId = await startTransfer();
  await act(async () => { failTransfer(new Error("The local database snapshot could not be read")); });
  const transferRecords = notifications().filter((record) => record.source === "settings");
  expect(transferRecords).toHaveLength(1);
  expect(transferRecords[0]).toMatchObject({ id: progressId, key: "settings-data-transfer", severity: "error",
    message: "The local database snapshot could not be read", active: false, resolvedAt: null });
  expect(notificationNeedsAttention(transferRecords[0])).toBe(true);
  expect(notificationToastIds()).toContain(progressId);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  expect(within(screen.getByRole("region", { name: "Notifications" })).getAllByText(transferRecords[0].message)).not.toHaveLength(0);
});

it("successful settings transfer resolves the same progress record quietly", async () => {
  let finishTransfer!: (result: Awaited<ReturnType<typeof migrateSqliteToBrowser>>) => void;
  vi.mocked(migrateSqliteToBrowser).mockReturnValue(new Promise((resolve) => { finishTransfer = resolve; }));
  const progressId = await startTransfer();
  await act(async () => { finishTransfer({ status: "migrated", counts: { cards: 2 }, checksum: "verified" }); });
  const transferRecords = notifications().filter((record) => record.source === "settings");
  expect(transferRecords).toHaveLength(1);
  expect(transferRecords[0]).toMatchObject({ id: progressId, key: "settings-data-transfer", severity: "success",
    message: "Verified browser copy (2 records).", active: false, resolvedAt: expect.any(String) });
  expect(notificationNeedsAttention(transferRecords[0])).toBe(false);
  expect(notificationToastIds()).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  const tray = within(screen.getByRole("region", { name: "Notifications" }));
  expect(tray.queryByText(transferRecords[0].message)).toBeNull();
  fireEvent.click(tray.getByRole("button", { name: "All" }));
  expect(tray.getByText(transferRecords[0].message)).toBeTruthy();
});
