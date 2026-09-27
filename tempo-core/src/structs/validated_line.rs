use std::str::FromStr;

use crate::domain::{canonical_fen_key_native, is_null_move, is_valid_uci_move, position_from_fen};
use crate::structs::line_diagnostic::LineDiagnostic;
use serde::{Deserialize, Serialize};
use shakmaty::Position;
use shakmaty::{fen::Fen, uci::UciMove, CastlingMode, Chess, EnPassantMode};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ValidatedLine {
    pub starting_fen: String,
    pub moves: Vec<String>,
    pub final_fen: String,
    pub diagnostics: Vec<LineDiagnostic>,
}

impl ValidatedLine {
    /// Creates a new `ValidatedLine` with the given starting FEN, moves, final FEN, and diagnostics.
    pub fn new(
        starting_fen: String,
        moves: Vec<String>,
        final_fen: String,
        diagnostics: Vec<LineDiagnostic>,
    ) -> Self {
        Self {
            starting_fen,
            moves,
            final_fen,
            diagnostics,
        }
    }

    pub fn from_uci(starting_fen: &str, uci_moves: Vec<String>) -> Self {
        let mut diagnostics = Vec::new();
        let mut moves = Vec::new();
        let mut position = match position(starting_fen, &mut diagnostics, &moves) {
            Ok(value) => value,
            Err(value) => return value,
        };

        for (ply, value) in uci_moves.iter().enumerate() {
            let value = value.trim().to_ascii_lowercase();
            // If the move is a null move indicator, stop processing further moves.
            if is_null_move(value.as_str()) {
                diagnostics.push(LineDiagnostic::null(ply, &value));
                break;
            }

            if !is_valid_uci_move(value.as_str()) {
                diagnostics.push(LineDiagnostic::invalid(ply, &value, "Invalid UCI move"));
                break;
            }

            let uci = match UciMove::from_str(&value) {
                Ok(uci) => uci,
                Err(error) => {
                    diagnostics.push(LineDiagnostic::invalid(ply, &value, &error.to_string()));
                    break;
                }
            };
            let played = match uci.to_move(&position) {
                Ok(chess_move) => chess_move,
                Err(error) => {
                    diagnostics.push(LineDiagnostic::illegal(ply, &value, &error.to_string()));
                    break;
                }
            };
            moves.push(UciMove::from_move(played, CastlingMode::Standard).to_string());
            position = match position.clone().play(played) {
                Ok(next) => next,
                Err(error) => {
                    diagnostics.push(LineDiagnostic::illegal(ply, &value, &error.to_string()));
                    break;
                }
            };
        }
        let final_fen = Fen::from_position(&position, EnPassantMode::Legal).to_string();
        ValidatedLine {
            starting_fen: canonical_fen_key_native(starting_fen)
                .unwrap_or_else(|_| starting_fen.into()),
            moves,
            final_fen,
            diagnostics,
        }
    }
}

/// Attempts to create a `Chess` position from the given starting FEN.
/// If the FEN is invalid, it records an invalid line diagnostic and returns a `ValidatedLine` with the error.
fn position(
    starting_fen: &str,
    diagnostics: &mut Vec<LineDiagnostic>,
    moves: &[String],
) -> Result<Chess, ValidatedLine> {
    let line_moves = moves.to_vec();
    Ok(match position_from_fen(starting_fen) {
        Ok(position) => position,
        Err(message) => {
            diagnostics.push(LineDiagnostic::invalid(0, "", &message));
            return Err(ValidatedLine {
                starting_fen: starting_fen.into(),
                moves: line_moves,
                final_fen: starting_fen.into(),
                diagnostics: diagnostics.clone(),
            });
        }
    })
}
