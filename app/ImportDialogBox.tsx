"use client";
import { TextInput } from "./components/inputs/TextInput";
import { Button } from "./components/buttons/BaseButton";
import { useRef as useDialogRef } from "react";
import { useDialogFocus } from "./hooks/use-dialog-focus";
import { useState, useEffect, useCallback } from "react";
import { JSX } from "react/jsx-runtime";
import { Notice } from "./components/task-tabs";
import { readWorkspaceResponse, invalidateWorkspaceData } from "./lib/workspace-data";
import { API_URL } from "./const";
import { parsePgnImport } from "./lib/pgn-import";
import type { LocalRepertoire } from "./types";
import { usesLocalApi } from "./utils/local";
import CloseButton from "./components/buttons/CloseButton";
import {
  settingsResponseSchema,
  repertoiresResponseSchema,
  queueEnvelopeSchema,
} from "./domain/schemas";
import { readJsonResponse } from "./lib/validated-data";
import { reportDebugError } from "./lib/debug-reporting";
import { savePgnImportCommand, readPendingPgnImport, discardPendingPgnImport } from "./lib/pgn-import-command";
import { PendingOperationError } from "./lib/operation-status";

export function ImportDialogBox({
  onClose,
  onImported,
  onViewRepertoire,
  onDatabaseUpdated,
}: {
  onClose: () => void;
  onImported: (repertoire: LocalRepertoire) => void;
  onViewRepertoire: () => void;
  onDatabaseUpdated: () => Promise<void>;
}): JSX.Element {
  const dialogRef = useDialogRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [file, setFile] = useState<File | null>(null);
  const [initialDepth, setInitialDepth] = useState(6);
  const [trainedColor, setTrainedColor] = useState<"white" | "black">("white");
  const [finished, setFinished] = useState(false);
  const [summary, setSummary] = useState({
    lines: 0,
    duplicates: 0,
    admitted: 0,
    prefixes: 0,
    descendants: 0,
    sharedPrefixes: 0,
    backend: false,
  });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  function rememberedPendingImport() {
    if (!usesLocalApi()) return null;
    try {
      const remembered = readPendingPgnImport();
      return remembered ? new PendingOperationError(remembered.operationId,
        "An earlier PGN import needs confirmation. Check again with its original file, or discard it to import a different file.") : null;
    } catch { return null; }
  }
  const [pendingImport, setPendingImport] = useState<PendingOperationError | null>(rememberedPendingImport);
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const [discarding, setDiscarding] = useState(false);
  const importController = useDialogRef<AbortController | null>(null);
  useEffect(() => () => importController.current?.abort(), [importController]);
  const [settingsLoaded, setSettingsLoaded] = useState(!usesLocalApi());
  const [settingsError, setSettingsError] = useState("");
  const loadImportSettings = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await readWorkspaceResponse(`${API_URL}/api/settings`);
      const saved = await readJsonResponse(response, settingsResponseSchema, "import settings");
      setInitialDepth(saved.initial_depth);
      setSettingsLoaded(true);
      setSettingsError("");
    } catch (failure) {
      reportDebugError(failure, {
        kind: "api",
        source: "import-settings",
        operation: "load import settings",
        endpoint: `${API_URL}/api/settings`,
      });
      setSettingsError(`Import settings unavailable: ${failure instanceof Error ? failure.message : "connection failed"}`);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadImportSettings(), 0);
    return () => window.clearTimeout(timer);
  }, [loadImportSettings]);

  async function waitForPublishedAdmission(
    repertoireId: string,
    signal: AbortSignal,
  ): Promise<number> {
    for (let attempt = 0; attempt < 200; attempt += 1) {
      signal.throwIfAborted();
      const response = await fetch(`${API_URL}/api/repertoires`, { signal, headers: { "X-Tempo-Work-Class": "background" } });
      if (response.ok) {
        const result = await readJsonResponse(
          response,
          repertoiresResponseSchema,
          "imported repertoire admission",
        );
        const repertoire = result.repertoires.find(
          (candidate) => candidate.id === repertoireId,
        );
        if (repertoire?.graph_state === "failed") throw new Error("Repertoire graph failed after import. Retry the failed task in Settings → Service status.");
        if (repertoire?.integrity_scan_status === "failed")
          throw new Error(`Repertoire integrity scan failed after import: ${repertoire.integrity_scan_error ?? "Check Activity and retry the failed task."}`);
        if (repertoire?.integrity_status === "clean" && repertoire.graph_state === "ready" && repertoire.graph_updated_at) {
          const queueResponse = await fetch(`${API_URL}/api/queue/window?limit=1`, { signal, headers: { "X-Tempo-Work-Class": "background" } });
          if (queueResponse.ok) {
            const queue = await readJsonResponse(queueResponse, queueEnvelopeSchema, "imported repertoire queue");
            const projection = queue.projection;
            if (projection?.state === "failed") throw new Error("Daily queue failed after import. Retry the failed task in Settings → Service status.");
            if (projection?.state === "ready" && !projection.refresh_pending && projection.updated_at &&
                Date.parse(projection.updated_at) >= Date.parse(repertoire.graph_updated_at)) {
              const refreshedResponse = await fetch(`${API_URL}/api/repertoires`, { signal, headers: { "X-Tempo-Work-Class": "background" } });
              if (refreshedResponse.ok) {
                const refreshed = await readJsonResponse(refreshedResponse, repertoiresResponseSchema, "published repertoire admission");
                const refreshedRepertoire = refreshed.repertoires.find((candidate) => candidate.id === repertoireId);
                if (refreshedRepertoire) return refreshedRepertoire.due_count;
              }
            }
          }
        }
        if (repertoire?.integrity_status === "needs_repair") return 0;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    throw new Error("Repertoire imported, but today's queue is still preparing. Check Activity before starting training.");
  }

  async function importFile(retryBlocked = false) {
    if (!file || !settingsLoaded || importController.current) return;
    const controller = new AbortController();
    importController.current = controller;
    setWorking(true);
    setError("");
    setRecoveryNotice("");
    setPendingImport(null);
    try {
      const parsed = parsePgnImport(
        file.name,
        await file.text(),
        trainedColor,
        initialDepth,
      );
      let backend = false;
      let admitted = 0;
      let lines = parsed.cards.length;
      let duplicates = parsed.duplicateLines;
      let prefixes = parsed.prefixCards;
      let descendants = parsed.descendantCards;
      let sharedPrefixes = parsed.sharedPrefixes;
      let integrityRepertoireId: string | undefined;
      if (usesLocalApi()) {
        const result = await savePgnImportCommand(file, trainedColor, initialDepth, {
          signal: controller.signal, retryBlocked,
        });
        backend = true;
        if (backend) {
          admitted = result.cards_admitted_today ?? 0;
          lines = result.unique_lines;
          duplicates = result.duplicates_merged;
          prefixes = result.prefix_cards_created ?? 0;
          descendants = result.descendant_decision_cards_created ?? result.decision_cards_created ?? 0;
          sharedPrefixes = result.shared_prefixes_reused ?? 0;
          if (result.integrity?.status === "needs_repair") integrityRepertoireId = result.repertoire_id;
          admitted = await waitForPublishedAdmission(
            result.repertoire_id, controller.signal,
          );
          controller.signal.throwIfAborted();
          await onDatabaseUpdated();
        }
      } else onImported(parsed.repertoire);
      if (controller.signal.aborted) return;
      setSummary({
        lines,
        duplicates,
        admitted,
        prefixes,
        descendants,
        sharedPrefixes,
        backend,
      });
      setFinished(true);
      if (integrityRepertoireId) {
        window.setTimeout(() => window.dispatchEvent(new CustomEvent("tempo:integrity", { detail: { repertoireId: integrityRepertoireId } })), 0);
      }
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof PendingOperationError) {
        setPendingImport(reason);
        if (!reason.blocked) return;
      }
      reportDebugError(reason, {
        kind: "ui",
        source: "pgn-import",
        operation: "import PGN",
        endpoint: usesLocalApi() ? `${API_URL}/api/imports/pgn` : undefined,
        method: usesLocalApi() ? "POST" : undefined,
      });
      if (!(reason instanceof PendingOperationError)) setError(
        reason instanceof Error
          ? reason.message
          : "Tempo could not read this PGN.",
      );
    } finally {
      importController.current = null;
      if (!controller.signal.aborted) {
        setWorking(false);
        setPendingImport(current => current ?? rememberedPendingImport());
      }
    }
  }

  async function discardImport() {
    if (!pendingImport || importController.current) return;
    const controller = new AbortController();
    importController.current = controller;
    setWorking(true);
    setDiscarding(true);
    setError("");
    setRecoveryNotice("");
    try {
      const completed = await discardPendingPgnImport(pendingImport.operationId, { signal: controller.signal });
      controller.signal.throwIfAborted();
      setPendingImport(rememberedPendingImport());
      setRecoveryNotice(completed ? "The earlier import already completed. Its repertoire has been kept."
        : "Pending import discarded. You can choose a new PGN file.");
      if (completed) await onDatabaseUpdated();
    } catch (failure) {
      if (!controller.signal.aborted) {
        setPendingImport(rememberedPendingImport());
        setError(failure instanceof Error ? failure.message : "Discard confirmation unavailable. Check again.");
      }
    } finally {
      importController.current = null;
      if (!controller.signal.aborted) { setWorking(false); setDiscarding(false); }
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="ui-dialog import-dialog"
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <CloseButton onClose={onClose} ariaLabel="Close import dialog" />
        {finished ? (
          <div className="import-finished">
            <span>✓</span>
            <h2 id="import-title">Imported</h2>
            <p>
              <strong>{file?.name}</strong> added {summary.lines} unique{" "}
              {summary.lines === 1 ? "line" : "lines"}
              {summary.duplicates
                ? ` and merged ${summary.duplicates} duplicate ${summary.duplicates === 1 ? "line" : "lines"}`
                : ""}
              .{" "}
              Created {summary.prefixes} initial prefix {summary.prefixes === 1 ? "card" : "cards"} and {summary.descendants} one-move descendant {summary.descendants === 1 ? "card" : "cards"}
              {summary.sharedPrefixes ? `; reused ${summary.sharedPrefixes} shared ${summary.sharedPrefixes === 1 ? "prefix" : "prefixes"}` : ""}.{" "}
              {summary.backend
                ? `${summary.admitted} cards are in today’s queue; remaining new cards will follow your daily limit.`
                : "This browser’s repertoire and practice queue are updated."}
            </p>
            <Button
              variant="primary" className="primary-button"
              onClick={() => {
                onClose();
                onViewRepertoire();
              }}
            >
              View imported repertoire
            </Button>
          </div>
        ) : (
          <>
            <p className="eyebrow">Local import</p>
            <h2 id="import-title">Add PGN repertoire</h2>
            <p className="dialog-copy">
              Your file is parsed on this computer. Re-uploading the same
              positions updates the repertoire without duplicating identical
              full prefixes or descendant decisions.
            </p>
            <label className={`drop-zone${file ? " has-file" : ""}`}>
              <TextInput
                type="file"
                accept=".pgn"
                disabled={working}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setError("");
                }}
              />
              <span>{file ? "♟" : "⇧"}</span>
              <strong>{file?.name || "Choose a PGN file"}</strong>
              <small>{file ? "Ready to import" : ".pgn files only"}</small>
            </label>
            <div className="color-setting">
              <span>
                <strong>Side to train</strong>
                <small>Only your moves count toward line depth</small>
              </span>
              <span className="color-toggle">
                <Button
                  className={trainedColor === "white" ? "active" : ""}
                  disabled={working}
                  onClick={() => setTrainedColor("white")}
                >
                  White
                </Button>
                <Button
                  className={trainedColor === "black" ? "active" : ""}
                  disabled={working}
                  onClick={() => setTrainedColor("black")}
                >
                  Black
                </Button>
              </span>
            </div>
            <label className="depth-setting">
              <span>
                <strong>Learner moves in initial prefix card</strong>
                <small>Later moves become locked one-move descendants</small>
              </span>
              <span className="stepper">
                <Button
                  disabled={working}
                  onClick={() => setInitialDepth(Math.max(2, initialDepth - 1))}
                >
                  −
                </Button>
                <b>{initialDepth} user moves</b>
                <Button
                  disabled={working}
                  onClick={() =>
                    setInitialDepth(Math.min(20, initialDepth + 1))
                  }
                >
                  ＋
                </Button>
              </span>
            </label>
            {!settingsLoaded && !settingsError && <Notice>Loading import settings…</Notice>}
            {settingsError && <Notice error onRetry={() => { invalidateWorkspaceData(); void loadImportSettings(); }}>{settingsError}</Notice>}
            {error && <p className="editor-error" role="alert">{error}</p>}
            {pendingImport && <Notice error={pendingImport.blocked}>{pendingImport.message}</Notice>}
            {recoveryNotice && <Notice>{recoveryNotice}</Notice>}
            {pendingImport && <Button disabled={working} onClick={() => void discardImport()}>
              {discarding ? "Discarding…" : "Discard pending import"}
            </Button>}
            <div className="dialog-footer">
              <span>
                <i className="status-dot" /> Stored locally
              </span>
              <Button
                variant="primary" className="primary-button"
                disabled={!file || working || !settingsLoaded}
                onClick={() => void importFile(pendingImport?.blocked ?? false)}
              >
                {working ? "Importing…" : pendingImport?.blocked ? "Retry blocked import" : pendingImport ? "Check again" : "Import repertoire"}
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
