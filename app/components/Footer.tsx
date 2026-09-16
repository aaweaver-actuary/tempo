export default function Footer() {
  return (
    <footer className="source-footer">
      Board interaction by{" "}
      <a
        href="https://github.com/lichess-org/chessground"
        target="_blank"
        rel="noreferrer"
      >
        Chessground
      </a>{" "}
      · Woodland sounds and chess assets from{" "}
      <a
        href="https://github.com/lichess-org/lila"
        target="_blank"
        rel="noreferrer"
      >
        Lichess
      </a>{" "}
      under AGPL-3.0+ · Puzzle positions from the public-domain{" "}
      <a
        href="https://database.lichess.org/#puzzles"
        target="_blank"
        rel="noreferrer"
      >
        Lichess database
      </a>
    </footer>
  );
}
