/** Existing package implementation, with function identity and behavior retained. */
export { createStudySession } from "../index.js";
export type { LearningClient, SessionPhase, SessionSnapshot, Settlement, StudySession } from "../index.js";

export type StudySessionOptions = Parameters<typeof import("../index.js").createStudySession>[0];
