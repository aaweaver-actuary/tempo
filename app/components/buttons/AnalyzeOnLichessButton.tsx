import { lichessAnalysisUrl } from "@/app/utils/urls";
import { ActionLink } from "../ui";

interface AnalyzeOnLichessButtonProps {
  moves: string[];
  fen: string;
  onClick: () => void;
}

export default function AnalyzeOnLichessButton({
  moves,
  fen,
  onClick,
}: AnalyzeOnLichessButtonProps) {
  const analysisUrl = lichessAnalysisUrl(moves, fen);
  return (
    <ActionLink href={analysisUrl} onClick={onClick} target="_blank" rel="noreferrer">
      ↗ <span>Analyze</span>
    </ActionLink>
  );
}
