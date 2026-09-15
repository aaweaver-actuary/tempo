import { lichessAnalysisUrl } from "../utils/urls";

export default function AnalyzeThisButton({
  line,
  ply,
}: {
  line: string[];
  ply: number;
}) {
  return (
    <a
      className="tree-analysis"
      href={lichessAnalysisUrl(line.slice(0, ply))}
      target="_blank"
      rel="noreferrer"
    >
      ↗ Analyze this position on Lichess
    </a>
  );
}
