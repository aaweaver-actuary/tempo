export type RustLineDiagnostic = {
  ply: number;
  chessMove: string;
  kind: "null" | "invalid" | "illegal";
  message: string;
};

export type RustValidatedLine = {
  startingFen: string;
  moves: string[];
  finalFen: string;
  diagnostics: RustLineDiagnostic[];
};

type TempoCoreModule = {
  default: (input?: RequestInfo | URL | WebAssembly.Module) => Promise<unknown>;
  core_version: () => string;
  canonical_fen_key: (fen: string) => string;
  validate_uci_line: (fen: string, moves: string[]) => RustValidatedLine;
  card_id: (fen: string, moves: string[]) => string;
  position_distance: (leftFen: string, rightFen: string) => number;
  prefix: (fen: string, moves: string[], color: string, depth: number) => string[];
};

let corePromise: Promise<TempoCoreModule> | undefined;

function assetRoot() {
  const base = document.querySelector("base")?.href ?? document.baseURI;
  return new URL("tempo-core/", base);
}

export async function loadTempoCore(): Promise<TempoCoreModule> {
  if (!corePromise) {
    corePromise = (async () => {
      const root = assetRoot();
      const wasmCore = await import(/* @vite-ignore */ new URL("tempo_core.js", root).href) as TempoCoreModule;
      await wasmCore.default(new URL("tempo_core_bg.wasm", root));
      return wasmCore;
    })();
  }
  return corePromise;
}

export const tempoCore = {
  version: async () => (await loadTempoCore()).core_version(),
  canonicalFenKey: async (fen: string) => (await loadTempoCore()).canonical_fen_key(fen),
  validateLine: async (fen: string, moves: string[]) => (await loadTempoCore()).validate_uci_line(fen, moves),
  cardId: async (fen: string, moves: string[]) => (await loadTempoCore()).card_id(fen, moves),
  positionDistance: async (leftFen: string, rightFen: string) => (await loadTempoCore()).position_distance(leftFen, rightFen),
  prefix: async (fen: string, moves: string[], color: "white" | "black", depth: number) => (await loadTempoCore()).prefix(fen, moves, color, depth),
};
