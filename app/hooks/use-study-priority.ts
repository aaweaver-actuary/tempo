import { useEffect } from 'react';
import { LEARNING_URL, independentStudy, sendStudyActivity } from '../lib/study-connection';
import { usesLocalApi } from '../utils/local';
import { holdStudyPriority, installStudyInteractionPriority, setStudyOnlyPreparationPaused, setStudyPriorityScope } from '../lib/study-priority';

export function useStudyPriority(studying: boolean, studyOnlyMode: boolean | null, operationPending = false) {
  useEffect(() => { setStudyPriorityScope(studying); return () => setStudyPriorityScope(false); }, [studying]);
  useEffect(() => { if(studying && operationPending) return holdStudyPriority(); },[studying,operationPending]);
  useEffect(() => { setStudyOnlyPreparationPaused(studyOnlyMode !== false); }, [studyOnlyMode]);
  useEffect(() => installStudyInteractionPriority(independentStudy ? sendStudyActivity : usesLocalApi()
    ? (session, remainingMs) => globalThis.fetch(`${LEARNING_URL}/api/system/browser-activity?session_id=${session}&expires_in_ms=${remainingMs}`, {
      method: 'POST', headers: { 'X-Tempo-Work-Class': 'background' }, signal: AbortSignal.timeout(500),
    }) : undefined), []);
}
