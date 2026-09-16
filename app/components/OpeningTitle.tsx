interface OpeningTitleProps {
  card: {
    title: string;
    subtitle: string;
  };
}

export default function OpeningTitle({ card }: OpeningTitleProps) {
  return (
    <div className="opening-title">
      <h2>{card.title}</h2>
      <span>{card.subtitle}</span>
    </div>
  );
}
