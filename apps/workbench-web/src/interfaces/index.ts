/** Supported app mount/read/navigation surface; private command/client state stays private. */
export { mountWorkbench } from "../workbench.js";
export { gameFrames } from "../game-browser.js";
export { gameSourceUrl } from "../game-source.js";
export {
  intelligenceOrigin, intelligenceOriginForBuild, studyHandoffNotice,
  studyUrl, studyUrlForBuild,
} from "../public-navigation.js";
export type { WorkbenchOptions } from "../workbench.js";
export type { GamesReader, GameScope, GamePage, Game } from "../games-reader.js";
export type { RunCreationRequest } from "../contracts.js";
