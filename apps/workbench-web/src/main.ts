import { mountWorkbench } from "./index.js";
declare const __TEMPO_STUDY_URL__: string;
declare const __TEMPO_INTELLIGENCE_ORIGIN__: string;
mountWorkbench(document.getElementById("app")!, {
  intelligenceOrigin: typeof __TEMPO_INTELLIGENCE_ORIGIN__ === "undefined" ? undefined : __TEMPO_INTELLIGENCE_ORIGIN__,
  studyUrl: typeof __TEMPO_STUDY_URL__ === "undefined" ? undefined : __TEMPO_STUDY_URL__,
});
