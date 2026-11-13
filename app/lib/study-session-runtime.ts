/** Compatibility entry; default construction happens only on first use. */
import { defaultStudyRuntime } from './study-runtime-composition';
export async function studySession() {
  return defaultStudyRuntime().session();
}
