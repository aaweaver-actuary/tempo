"""Publication receipts for unchanged, completely indexed game sources."""
import hashlib
import json

INDEX_ALGORITHM_VERSION = 1


def source_fingerprint(start_fen: str, moves_json: str) -> str:
    """Hash the copied source after its read connection has closed."""
    return hashlib.sha256(json.dumps([start_fen, moves_json], separators=(',', ':')).encode()).hexdigest()


def current_index_receipt(database, game_id):
    return database.execute(
        'SELECT source.*,job.published_position_version FROM game_position_index_sources source '
        'JOIN game_derivation_jobs job ON job.game_id=source.game_id '
        'AND job.published_position_version=source.derivation_version '
        'WHERE source.game_id=?', (game_id,),
    ).fetchone()


def can_reuse_index(receipt, fingerprint):
    return bool(receipt and receipt['source_fingerprint'] == fingerprint
                and receipt['algorithm_version'] == INDEX_ALGORITHM_VERSION
                and receipt['verified_from_start'] == 1 and receipt['published_at']
                and int(receipt['published_position_version']) > 0
                and int(receipt['position_count'] or 0) > 0)
