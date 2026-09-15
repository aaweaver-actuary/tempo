"use client";
import { useState } from "react";
import { JSX } from "react/jsx-runtime";
import { API_URL } from "./const";
import { parsePgnImport } from "./lib/pgn-import";
import type { LocalRepertoire } from "./types";

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

  async function importFile() {
    if (!file) return;
    setWorking(true);
    setError("");
    try {
      const parsed = parsePgnImport(
        file.name,
        await file.text(),
        trainedColor,
        initialDepth,
      );
      onImported(parsed.repertoire);
      let backend = false;
      let admitted = 0;
      if (["localhost", "127.0.0.1"].includes(location.hostname)) {
        const data = new FormData();
        data.append("file", file);
        data.append("trained_color", trainedColor);
        data.append("initial_depth", String(initialDepth));
        try {
          const response = await fetch(`${API_URL}/api/imports/pgn`, {
            method: "POST",
            body: data,
          });
          backend = response.ok;
          if (backend) {
            admitted =
              ((await response.json()) as { cards_admitted_today?: number })
                .cards_admitted_today ?? 0;
            await onDatabaseUpdated();
          }
        } catch {
          /* The browser-local import remains usable without the service. */
        }
      }
      setSummary({
        lines: parsed.cards.length,
        duplicates: parsed.duplicateLines,
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
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button
          className="close-button"
          onClick={onClose}
          aria-label="Close import dialog"
        >
          ×
        </button>
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
            {error && <p className="editor-error">{error}</p>}
            <div className="dialog-footer">
              <span>
                <i className="status-dot" /> Stored locally
              </span>
              <button
                className="primary-button"
                disabled={!file || working}
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
