"use client";
import { TabList } from "../components/ui";
import { RepertoireDailyLimits } from "../components/repertoire-daily-limits";
import { SelectInput } from "../components/inputs/SelectInput";
import { TextInput } from "../components/inputs/TextInput";
import { Button } from "../components/buttons/BaseButton";
import { useRef, useState, useEffect, useCallback } from "react";
import type { BoardTheme, PieceSet } from "../components/chessboard";
import { API_URL } from "../const";
import {
  readWorkspaceResponse,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { usesLocalApi } from "../utils/local";
import {
  createEncryptedBackup,
  restoreEncryptedBackup,
} from "../lib/encrypted-backup";
import { migrateSqliteToBrowser } from "../lib/sqlite-migration";
import { reportDebugError } from "../lib/debug-reporting";
import { saveLocalSettings } from "../lib/settings-save";
import { PendingOperationError } from "../lib/operation-status";
import { publishNotification, resolveNotification } from "../lib/notifications";

import { Notice } from "../components/task-tabs";

type SettingsValues = {
  initial_depth: number;
  timezone: string;
  new_cards_per_day: number;
  study_new_per_day: number;
  tactics_new_per_day: number;
  defense_new_cards_per_day: number;
  include_defensive_cards_in_daily_stack: boolean;
  discovery_window_days: 30 | 90;
  lichess_username: string;
  chesscom_username: string;
  auto_sync_minutes: number;
  engine_line_window_cp: number;
  major_mistake_cp: number;
  light_first_interval_days: number;
  draw_hold_user_moves: number;
  coverage_reply_denominator: number;
  coverage_cumulative_target: number;
  coverage_horizon_fullmoves: number;
  coverage_path_floor: number;
  coverage_maia_elo: number;
  board_theme: BoardTheme;
  piece_set: PieceSet;
  sound: boolean;
  sound_volume: number;
  coverage_target: number;
  maia_elo: string;
  maia_transposition_plies: number;
  explorer_speeds: string;
  explorer_ratings: string;
  arrow_metric: "stockfish" | "lichess" | "masters";
};

export default function SettingsView({
  theme,
  pieceSet,
  sound,
  onTheme,
  onPieces,
  onSound,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  sound: boolean;
  onTheme: (value: BoardTheme) => void;
  onPieces: (value: PieceSet) => void;
  onSound: (value: boolean) => void;
}) {
  const [values, setValues] = useState<SettingsValues>({
    initial_depth: 6,
    timezone: "local",
    new_cards_per_day: 10,
    study_new_per_day: 2,
    tactics_new_per_day: 5,
    defense_new_cards_per_day: 5,
    include_defensive_cards_in_daily_stack: true,
    discovery_window_days: 90,
    lichess_username: "",
    chesscom_username: "",
    auto_sync_minutes: 3,
    engine_line_window_cp: 30,
    major_mistake_cp: 100,
    light_first_interval_days: 7,
    draw_hold_user_moves: 20,
    coverage_reply_denominator: 100,
    coverage_cumulative_target: 95,
    coverage_horizon_fullmoves: 15,
    coverage_path_floor: 0.0005,
    coverage_maia_elo: 1500,
    board_theme: theme,
    piece_set: pieceSet,
    sound,
    sound_volume: 0.72,
    coverage_target: 90,
    maia_elo: "1500",
    maia_transposition_plies: 4,
    explorer_speeds: "blitz,rapid,classical",
    explorer_ratings: "1600,1800,2000,2200,2500",
    arrow_metric: "stockfish",
  });
  const statusNotificationId = useRef<string | undefined>(undefined);
  function setStatus(message: string) {
    const severity = /could not|damaged|incorrect|unavailable/i.test(message) ? "error"
      : /open Docker|retry/i.test(message) ? "warning" : "success";
    if (message.startsWith("Copying and verifying")) {
      statusNotificationId.current = publishNotification({ severity: "info", source: "settings",
        key: "settings-data-transfer", message, active: true });
    } else if (statusNotificationId.current) {
      resolveNotification(statusNotificationId.current, { severity, message });
      statusNotificationId.current = undefined;
    } else publishNotification({ severity, source: "settings", message });
  }
  const [savedDailyLimit, setSavedDailyLimit] = useState(10);
  const [dirty, setDirty] = useState(false);
  const [activeSection, setActiveSection] = useState("training");
  const [settingsLoaded, setSettingsLoaded] = useState(!usesLocalApi());
  const [loadError, setLoadError] = useState("");
  const loadSettings = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await readWorkspaceResponse(`${API_URL}/api/settings`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const saved = (await response.json()) as Partial<SettingsValues> | null;
      if (!saved || typeof saved.initial_depth !== "number")
        throw new Error("Malformed settings response");
      setValues((current) => ({ ...current, ...saved }));
      setSavedDailyLimit(saved.new_cards_per_day ?? 10);
      setSettingsLoaded(true);
      setLoadError("");
    } catch (error) {
      reportDebugError(error, {
        kind: "api",
        source: "settings-view",
        operation: "load settings",
        endpoint: `${API_URL}/api/settings`,
      });
      setLoadError(
        `Settings unavailable: ${error instanceof Error ? error.message : "connection failed"}. Retry before saving.`,
      );
    }
  }, []);
  const backupInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setValues((current) => ({
        ...current,
        board_theme: theme,
        piece_set: pieceSet,
        sound,
        sound_volume: Number(
          localStorage.getItem("tempo-sound-volume") ?? 0.72,
        ),
        arrow_metric:
          (localStorage.getItem("tempo-arrow-metric") as
            | SettingsValues["arrow_metric"]
            | null) ?? "stockfish",
        engine_line_window_cp: Number(
          localStorage.getItem("tempo-engine-window-cp") ?? 30,
        ),
        coverage_target: Number(
          localStorage.getItem("tempo-coverage-target") ?? 90,
        ),
        maia_elo: localStorage.getItem("tempo-maia-elo") ?? "1500",
        maia_transposition_plies: Number(
          localStorage.getItem("tempo-maia-transposition-plies") ?? 4,
        ),
        explorer_speeds:
          localStorage.getItem("tempo-explorer-speeds") ??
          "blitz,rapid,classical",
        explorer_ratings:
          localStorage.getItem("tempo-explorer-ratings") ??
          "1600,1800,2000,2200,2500",
        lichess_username: localStorage.getItem("tempo-lichess-username") ?? "",
        chesscom_username:
          localStorage.getItem("tempo-chesscom-username") ?? "",
      }));
      void loadSettings();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [pieceSet, sound, theme, loadSettings]);

  function update<K extends keyof SettingsValues>(
    key: K,
    value: SettingsValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setDirty(true);
  }

  async function save() {
    if (!settingsLoaded) return;
    onTheme(values.board_theme);
    onPieces(values.piece_set);
    onSound(values.sound);
    localStorage.setItem(
      "tempo-coverage-target",
      String(values.coverage_target),
    );
    localStorage.setItem("tempo-maia-elo", values.maia_elo);
    localStorage.setItem(
      "tempo-maia-transposition-plies",
      String(values.maia_transposition_plies),
    );
    localStorage.setItem("tempo-explorer-speeds", values.explorer_speeds);
    localStorage.setItem("tempo-explorer-ratings", values.explorer_ratings);
    localStorage.setItem(
      "tempo-engine-window-cp",
      String(values.engine_line_window_cp),
    );
    localStorage.setItem("tempo-arrow-metric", values.arrow_metric);
    localStorage.setItem("tempo-sound-volume", String(values.sound_volume));
    localStorage.setItem(
      "tempo-lichess-username",
      values.lichess_username.trim(),
    );
    localStorage.setItem(
      "tempo-chesscom-username",
      values.chesscom_username.trim(),
    );
    if (usesLocalApi()) {
      const backend = {
        initial_depth: values.initial_depth,
        timezone: values.timezone,
        new_cards_per_day: values.new_cards_per_day,
        study_new_per_day: values.study_new_per_day,
        tactics_new_per_day: values.tactics_new_per_day,
        defense_new_cards_per_day: values.defense_new_cards_per_day,
        include_defensive_cards_in_daily_stack: values.include_defensive_cards_in_daily_stack,
        discovery_window_days: values.discovery_window_days,
        lichess_username: values.lichess_username.trim(),
        chesscom_username: values.chesscom_username.trim(),
        auto_sync_minutes: values.auto_sync_minutes,
        engine_line_window_cp: values.engine_line_window_cp,
        major_mistake_cp: values.major_mistake_cp,
        light_first_interval_days: values.light_first_interval_days,
        draw_hold_user_moves: values.draw_hold_user_moves,
        coverage_reply_denominator: values.coverage_reply_denominator,
        coverage_cumulative_target: values.coverage_cumulative_target,
        coverage_horizon_fullmoves: values.coverage_horizon_fullmoves,
        coverage_path_floor: values.coverage_path_floor,
        coverage_maia_elo: values.coverage_maia_elo,
      };
      try {
        await saveLocalSettings(backend);
        setSavedDailyLimit(values.new_cards_per_day);
        invalidateWorkspaceData();
        setStatus("Saved.");
        setDirty(false);
      } catch (error) {
        setStatus(error instanceof PendingOperationError
          ? "Settings save is pending. Retry with the same changes to check it."
          : error instanceof Error ? error.message
          : "The local service could not save these settings. Retry when it is available.");
      }
    } else {
      setStatus("Saved in this browser.");
      setDirty(false);
    }
  }

  async function transferLocalData() {
    setStatus("Copying and verifying the local database…");
    try {
      const result = await migrateSqliteToBrowser(true);
      if (result.status === "unavailable")
        setStatus("Open Docker Tempo to transfer its local database.");
      else
        setStatus(
          `Verified browser copy (${Object.values(result.counts ?? {}).reduce((sum, count) => sum + count, 0)} records).`,
        );
    } catch (error) {
      reportDebugError(error, {
        kind: "ui",
        source: "settings-view",
        operation: "transfer local data",
      });
      setStatus(
        error instanceof Error
          ? error.message
          : "The local database could not be transferred.",
      );
    }
  }

  async function exportBackup() {
    const passphrase = window.prompt(
      "Choose a passphrase for this encrypted backup",
    );
    if (!passphrase) return;
    try {
      const blob = await createEncryptedBackup(passphrase);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `tempo-backup-${new Date().toISOString().slice(0, 10)}.tempo`;
      link.click();
      URL.revokeObjectURL(link.href);
      setStatus("Encrypted backup downloaded.");
    } catch (error) {
      reportDebugError(error, {
        kind: "ui",
        source: "settings-view",
        operation: "create encrypted backup",
      });
      setStatus(
        error instanceof Error
          ? error.message
          : "The backup could not be created.",
      );
    }
  }

  async function importBackup(file: File | undefined) {
    if (!file) return;
    const passphrase = window.prompt("Enter this backup’s passphrase");
    if (!passphrase) return;
    try {
      const result = await restoreEncryptedBackup(file, passphrase);
      setStatus(
        `Backup restored (${result.merged} records merged). Reload Tempo to use the restored data.`,
      );
    } catch {
      setStatus("That backup is damaged or the passphrase is incorrect.");
    } finally {
      if (backupInput.current) backupInput.current.value = "";
    }
  }

  return (
    <section className="settings-page">
      <div className="page-heading compact">
        <div>
          <h1>Settings</h1>
        </div>
        <Button
          variant="primary" className="primary-button"
          disabled={!settingsLoaded}
          onClick={() => void save()}
        >
          Save settings
        </Button>
        {dirty && <small className="settings-unsaved" role="status">Unsaved changes</small>}
      </div>
      {loadError && (
        <Notice
          error
          onRetry={() => {
            invalidateWorkspaceData();
            void loadSettings();
          }}
        >
          {loadError}
        </Notice>
      )}
      {!settingsLoaded && !loadError && <Notice>Loading settings…</Notice>}
      <TabList as="nav" className="settings-section-nav" label="Settings sections">
        {["training", "board", "builder", "games", "data"].map((section) => (
          <Button
            key={section}
            id={`settings-tab-${section}`}
            role="tab"
            aria-selected={activeSection === section}
            aria-controls={`settings-section-${section}`}
            tabIndex={activeSection === section ? 0 : -1}
            onClick={() => setActiveSection(section)}
          >
            {section === "data" ? "Data & backup" : section[0].toUpperCase() + section.slice(1)}
          </Button>
        ))}
      </TabList>
      <div className="settings-grid">
        <section id="settings-section-training" className="settings-card" role="tabpanel" aria-labelledby="settings-tab-training" hidden={activeSection !== "training"}>
          <h2>Training</h2>
          <label>
            <span>
              Default initial prefix length<small>Learner moves grouped when importing new routes</small>
            </span>
            <TextInput
              type="number"
              min="2"
              max="20"
              value={values.initial_depth}
              onChange={(event) =>
                update("initial_depth", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>
              Default new cards per repertoire per day
              <small>
                Reviews are always shown; unused new-card allowance does not carry over
              </small>
            </span>
            <TextInput
              type="number"
              min="0"
              max="100"
              value={values.new_cards_per_day}
              onChange={(event) =>
                update("new_cards_per_day", Number(event.target.value))
              }
            />
          </label>
          <RepertoireDailyLimits defaultLimit={savedDailyLimit} enabled={settingsLoaded} local={usesLocalApi()} />
          <label>
            <span>New study exercises per day<small>Independent of opening cards; due study reviews still appear</small></span>
            <TextInput type="number" min="0" max="100" value={values.study_new_per_day}
              onChange={(event) => update("study_new_per_day", Number(event.target.value))} />
          </label>
          <label>
            <span>
              New tactics per day
              <small>
                Shared across active packs; due reviews and practice discoveries
                are additional
              </small>
            </span>
            <TextInput
              type="number"
              min="0"
              max="100"
              value={values.tactics_new_per_day}
              onChange={(event) =>
                update("tactics_new_per_day", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>New defensive exercises per day<small>Verified threats and false alarms; due reviews are additional</small></span>
            <TextInput type="number" min="0" max="100"
              value={values.defense_new_cards_per_day}
              onChange={(event) => update("defense_new_cards_per_day", Number(event.target.value))} />
          </label>
          <label>
            <span>Defensive cards in daily stack<small>Turn off to hide them now; your cards and review history are kept</small></span>
            <TextInput type="checkbox"
              checked={values.include_defensive_cards_in_daily_stack}
              onChange={(event) => update("include_defensive_cards_in_daily_stack", event.target.checked)} />
          </label>
          <label>
            <span>Discovery evidence window<small>How far back Tempo counts analyzed games</small></span>
            <SelectInput value={values.discovery_window_days}
              onChange={(event) => update("discovery_window_days", Number(event.target.value) as 30 | 90)}>
              <option value={30}>30 days</option><option value={90}>90 days</option>
            </SelectInput>
          </label>
          <label>
            <span>
              Light first interval
              <small>Days after a clean tactics discovery</small>
            </span>
            <TextInput
              type="number"
              min="1"
              max="90"
              value={values.light_first_interval_days}
              onChange={(event) =>
                update("light_first_interval_days", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>
              Draw hold length
              <small>User moves required in endgame studies</small>
            </span>
            <TextInput
              type="number"
              min="5"
              max="100"
              value={values.draw_hold_user_moves}
              onChange={(event) =>
                update("draw_hold_user_moves", Number(event.target.value))
              }
            />
          </label>
        </section>
        <section id="settings-section-board" className="settings-card" role="tabpanel" aria-labelledby="settings-tab-board" hidden={activeSection !== "board"}>
          <h2>Board</h2>
          <label>
            <span>Board colors</span>
            <SelectInput
              value={values.board_theme}
              onChange={(event) =>
                update("board_theme", event.target.value as BoardTheme)
              }
            >
              <option value="brown">Brown</option>
              <option value="blue">Blue</option>
              <option value="green">Green</option>
            </SelectInput>
          </label>
          <label>
            <span>Piece set</span>
            <SelectInput
              value={values.piece_set}
              onChange={(event) =>
                update("piece_set", event.target.value as PieceSet)
              }
            >
              <option value="cburnett">Cburnett</option>
              <option value="merida">Merida</option>
            </SelectInput>
          </label>
          <label>
            <span>
              Chess-piece sounds
              <small>Lichess standard move and capture recordings</small>
            </span>
            <Button
              className={`setting-switch${values.sound ? " on" : ""}`}
              onClick={() => update("sound", !values.sound)}
            >
              {values.sound ? "On" : "Off"}
            </Button>
          </label>
          <label>
            <span>Sound volume</span>
            <TextInput
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={values.sound_volume}
              onChange={(event) =>
                update("sound_volume", Number(event.target.value))
              }
            />
          </label>
        </section>
        <section id="settings-section-builder" className="settings-card" role="tabpanel" aria-labelledby="settings-tab-builder" hidden={activeSection !== "builder"}>
          <h2>Builder</h2>
          <label>
            <span>Coverage target</span>
            <SelectInput
              value={values.coverage_target}
              onChange={(event) =>
                update("coverage_target", Number(event.target.value))
              }
            >
              <option value="80">80%</option>
              <option value="90">90%</option>
              <option value="95">95%</option>
            </SelectInput>
          </label>
          <label>
            <span>
              Required reply threshold
              <small>Cover opponent moves occurring at least this often</small>
            </span>
            <SelectInput
              value={values.coverage_reply_denominator}
              onChange={(event) =>
                update("coverage_reply_denominator", Number(event.target.value))
              }
            >
              <option value="100">1 in 100</option>
              <option value="200">1 in 200</option>
              <option value="300">1 in 300</option>
            </SelectInput>
          </label>
          <label>
            <span>Cumulative reply coverage</span>
            <SelectInput
              value={values.coverage_cumulative_target}
              onChange={(event) =>
                update("coverage_cumulative_target", Number(event.target.value))
              }
            >
              <option value="90">90%</option>
              <option value="95">95%</option>
              <option value="99">99%</option>
            </SelectInput>
          </label>
          <label>
            <span>Coverage horizon<small>Full move number</small></span>
            <TextInput
              type="number"
              min="4"
              max="40"
              value={values.coverage_horizon_fullmoves}
              onChange={(event) =>
                update("coverage_horizon_fullmoves", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>Candidate colors</span>
            <SelectInput
              value={values.arrow_metric}
              onChange={(event) =>
                update(
                  "arrow_metric",
                  event.target.value as SettingsValues["arrow_metric"],
                )
              }
            >
              <option value="stockfish">Stockfish quality</option>
              <option value="lichess">Lichess practical score</option>
              <option value="masters">Masters practical score</option>
            </SelectInput>
          </label>
          <label>
            <span>
              Engine move window<small>Centipawns from the best move</small>
            </span>
            <TextInput
              type="number"
              min="0"
              max="300"
              value={values.engine_line_window_cp}
              onChange={(event) =>
                update("engine_line_window_cp", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>Maia strength</span>
            <SelectInput
              value={values.maia_elo}
              onChange={(event) => update("maia_elo", event.target.value)}
            >
              <option>1100</option>
              <option>1500</option>
              <option>1900</option>
            </SelectInput>
          </label>
          <label>
            <span>
              Transposition search<small>Maia lookahead plies</small>
            </span>
            <TextInput
              type="number"
              min="2"
              max="8"
              value={values.maia_transposition_plies}
              onChange={(event) =>
                update("maia_transposition_plies", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>Explorer games</span>
            <SelectInput
              value={values.explorer_speeds}
              onChange={(event) =>
                update("explorer_speeds", event.target.value)
              }
            >
              <option value="blitz,rapid,classical">
                Blitz + rapid + classical
              </option>
              <option value="rapid,classical">Rapid + classical</option>
              <option value="classical">Classical only</option>
            </SelectInput>
          </label>
          <label>
            <span>Explorer ratings</span>
            <SelectInput
              value={values.explorer_ratings}
              onChange={(event) =>
                update("explorer_ratings", event.target.value)
              }
            >
              <option value="1600,1800,2000,2200,2500">1600+</option>
              <option value="2000,2200,2500">2000+</option>
              <option value="2200,2500">2200+</option>
            </SelectInput>
          </label>
        </section>
        <section id="settings-section-games" className="settings-card" role="tabpanel" aria-labelledby="settings-tab-games" hidden={activeSection !== "games"}>
          <h2>Games</h2>
          <p>Tempo syncs rated standard blitz, rapid, and classical games from the last 90 days. Bullet, casual, variant, and older games are excluded.</p>
          <label>
            <span>Lichess username</span>
            <TextInput
              value={values.lichess_username}
              onChange={(event) =>
                update("lichess_username", event.target.value)
              }
              placeholder="Optional"
            />
          </label>
          <label>
            <span>Chess.com username</span>
            <TextInput
              value={values.chesscom_username}
              onChange={(event) =>
                update("chesscom_username", event.target.value)
              }
              placeholder="Optional"
            />
          </label>
          <label>
            <span>
              Automatic sync<small>Minutes while Tempo is open</small>
            </span>
            <TextInput
              type="number"
              min="2"
              max="60"
              value={values.auto_sync_minutes}
              onChange={(event) =>
                update("auto_sync_minutes", Number(event.target.value))
              }
            />
          </label>
          <label>
            <span>
              Major mistake threshold<small>Centipawn loss</small>
            </span>
            <TextInput
              type="number"
              min="25"
              max="1000"
              value={values.major_mistake_cp}
              onChange={(event) =>
                update("major_mistake_cp", Number(event.target.value))
              }
            />
          </label>
        </section>
        <section id="settings-section-data" className="settings-card" role="tabpanel" aria-labelledby="settings-tab-data" hidden={activeSection !== "data"}>
          <h2>Data &amp; backup</h2>
          <p className="settings-card-copy">
            Keep an encrypted portable copy of browser data. Docker’s SQLite
            file remains unchanged during transfer.
          </p>
          <div className="settings-actions">
            {usesLocalApi() && (
              <Button onClick={() => void transferLocalData()}>
                Transfer Docker data
              </Button>
            )}
            <Button onClick={() => void exportBackup()}>
              Export encrypted backup
            </Button>
            <Button onClick={() => backupInput.current?.click()}>
              Import encrypted backup
            </Button>
            <TextInput
              ref={backupInput}
              type="file"
              accept=".tempo,application/vnd.tempo.backup+json,application/json"
              hidden
              onChange={(event) => void importBackup(event.target.files?.[0])}
            />
          </div>
        </section>
      </div>
    </section>
  );
}
