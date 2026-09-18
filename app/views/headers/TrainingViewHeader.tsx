interface TrainingViewHeaderProps {
  dateLabel: string;
  serviceError: string | null;
  cardsLeft: number;
}

export default function TrainingViewHeader({
  dateLabel,
  serviceError,
  cardsLeft,
}: TrainingViewHeaderProps) {
  return (
    <section className="training-header">
      <div>
        <h1 className="sr-only">
          {serviceError
            ? "Local service unavailable"
            : cardsLeft === 0
              ? "You're done for today"
              : "Daily training"}
        </h1>
      </div>
      <div className="session-count">
        <span>Today · {dateLabel}</span>
        <strong>{serviceError ? "—" : cardsLeft}</strong>
        <span>cards left</span>
      </div>
    </section>
  );
}
