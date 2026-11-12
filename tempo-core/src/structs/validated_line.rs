use crate::structs::line_diagnostic::LineDiagnostic;
use serde::{Deserialize, Serialize};

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
}
