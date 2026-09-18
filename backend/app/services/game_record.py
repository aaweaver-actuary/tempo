"""Intermediate game representation before database insertion."""

from datetime import date


class GameRecord:
    """Represents a single game fetched from an API, before normalization into database."""
    
    def __init__(self, provider: str, username: str):
        self.provider = provider  # "lichess" or "chess.com"
        self.username = username  # The player's username
        self.id = None  # Unique game ID from provider
        self.played_at = None  # Date game was played (datetime.date)
        self.speed = None  # Speed type (blitz, rapid, classical, etc.)
        self.rated = False  # Whether the game was rated
        self.color = None  # "white" or "black"
        self.result = None  # "1-0", "0-1", or "1/2-1/2"
        self.start_fen = None  # Starting FEN position
        self.uci_moves = []  # List of UCI move strings
        self.opening_name = None  # Opening name from API
        self.game_url = None  # URL to view the game
