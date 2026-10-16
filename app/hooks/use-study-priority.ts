import { useEffect } from 'react';
import { LEARNING_URL, independentStudy } from '../lib/study-connection';
import { usesLocalApi } from '../utils/local';
import { holdStudyPriority, installStudyInteractionPriority, setStudyOnlyPreparationPaused, setStudyPriorityScope } from '../lib/study-priority';

export function useStudyPriority(studying: boolean, studyOnlyMode: boolean | null, operationPending = false) {
  useEffect(() => { setStudyPriorityScope(studying); return () => setStudyPriorityScope(false); }, [studying]);
  useEffect(() => { if(studying && operationPending) return holdStudyPriority(); },[studying,operationPending]);
  useEffect(() => { setStudyOnlyPreparationPaused(studyOnlyMode !== false); }, [studyOnlyMode]);
  useEffect(() => installStudyInteractionPriority(usesLocalApi() && !independentStudy
    ? session => globalThis.fetch(`${LEARNING_URL}/api/system/browser-activity?session_id=${session}`, {
      method: 'POST', headers: { 'X-Tempo-Work-Class': 'background' }, signal: AbortSignal.timeout(500),
    }) : undefined), []);
}
