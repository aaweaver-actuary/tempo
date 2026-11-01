import { computeEssentialStudyTask } from "./essential-study-computation";
import { installStudyWorker } from "./study-worker-handler";

installStudyWorker(computeEssentialStudyTask);
