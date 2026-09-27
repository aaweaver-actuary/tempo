use shakmaty::{fen::Fen, CastlingMode, Chess};

/// Returns a `Chess` position from the given FEN string, or an error message if the FEN is invalid.
pub fn position_from_fen(fen: &str) -> Result<Chess, String> {
    Fen::from_ascii(fen.trim().as_bytes())
        .map_err(|error| error.to_string())?
        .into_position(CastlingMode::Standard)
        .map_err(|error| error.to_string())
}
