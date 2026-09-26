import type { PracticeCard } from "../domain/cards";

export default function StudyReviewBadge({
  count,
}: {
  count: PracticeCard["priorStudyReviewCount"];
}) {
  if (count === undefined) return null;

  const label = count === 0
    ? "First time"
    : `Seen before ${count} ${count === 1 ? "time" : "times"}`;

  return <span className="pill">{label}</span>;
}
