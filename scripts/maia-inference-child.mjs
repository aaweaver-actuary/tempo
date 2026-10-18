// The legacy rating adapter stays unchanged; only its process lifetime is optional.
import { analyzeMaiaPosition } from './maia-inference.mjs';
process.on('message', async ({ id, fen, elo }) => {
  try { process.send?.({ id, moves: await analyzeMaiaPosition(fen, elo, {onInferenceStarted:()=>process.send?.({id,started:true})}) }); }
  catch (error) { process.send?.({ id, error: String(error) }); }
});
