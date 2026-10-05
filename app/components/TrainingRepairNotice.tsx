import { Button } from "./buttons/BaseButton";
import { usePhoneViewport } from "../hooks/use-phone-viewport";

export function TrainingRepairNotice({ blockedDue, issueCount, onResume }: {
  blockedDue: number; issueCount: number; onResume: () => void;
}) {
  const phoneViewport = usePhoneViewport();
  const cardsLabel = `${blockedDue} opening card${blockedDue === 1 ? "" : "s"}`;
  const issuesLabel = `${issueCount} issue${issueCount === 1 ? "" : "s"} remaining.`;
  return <div className="integrity-train-notice" role="status">
    <strong>{cardsLabel} paused{phoneViewport ? "." : " by repertoire repair."}</strong>
    <span>{!phoneViewport && "Unaffected openings and tactics remain available · "}{issuesLabel}</span>
    <Button onClick={onResume}>Resume repair</Button>
    {phoneViewport && <details className="repair-explanation">
      <summary>Why paused?</summary>
      <p>These cards are paused by repertoire repair. Unaffected openings and tactics remain available.</p>
    </details>}
  </div>;
}
