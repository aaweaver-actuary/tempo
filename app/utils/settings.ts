import { BoardTheme, PieceSet } from "../components/chessboard";
import { EngineStatus } from "../types";

export type BoardSettings = {
  board_theme: BoardTheme;
  piece_set: PieceSet;
  sound: boolean;
  sound_volume: number;
};

export type TrainingSettings = {
  initial_depth: number;
  new_cards_per_day: number;
  light_first_interval_days: number;
};

export type ExternalAccountSettings = {
  lichess_username: string;
  chesscom_username: string;
  auto_sync_minutes: number;
  lichess_token: string;
};

export type MoveExplorerSettings = {
  state: EngineStatus;
  speeds: string;
  ratings: string;
};

export type MastersExplorerSettings = {
  state: EngineStatus;
};

export type AnalysisEngineSettings = {
  state: EngineStatus;
  engine_path: string;
  hash_size_mb: number;
  threads: number;
  engine_line_window_cp: number;
};

export type MaiaSettings = {
  state: EngineStatus;
  elo: string;
  transposition_plies: number;
};

export type TempoSettings = {
  board: BoardSettings;
  training: TrainingSettings;
  external: ExternalAccountSettings;
  explorer: MoveExplorerSettings;
  masters_explorer: MastersExplorerSettings;
  engine: AnalysisEngineSettings;
  maia: MaiaSettings;

  timezone: string;
  major_mistake_cp: number;
  draw_hold_user_moves: number;
  coverage_target: number;
  arrow_metric: "stockfish" | "lichess" | "masters";
};

export class Settings {
  private settings: TempoSettings;

  // Constructor for the Settings class. Can overwrite any value by passing an object with the desired settings.
  constructor(settings?: Partial<TempoSettings>) {
    // Initialize settings with default values or overwrite with provided settings
    this.settings = {
      board: {
        board_theme: "brown",
        piece_set: "cburnett",
        sound: true,
        sound_volume: 0.72,
      },
      training: {
        initial_depth: 6,
        new_cards_per_day: 10,
        light_first_interval_days: 7,
      },
      external: {
        lichess_username: "",
        chesscom_username: "",
        auto_sync_minutes: 3,
        lichess_token: "",
      },
      explorer: {
        state: "ready",
        speeds: "blitz,rapid,classical",
        ratings: "1600,1800,2000,2200,2500",
      },
      masters_explorer: {
        state: "ready",
      },
      engine: {
        state: "ready",
        engine_path: "",
        hash_size_mb: 128,
        threads: 4,
        engine_line_window_cp: 30,
      },
      maia: {
        state: "ready",
        elo: "1500",
        transposition_plies: 4,
      },
      timezone: "local",
      major_mistake_cp: 100,
      draw_hold_user_moves: 20,
      coverage_target: 90,

      arrow_metric: "stockfish",
      ...settings,
    };
  }

  // Method to get the current settings
  getSettings(): TempoSettings {
    return this.settings;
  }

  getLichessStatus(): EngineStatus {
    return this.settings.external.lichess_token ? "ready" : "auth";
  }

  getMaia(): MaiaSettings {
    return this.settings.maia;
  }

  getEngine(): AnalysisEngineSettings {
    return this.settings.engine;
  }

  getMastersExplorer(): MastersExplorerSettings {
    return this.settings.masters_explorer;
  }

  getExplorer(): MoveExplorerSettings {
    return this.settings.explorer;
  }

  getBoard(): BoardSettings {
    return this.settings.board;
  }
}
