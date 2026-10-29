import { defineConfig } from "vite";
import { studyUrlForBuild, intelligenceOriginForBuild } from "./src/public-navigation.js";

export default defineConfig({
  resolve: { dedupe: ["react", "react-dom"] },
  define: {
    __TEMPO_STUDY_URL__: JSON.stringify(studyUrlForBuild(process.env.TEMPO_STUDY_URL)),
    __TEMPO_INTELLIGENCE_ORIGIN__: JSON.stringify(intelligenceOriginForBuild(process.env.TEMPO_INTELLIGENCE_ORIGIN)),
  },
});
