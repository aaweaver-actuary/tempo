import { computeOptionalStudyTask } from "./optional-study-computation";
import { installStudyWorker } from "./study-worker-handler";

installStudyWorker(computeOptionalStudyTask);
