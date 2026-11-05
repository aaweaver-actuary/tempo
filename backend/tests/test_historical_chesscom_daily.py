"""Chess.com daily mapping preserves the original provider fact."""


def test_daily_mapping_uses_original_provider_fact():
    from test_product_source_identity import inputs
    from tempo_legacy_database.adapters.games.product_sources import _prepare
    record, spec, _ = inputs(provider='chesscom')
    record = record.model_copy(update={'source_metadata':(('rated','true'),('time_class','"daily"'))})
    assert _prepare(record, spec).speed == 'classical'
    assert dict(record.source_metadata)['time_class'] == '"daily"'
