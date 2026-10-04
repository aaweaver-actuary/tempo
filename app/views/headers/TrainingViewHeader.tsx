interface TrainingViewHeaderProps {
  dateLabel: string;
  serviceError: string | null;
  cardsLeft: number;
  queueNotice?: string;
  compact?: boolean;
}

export default function TrainingViewHeader({
  dateLabel,
  serviceError,
  cardsLeft,
  queueNotice,
  compact = false,
}: TrainingViewHeaderProps) {
  const preparedExerciseUnavailable = cardsLeft === 0 && /requires? the computer/.test(queueNotice ?? "");
  const pageHeading = (
    <h1 className="sr-only">
      {serviceError
        ? "Local service unavailable"
        : preparedExerciseUnavailable
          ? "Prepared exercises unavailable offline"
          : cardsLeft === 0
            ? "You're done for today"
            : "Daily training"}
    </h1>
  );
  if (compact) return pageHeading;
  return (
    <section className="training-header">
      <div>
        {pageHeading}
        {preparedExerciseUnavailable && <p role="status">Prepared exercises require the computer. Reconnect to continue.</p>}
      </div>
      <div className="session-count">
        <span>Today · {dateLabel}</span>
        <strong>{serviceError ? "—" : cardsLeft}</strong>
        <span>cards left</span>
      </div>
    </section>
  );
}
