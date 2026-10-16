"""Public external pilot entrypoints. Source ownership remains with A/B/C."""
from .cli import main
from .inputs import normalize
from .stockfish_client import connect
__all__=["main","normalize","connect"]
