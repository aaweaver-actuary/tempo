"use client";
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
import { settingsResponseSchema, importResultSchema } from "./domain/schemas";
import { readJsonResponse } from "./lib/validated-data";

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
    backend: false,
  });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
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
      setSettingsError(`Import settings unavailable: ${failure instanceof Error ? failure.message : "connection failed"}`);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadImportSettings(), 0);
    return () => window.clearTimeout(timer);
  }, [loadImportSettings]);

  async function importFile() {
    if (!file || !settingsLoaded) return;
    setWorking(true);
    setError("");
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
      if (usesLocalApi()) {
        const data = new FormData();
        data.append("file", file);
        data.append("trained_color", trainedColor);
        data.append("initial_depth", String(initialDepth));
        const response = await fetch(`${API_URL}/api/imports/pgn`, {
          method: "POST",
          body: data,
        });
        const result = await readJsonResponse(response, importResultSchema, "PGN import");
        backend = true;
        if (backend) {
          admitted = result.cards_admitted_today ?? 0;
          lines = result.unique_lines;
          duplicates = result.duplicates_merged;
          await onDatabaseUpdated();
        }
      } else onImported(parsed.repertoire);
      setSummary({
        lines,
        duplicates,
        admitted,
        backend,
      });
      setFinished(true);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Tempo could not read this PGN.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="import-dialog"
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
              {summary.backend
                ? `${summary.admitted} cards are in today’s queue; remaining new cards will follow your daily limit.`
                : "This browser’s repertoire and practice queue are updated."}
            </p>
            <button
              className="primary-button"
              onClick={() => {
                onClose();
                onViewRepertoire();
              }}
            >
              View imported repertoire
            </button>
          </div>
        ) : (
          <>
            <p className="eyebrow">Local import</p>
            <h2 id="import-title">Add PGN repertoire</h2>
            <p className="dialog-copy">
              Your file is parsed on this computer. Re-uploading the same
              positions updates the repertoire without duplicating cards.
            </p>
            <label className={`drop-zone${file ? " has-file" : ""}`}>
              <input
                type="file"
                accept=".pgn"
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
                <button
                  className={trainedColor === "white" ? "active" : ""}
                  onClick={() => setTrainedColor("white")}
                >
                  White
                </button>
                <button
                  className={trainedColor === "black" ? "active" : ""}
                  onClick={() => setTrainedColor("black")}
                >
                  Black
                </button>
              </span>
            </div>
            <label className="depth-setting">
              <span>
                <strong>Initial line depth</strong>
                <small>New prefix cards test this many user moves</small>
              </span>
              <span className="stepper">
                <button
                  onClick={() => setInitialDepth(Math.max(2, initialDepth - 1))}
                >
                  −
                </button>
                <b>{initialDepth} user moves</b>
                <button
                  onClick={() =>
                    setInitialDepth(Math.min(20, initialDepth + 1))
                  }
                >
                  ＋
                </button>
              </span>
            </label>
            {!settingsLoaded && !settingsError && <Notice>Loading import settings…</Notice>}
            {settingsError && <Notice error onRetry={() => { invalidateWorkspaceData(); void loadImportSettings(); }}>{settingsError}</Notice>}
            {error && <p className="editor-error" role="alert">{error}</p>}
            <div className="dialog-footer">
              <span>
                <i className="status-dot" /> Stored locally
              </span>
              <button
                className="primary-button"
                disabled={!file || working || !settingsLoaded}
                onClick={() => void importFile()}
              >
                {working ? "Importing…" : "Import repertoire"}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
