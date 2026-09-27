use std::str::FromStr;

use shakmaty::uci::UciMove;

/// Returns `true` if the given UCI move string represents a null move, otherwise returns `false`.
pub fn is_null_move(value: &str) -> bool {
    matches!(value, "0000" | "--" | "z0")
}

/// Returns `true` if the given UCI move string is valid, otherwise returns `false`.
pub fn is_valid_uci_move(value: &str) -> bool {
    UciMove::from_str(value).is_ok()
}

// /// Returns `true` if the given UCI move string represents a legal move in the position,
// /// otherwise returns `false`.
// pub fn is_legal_uci_move(value: &str, position: &Chess) -> bool {
//     if let Ok(uci) = UciMove::from_str(value) {
//         if let Ok(chess_move) = uci.to_move(position) {
//             return position.legal_moves().any(|m| m == chess_move);
//         }
//     }
//     false
// }
