// Transitional same-rating/full-legal-universe adapter. Computation is owned by
// workers/maia; retire this entry point when legacy coverage tooling migrates.
import { resolve } from "node:path";
import { createModel, inspectModel } from "../workers/maia/model.mjs";
import { validatePosition } from "../workers/maia/core.mjs";

const repositoryDirectory = resolve(import.meta.dirname, "..");
const options = {
  allowLegacyRatings: true,
  assetPaths: {
    modelParts: Array.from({ length: 6 }, (_, index) => resolve(repositoryDirectory, `public/maia3/parts/part-${String(index).padStart(2, "0")}`)),
    moveIndex: resolve(repositoryDirectory, "app/lib/all_moves_maia3.json"),
    reverseIndex: resolve(repositoryDirectory, "app/lib/all_moves_maia3_reversed.json"),
  },
};
let modelPromise;
function loadModel() {
  modelPromise ??= createModel(options).catch((error) => { modelPromise = undefined; throw error; });
  return modelPromise;
}

export async function getMaiaIdentity() { return inspectModel(options); }

export async function analyzeMaiaPosition(fen, elo, { onInferenceStarted } = {}) {
  // chess.js historically accepted missing move counters in legacy tooling.
  const fields = typeof fen === "string" ? fen.split(" ") : [];
  const completeFen = fields.length === 4 ? `${fen} 0 1` : fields.length === 5 ? `${fen} 1` : fen;
  const position = validatePosition(completeFen);
  if (position.terminal) return [];
  const model = await loadModel();
  onInferenceStarted?.();
  const predictions = await model.infer({ fen: completeFen, rating: elo, legal_moves: position.legalMoves, model_id: model.identity.model_id, model_version: model.identity.model_version });
  return predictions.map(({ move, probability }) => ({ move_uci: move, probability })).sort((left, right) => right.probability - left.probability);
}
