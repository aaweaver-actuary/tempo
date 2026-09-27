use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LineDiagnostic {
    pub ply: usize,
    pub chess_move: String,
    pub kind: String,
    pub message: String,
}

impl LineDiagnostic {
    /// Creates a new `LineDiagnostic` with the given ply, chess move, kind, and message.
    pub fn new(ply: usize, chess_move: &str, kind: &str, message: &str) -> Self {
        Self {
            ply,
            chess_move: chess_move.into(),
            kind: kind.into(),
            message: message.into(),
        }
    }

    pub fn null(ply: usize, chess_move: &str) -> Self {
        Self {
            ply,
            chess_move: chess_move.into(),
            kind: "null".into(),
            message: format!("Stopped at null move {chess_move}"),
        }
    }

    pub fn invalid(ply: usize, chess_move: &str, message: &str) -> Self {
        Self {
            ply,
            chess_move: chess_move.into(),
            kind: "invalid".into(),
            message: message.into(),
        }
    }

    pub fn illegal(ply: usize, chess_move: &str, message: &str) -> Self {
        Self {
            ply,
            chess_move: chess_move.into(),
            kind: "illegal".into(),
            message: message.into(),
        }
    }
}
