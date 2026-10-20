"""Stable source projection fields shared by legacy evidence read adapters."""
CARD_SOURCE_FIELDS=('id','revision','start_fen','moves_json','trained_color','content_type','archived',
    'pending_validation','superseded_by','repertoire_id','canonical_route_source','introduced_at')
GAME_SOURCE_FIELDS=('id','provider','provider_game_id','content_hash','played_at','color','result','start_fen',
    'moves_json','adaptive_excluded')
