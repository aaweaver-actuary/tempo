use shakmaty::{fen::Fen, EnPassantMode};
use crate::domain::position_from_fen::position_from_fen;

/// Returns the canonical FEN key for the given FEN string, including only the first four fields.
pub fn canonical_fen_key_native(fen: &str) -> Result<String, String> {
    let position = position_from_fen(fen)?;
    Ok(Fen::from_position(&position, EnPassantMode::Legal)
        .to_string()
        .split_whitespace()
        .take(4)
        .collect::<Vec<_>>()
        .join(" "))
}