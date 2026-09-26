import { lichessAnalysisUrl } from "../../utils/urls";
import { ActionLink } from "../ui";

export default function AnalyzeThisButton({
  line,
  ply,
}: {
  line: string[];
  ply: number;
}) {
  return (
    <ActionLink
      className="tree-analysis"
      href={lichessAnalysisUrl(line.slice(0, ply))}
      target="_blank"
      rel="noreferrer"
    >
      ↗ Analyze this position on Lichess
    </ActionLink>
  );
}
