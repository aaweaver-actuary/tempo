import { Feedback } from "../../types";

interface FeedbackIconProps {
  feedback: Feedback;
}

export default function FeedbackIcon({ feedback }: FeedbackIconProps) {
  return (
    <span className="feedback-icon">
      {feedback === "wrong"
        ? "×"
        : feedback === "complete"
          ? "✓"
          : feedback === "ready" || feedback === "branch"
            ? ""
            : "●"}
    </span>
  );
}
