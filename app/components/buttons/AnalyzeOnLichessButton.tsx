import { lichessAnalysisUrl } from "@/app/utils/urls";

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
    <a href={analysisUrl} onClick={onClick} target="_blank" rel="noreferrer">
      ↗ <span>Analyze</span>
    </a>
  );
}
