interface FeedbackTextProps {
  title: string;
  body: string;
}

export default function FeedbackText({ title, body }: FeedbackTextProps) {
  return (
    <div>
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}
