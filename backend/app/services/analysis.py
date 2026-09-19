from dataclasses import dataclass
from urllib.parse import urlencode


STOCKFISH_VERSION = 19
MAIA_VERSION = 3
EXPLORER_ENDPOINT = "https://explorer.lichess.org/lichess"


@dataclass(frozen=True)
class AnalysisCapabilities:
    """Represents the analysis capabilities of the local Tempo instance."""

    stockfish_version: int = STOCKFISH_VERSION
    maia_version: int = MAIA_VERSION
    stockfish_runtime: str = "local-wasm-adapter"
    maia_runtime: str = "local-onnx-adapter"
    explorer_endpoint: str = EXPLORER_ENDPOINT


def explorer_url(fen: str) -> str:
    """Build the public Lichess Explorer URL used by the local analysis client."""
    query = urlencode(
        {
            "variant": "standard",
            "fen": fen,
            "speeds": "rapid,classical",
            "ratings": "1200,1400,1600,1800,2000,2200,2500",
        }
    )
    return f"{EXPLORER_ENDPOINT}?{query}"
