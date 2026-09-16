interface FailureNoteProps {
  failureAnnotation: {
    comment: string;
  };
}

export default function FailureNote({ failureAnnotation }: FailureNoteProps) {
  return (
    <div className="failure-note" role="note">
      <strong>Note for this position</strong>
      <p>{failureAnnotation.comment}</p>
    </div>
  );
}
