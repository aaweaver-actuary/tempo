import { readAuthorityRegistry, readRetainedStudyData, inspectRetainedStudyAuthority, readSessionConnection, removeSessionConnection,
  sessionConnectionAvailable, writeAuthorityRegistry, writeSessionConnection } from "../../database/browser/study-connection-storage";
import { holdActiveStudyRequest } from "./study-priority";
import { API_URL } from "../const";

declare const __TEMPO_STUDY__: boolean;
export const independentStudy = typeof __TEMPO_STUDY__ !== "undefined" && __TEMPO_STUDY__;
export { studySettingsSchema, studyBootstrapSchema, StudyConnectionError } from "./study-client";
export type { StudyBootstrap, StudyClient, StudyClientStorage } from "./study-client";
import { createStudyClient as createExplicitStudyClient, type StudyClientConfiguration as ExplicitStudyClientConfiguration,
  type StudyClientStorage } from "./study-client";

/** Browser compatibility defaults remain here, never in the explicit factory. */
export type StudyClientConfiguration = Omit<ExplicitStudyClientConfiguration,
  "resolveBaseHref" | "onAuthenticationRequired" | "acquireActivityHold"> &
  Partial<Pick<ExplicitStudyClientConfiguration, "resolveBaseHref" | "onAuthenticationRequired" | "acquireActivityHold">>;
export function createStudyClient(configuration: StudyClientConfiguration) {
  return createExplicitStudyClient({ ...configuration,
    resolveBaseHref: configuration.resolveBaseHref ?? (() => typeof window === "undefined" ? "http://tempo.local" : window.location.href),
    onAuthenticationRequired: configuration.onAuthenticationRequired ?? (() => window.dispatchEvent(new Event("tempo:study-auth-required"))),
    acquireActivityHold: configuration.acquireActivityHold ?? holdActiveStudyRequest,
  });
}

const browserStorage: StudyClientStorage = { readAuthorityRegistry, readRetainedStudyData, inspectRetainedStudyAuthority, readSessionConnection,
  removeSessionConnection, sessionConnectionAvailable, writeAuthorityRegistry, writeSessionConnection };
export let LEARNING_URL = API_URL;
export const defaultStudyClient = createStudyClient({ independent: independentStudy, baseUrl: API_URL, storage: browserStorage,
  transport: (input, init) => globalThis.fetch(input, init), persistSession: true,
  onUrlChange: value => { LEARNING_URL = value; } });
export const studyConnection = defaultStudyClient.studyConnection;
export const studyWorkspace = defaultStudyClient.studyWorkspace;
export const studyOfflineDatabaseName = defaultStudyClient.studyOfflineDatabaseName;
export const learningDayKey = defaultStudyClient.learningDayKey;
export const sendStudyActivity = defaultStudyClient.sendStudyActivity;
export const learningFetch = defaultStudyClient.learningFetch;
export const retainedStudyData = defaultStudyClient.retainedStudyData;
export const assertStudyAuthority = defaultStudyClient.assertStudyAuthority;
export const connectStudy = defaultStudyClient.connectStudy;
export const disconnectStudy = defaultStudyClient.disconnectStudy;
