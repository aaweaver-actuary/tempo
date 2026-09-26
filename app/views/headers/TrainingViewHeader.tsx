interface TrainingViewHeaderProps {
  dateLabel: string;
  serviceError: string | null;
  cardsLeft: number;
  queueNotice?: string;
}

export default function TrainingViewHeader({
  dateLabel,
  serviceError,
  cardsLeft,
  queueNotice,
}: TrainingViewHeaderProps) {
  const preparedExerciseUnavailable = cardsLeft === 0 && /requires? the computer/.test(queueNotice ?? "");
  return (
    <section className="training-header">
      <div>
        <h1 className="sr-only">
          {serviceError
            ? "Local service unavailable"
            : preparedExerciseUnavailable
              ? "Prepared exercises unavailable offline"
              : cardsLeft === 0
              ? "You're done for today"
              : "Daily training"}
        </h1>
        {cardsLeft === 0 && queueNotice && <p role="status">{queueNotice}</p>}
      </div>
      <div className="session-count">
        <span>Today · {dateLabel}</span>
        <strong>{serviceError ? "—" : cardsLeft}</strong>
        <span>cards left</span>
      </div>
    </section>
  );
}
