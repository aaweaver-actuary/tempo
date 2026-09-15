use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use shakmaty::{fen::Fen, uci::UciMove, CastlingMode, Chess, EnPassantMode, Position};
use std::{collections::BTreeMap, str::FromStr};
use wasm_bindgen::prelude::*;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LineDiagnostic {
    pub ply: usize,
    pub chess_move: String,
    pub kind: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ValidatedLine {
    pub starting_fen: String,
    pub moves: Vec<String>,
    pub final_fen: String,
    pub diagnostics: Vec<LineDiagnostic>,
}

fn position_from_fen(fen: &str) -> Result<Chess, String> {
    Fen::from_ascii(fen.trim().as_bytes())
        .map_err(|error| error.to_string())?
        .into_position(CastlingMode::Standard)
        .map_err(|error| error.to_string())
}

pub fn canonical_fen_key_native(fen: &str) -> Result<String, String> {
    let position = position_from_fen(fen)?;
    Ok(Fen::from_position(&position, EnPassantMode::Legal)
        .to_string()
        .split_whitespace()
        .take(4)
        .collect::<Vec<_>>()
        .join(" "))
}

pub fn validate_uci_line_native(starting_fen: &str, input: &[String]) -> ValidatedLine {
    let mut diagnostics = Vec::new();
    let mut moves = Vec::new();
    let mut position = match position_from_fen(starting_fen) {
        Ok(position) => position,
        Err(message) => {
            diagnostics.push(LineDiagnostic {
                ply: 0,
                chess_move: String::new(),
                kind: "invalid".into(),
                message,
            });
            return ValidatedLine {
                starting_fen: starting_fen.into(),
                moves,
                final_fen: starting_fen.into(),
                diagnostics,
            };
        }
    };
    for (ply, value) in input.iter().enumerate() {
        let value = value.trim().to_ascii_lowercase();
        if matches!(value.as_str(), "0000" | "--" | "z0") {
            diagnostics.push(LineDiagnostic {
                ply,
                chess_move: value.clone(),
                kind: "null".into(),
                message: format!("Stopped at null move {value}"),
            });
            break;
        }
        let uci = match UciMove::from_str(&value) {
            Ok(uci) => uci,
            Err(error) => {
                diagnostics.push(LineDiagnostic {
                    ply,
                    chess_move: value.clone(),
                    kind: "invalid".into(),
                    message: error.to_string(),
                });
                break;
            }
        };
        let played = match uci.to_move(&position) {
            Ok(chess_move) => chess_move,
            Err(error) => {
                diagnostics.push(LineDiagnostic {
                    ply,
                    chess_move: value.clone(),
                    kind: "illegal".into(),
                    message: error.to_string(),
                });
                break;
            }
        };
        moves.push(UciMove::from_move(played, CastlingMode::Standard).to_string());
        position = match position.clone().play(played) {
            Ok(next) => next,
            Err(error) => {
                diagnostics.push(LineDiagnostic {
                    ply,
                    chess_move: value,
                    kind: "illegal".into(),
                    message: error.to_string(),
                });
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

pub fn card_id_native(starting_fen: &str, moves: &[String]) -> Result<String, String> {
    let fen = canonical_fen_key_native(starting_fen)?;
    let normalized_moves = moves
        .iter()
        .map(|value| value.trim())
        .collect::<Vec<_>>()
        .join(" ");
    let mut hasher = Sha256::new();
    hasher.update(format!("{fen}\n{normalized_moves}").as_bytes());
    Ok(hex::encode(hasher.finalize()))
}

fn piece_groups(position: &Chess) -> BTreeMap<String, Vec<String>> {
    let mut groups: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for square in shakmaty::Square::ALL {
        if let Some(piece) = position.board().piece_at(square) {
            groups
                .entry(format!("{:?}{:?}", piece.color, piece.role))
                .or_default()
                .push(square.to_string());
        }
    }
    groups
}

pub fn position_distance_native(left_fen: &str, right_fen: &str) -> Option<usize> {
    let left_key = canonical_fen_key_native(left_fen).ok()?;
    let right_key = canonical_fen_key_native(right_fen).ok()?;
    let left_state = left_key.split_whitespace().collect::<Vec<_>>();
    let right_state = right_key.split_whitespace().collect::<Vec<_>>();
    if left_state.get(1..4) != right_state.get(1..4) {
        return None;
    }
    let left = position_from_fen(left_fen).ok()?;
    let right = position_from_fen(right_fen).ok()?;
    let left_groups = piece_groups(&left);
    let right_groups = piece_groups(&right);
    if left_groups.keys().collect::<Vec<_>>() != right_groups.keys().collect::<Vec<_>>()
        || left_groups
            .iter()
            .any(|(key, values)| values.len() != right_groups.get(key).map_or(0, Vec::len))
    {
        return None;
    }
    Some(
        left_groups
            .iter()
            .map(|(key, values)| {
                let other = &right_groups[key];
                values
                    .iter()
                    .filter(|square| !other.contains(square))
                    .count()
            })
            .sum(),
    )
}

#[wasm_bindgen]
pub fn core_version() -> String {
    env!("CARGO_PKG_VERSION").into()
}

#[wasm_bindgen]
pub fn canonical_fen_key(fen: &str) -> Result<String, JsError> {
    canonical_fen_key_native(fen).map_err(|message| JsError::new(&message))
}

#[wasm_bindgen]
pub fn validate_uci_line(starting_fen: &str, moves: JsValue) -> Result<JsValue, JsError> {
    let moves: Vec<String> = serde_wasm_bindgen::from_value(moves)?;
    Ok(serde_wasm_bindgen::to_value(&validate_uci_line_native(
        starting_fen,
        &moves,
    ))?)
}

#[wasm_bindgen]
pub fn card_id(starting_fen: &str, moves: JsValue) -> Result<String, JsError> {
    let moves: Vec<String> = serde_wasm_bindgen::from_value(moves)?;
    card_id_native(starting_fen, &moves).map_err(|message| JsError::new(&message))
}

#[wasm_bindgen]
pub fn position_distance(left_fen: &str, right_fen: &str) -> i32 {
    position_distance_native(left_fen, right_fen)
        .map(|value| value as i32)
        .unwrap_or(-1)
}

/// Touch the maintained FSRS implementation in this scheduling-only crate. The
/// browser adapter will expose the full review transition after parity fixtures
/// are locked against the current Python scheduler.
pub fn fsrs_core_available() -> bool {
    let _scheduler = fsrs::FSRS::default();
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    const START: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

    #[test]
    fn null_moves_truncate_without_panicking() {
        let moves = vec!["e2e4".into(), "e7e5".into(), "0000".into(), "g1f3".into()];
        let result = validate_uci_line_native(START, &moves);
        assert_eq!(result.moves, vec!["e2e4", "e7e5"]);
        assert_eq!(result.diagnostics[0].kind, "null");
    }

    #[test]
    fn identity_ignores_fen_clocks() {
        let a = card_id_native("8/8/8/8/8/8/8/K6k w - - 0 1", &["a1a2".into()]).unwrap();
        let b = card_id_native("8/8/8/8/8/8/8/K6k w - - 17 42", &["a1a2".into()]).unwrap();
        assert_eq!(a, b);
    }

    #[test]
    fn identity_matches_python_golden_fixture() {
        let id = card_id_native(START, &["e2e4".into(), "e7e5".into()]).unwrap();
        assert_eq!(
            id,
            "4f1415440718daa4b13b5cf818644caccae3f5968d6756159ff366a2a10bda09"
        );
    }

    #[test]
    fn distance_requires_compatible_state_and_material() {
        let same = position_distance_native(START, START);
        assert_eq!(same, Some(0));
        let wrong_turn = START.replace(" w ", " b ");
        assert_eq!(position_distance_native(START, &wrong_turn), None);
    }

    #[test]
    fn maintained_fsrs_is_linked() {
        assert!(fsrs_core_available());
    }
}
