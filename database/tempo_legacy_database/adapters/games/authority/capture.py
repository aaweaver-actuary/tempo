"""Existing finite source stream metadata and count/highwater/page queries."""

from dataclasses import dataclass

from .headers import _CARD_COLUMNS, _REPERTOIRE_COLUMNS


@dataclass(frozen=True)
class Stream:
    name: str
    columns: str
    source: str
    keys: tuple[str, ...]
    parameters: tuple = ()

    def key(self, row):
        return tuple(row[key.rsplit(".", 1)[-1]] for key in self.keys)


def stream_count(reader, stream):
    return reader(f"/*legacy-stream:{stream.name}:count*/ SELECT COUNT(*) row_count FROM " +
        stream.source, stream.parameters)[0]["row_count"]


def stream_highwater(reader, stream):
    return reader(f"/*legacy-stream:{stream.name}:highwater*/ SELECT " + ",".join(stream.keys) +
        " FROM " + stream.source + " ORDER BY " + ",".join(key + " DESC" for key in stream.keys) +
        " LIMIT 1", stream.parameters)


def stream_page(reader, stream, cursor, highwater):
    keys = "ROW(" + ",".join(stream.keys) + ")"
    marks = "ROW(" + ",".join("%s" for _ in stream.keys) + ")"
    # PostgreSQL compares seek/highwater keys in its own collation.
    statement = (f"/*legacy-stream:{stream.name}:page*/ SELECT " + stream.columns +
        f",{keys}>{marks} AS _seek_advanced FROM " + stream.source +
        f" AND {keys}>{marks} AND {keys}<={marks} ORDER BY " + ",".join(stream.keys) + " LIMIT 64")
    return reader(statement, (*cursor, *stream.parameters, *cursor, *highwater))


def source_streams(repertoire_id, graph_generation):
    return [
        Stream("repertoires", _REPERTOIRE_COLUMNS, "repertoires WHERE left(id,2)<>'__'", ("id",)),
        Stream("repertoire_lines", "id,repertoire_id,trained_color,start_fen,moves_json",
            "repertoire_lines WHERE left(repertoire_id,2)<>'__'", ("id",)),
        Stream("graphs", "*", "opening_graph_steps WHERE repertoire_id=%s AND generation=%s",
            ("line_id", "decision_index"), (repertoire_id, graph_generation)),
        Stream("cards", ",".join("card." + key for key in _CARD_COLUMNS.split(",")) +
            ",EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id) integrity_blocked,"
            "EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id) deleted",
            "cards card WHERE (EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id=%s) "
            "OR EXISTS(SELECT 1 FROM opening_graph_steps graph WHERE graph.card_id=card.id AND graph.repertoire_id=%s "
            "AND graph.generation=%s)) AND card.content_type='opening'",
            ("card.id",), (repertoire_id, repertoire_id, graph_generation)),
        Stream("repertoire_cards", "link.repertoire_id,link.card_id,link.canonical_route_source",
            "repertoire_cards link WHERE (EXISTS(SELECT 1 FROM repertoire_cards selected "
            "WHERE selected.card_id=link.card_id AND selected.repertoire_id=%s) OR EXISTS(SELECT 1 FROM opening_graph_steps graph "
            "WHERE graph.card_id=link.card_id AND graph.repertoire_id=%s AND graph.generation=%s))",
            ("link.repertoire_id", "link.card_id"), (repertoire_id, repertoire_id, graph_generation)),
    ]
