"""Existing bounded provider surface; import never performs acquisition."""
from ...chesscom_acquisition import ChesscomAcquisitionError, ChesscomGamePage, ChesscomSourceRecord, fetch_chesscom_page
from ...chesscom import fetch_chesscom_games, fetch_chesscom_archive_urls, fetch_chesscom_archive_month

__all__ = ['ChesscomAcquisitionError', 'ChesscomGamePage', 'ChesscomSourceRecord', 'fetch_chesscom_page', 'fetch_chesscom_games', 'fetch_chesscom_archive_urls', 'fetch_chesscom_archive_month']
