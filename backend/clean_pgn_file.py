#!/usr/bin/env python3
"""
clean_pgn_for_lichess.py

Clean instructional/course PGNs for Lichess.

Pipeline
--------
1. Parse each game with python-chess.
2. Apply conservative repairs for known-invalid constructs:
      - remove null-move ("--") subtrees
      - remove invalid ASCII control characters
3. Re-export the repaired game.
4. Parse the repaired PGN again from scratch.
5. If the repaired game is valid:
      -> write it to the clean output.
6. If it is still invalid:
      -> write it to --invalid-output if supplied
      -> otherwise discard it.

Installation
------------
    pip install python-chess

Example
-------
    python clean_pgn_for_lichess.py input.pgn \
        -o clean.pgn \
        --invalid-output rejected.pgn \
        --report report.txt
"""

from __future__ import annotations

import argparse
import io
import re
import unicodedata
from pathlib import Path

import chess
import chess.pgn


INVALID_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ============================================================
# Raw-text cleanup
# ============================================================


def clean_raw_text(text: str) -> tuple[str, int]:
    """
    Conservative cleanup before PGN parsing.

    Keeps legitimate Unicode, comments, arrows, [%cal], [%csl], etc.
    """

    text = text.replace("\ufeff", "")
    text = unicodedata.normalize("NFC", text)

    # Normalize newlines.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove genuinely invalid/non-printing ASCII controls.
    text, controls_removed = INVALID_CONTROL_CHARS.subn("", text)

    # Remove markdown bold formatting
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)

    # Remove $number annotations
    text = re.sub(r"\$\d+", "", text)

    # Remove "[--]"
    text = re.sub(r"\[\s*--\s*\]", "", text)

    # Remove "{ . }"
    text = re.sub(r"\{\s*\.\s*\}", "", text)

    return text, controls_removed


# ============================================================
# PGN tree helpers
# ============================================================


def walk_nodes(node: chess.pgn.GameNode):
    """Yield every descendant move node."""
    for child in node.variations:
        yield child
        yield from walk_nodes(child)


def count_nodes(node: chess.pgn.GameNode) -> int:
    """Count descendants below a node."""
    return sum(1 + count_nodes(child) for child in node.variations)


def prune_null_move_subtrees(
    node: chess.pgn.GameNode,
) -> tuple[int, int]:
    """
    Remove subtrees beginning with a null move.

    This is intentionally more conservative than simply deleting "--".

    Example:

        11. Na3 -- 12. f5

    becomes:

        11. Na3

    We do NOT turn it into:

        11. Na3 12. f5

    because the missing Black move makes the continuation semantically
    unreliable.

    Likewise:

        (7. -- Nh5 8. Bg5)

    is removed as an entire variation.

    Returns
    -------
    null_moves_removed
    nodes_removed
    """

    null_moves_removed = 0
    nodes_removed = 0

    kept_variations = []

    for child in node.variations:
        if child.move == chess.Move.null():
            null_moves_removed += 1

            # Null node itself + everything below it.
            nodes_removed += 1 + count_nodes(child)

            continue

        child_nulls, child_removed = prune_null_move_subtrees(child)

        null_moves_removed += child_nulls
        nodes_removed += child_removed

        kept_variations.append(child)

    node.variations[:] = kept_variations

    return null_moves_removed, nodes_removed


# ============================================================
# Serialization / validation
# ============================================================


def export_game(game: chess.pgn.Game) -> str:
    """Serialize a game into normalized PGN."""

    exporter = chess.pgn.StringExporter(
        headers=True,
        comments=True,
        variations=True,
        columns=None,
    )

    return game.accept(exporter)


def parse_single_game(
    pgn_text: str,
) -> chess.pgn.Game | None:
    """Parse exactly one serialized PGN game."""

    return chess.pgn.read_game(io.StringIO(pgn_text))


def validate_exported_game(
    pgn_text: str,
) -> tuple[bool, list[str]]:
    """
    Reparse the repaired PGN from scratch.

    This is the important final validation step.
    """

    reasons: list[str] = []

    try:
        game = parse_single_game(pgn_text)

    except Exception as exc:
        return False, [f"{type(exc).__name__}: {exc}"]

    if game is None:
        return False, ["Could not parse exported game"]

    for error in game.errors:
        reasons.append(f"{type(error).__name__}: {error}")

    # This should normally be zero because we already pruned them,
    # but verify rather than assume.
    remaining_nulls = [
        node for node in walk_nodes(game) if node.move == chess.Move.null()
    ]

    if remaining_nulls:
        reasons.append(f"{len(remaining_nulls)} null move(s) remain after repair")

    return len(reasons) == 0, reasons


# ============================================================
# Reporting
# ============================================================


def game_description(
    game: chess.pgn.Game,
    game_number: int,
) -> str:

    event = game.headers.get("Event", "?")
    white = game.headers.get("White", "?")
    black = game.headers.get("Black", "?")

    return f"Game {game_number}: {white} vs {black} [{event}]"


# ============================================================
# Main cleaner
# ============================================================


def clean_pgn(
    input_path: Path,
    output_path: Path,
    invalid_output_path: Path | None = None,
    report_path: Path | None = None,
) -> None:

    raw_bytes = input_path.read_bytes()

    raw_text = raw_bytes.decode(
        "utf-8-sig",
        errors="replace",
    )

    bad_utf8_count = raw_text.count("\ufffd")

    cleaned_text, controls_removed = clean_raw_text(raw_text)

    handle = io.StringIO(cleaned_text)

    clean_games: list[str] = []
    invalid_games: list[str] = []

    details: list[str] = []

    total_games = 0
    clean_count = 0
    rejected_count = 0

    repaired_games = 0
    total_nulls_removed = 0
    total_nodes_removed = 0

    while True:
        try:
            game = chess.pgn.read_game(handle)

        except Exception as exc:
            details.extend(
                [
                    "",
                    (f"UNRECOVERABLE INPUT PARSE ERROR near game {total_games + 1}"),
                    f"  {type(exc).__name__}: {exc}",
                ]
            )

            break

        if game is None:
            break

        total_games += 1

        description = game_description(
            game,
            total_games,
        )

        # ----------------------------------------------------
        # Phase 1: repair known-invalid constructs
        # ----------------------------------------------------

        nulls_removed, nodes_removed = prune_null_move_subtrees(game)

        if nulls_removed:
            repaired_games += 1
            total_nulls_removed += nulls_removed
            total_nodes_removed += nodes_removed

        # ----------------------------------------------------
        # Phase 2: export normalized repaired PGN
        # ----------------------------------------------------

        repaired_pgn = export_game(game)

        # ----------------------------------------------------
        # Phase 3: reparse from scratch
        # ----------------------------------------------------

        is_valid, reasons = validate_exported_game(repaired_pgn)

        if is_valid:
            clean_count += 1
            clean_games.append(repaired_pgn)

            if nulls_removed:
                details.extend(
                    [
                        "",
                        description,
                        "  REPAIRED AND ACCEPTED",
                        (f"    - Removed {nulls_removed} null-move subtree(s)"),
                        (f"    - Removed {nodes_removed} total move node(s)"),
                    ]
                )

            continue

        # ----------------------------------------------------
        # Still invalid after repair
        # ----------------------------------------------------

        rejected_count += 1

        details.extend(
            [
                "",
                description,
                "  REJECTED AFTER REPAIR",
            ]
        )

        if nulls_removed:
            details.append(
                (f"    - Initially removed {nulls_removed} null-move subtree(s)")
            )

        for reason in reasons:
            details.append(f"    - {reason}")

        if invalid_output_path is not None:
            invalid_games.append(repaired_pgn)

    # ========================================================
    # Write clean output
    # ========================================================

    clean_text = ""

    if clean_games:
        clean_text = "\n\n".join(clean_games).rstrip() + "\n"

    output_path.write_text(
        clean_text,
        encoding="utf-8",
        newline="\n",
    )

    # ========================================================
    # Write rejected games if requested
    # ========================================================

    if invalid_output_path is not None:
        invalid_text = ""

        if invalid_games:
            invalid_text = "\n\n".join(invalid_games).rstrip() + "\n"

        invalid_output_path.write_text(
            invalid_text,
            encoding="utf-8",
            newline="\n",
        )

    # ========================================================
    # Report
    # ========================================================

    summary = [
        "PGN CLEANING / VALIDATION REPORT",
        "=" * 70,
        "",
        f"Input:                     {input_path}",
        f"Clean output:              {output_path}",
        (
            f"Rejected output:           {invalid_output_path}"
            if invalid_output_path is not None
            else "Rejected output:           DISCARDED"
        ),
        "",
        f"Games read:                {total_games}",
        f"Clean games:               {clean_count}",
        f"Rejected games:            {rejected_count}",
        f"Games repaired:            {repaired_games}",
        "",
        f"Null moves removed:        {total_nulls_removed}",
        f"Move nodes removed:        {total_nodes_removed}",
        "",
        f"Control chars removed:     {controls_removed}",
        f"Bad UTF-8 replacements:    {bad_utf8_count}",
        "",
        "PROCESS:",
        "  1. Parse source game",
        "  2. Remove known-invalid constructs",
        "  3. Export normalized PGN",
        "  4. Reparse exported PGN from scratch",
        "  5. Accept only if reparsed game is valid",
    ]

    full_report = "\n".join(summary + details) + "\n"

    print(full_report)

    if report_path is not None:
        report_path.write_text(
            full_report,
            encoding="utf-8",
        )


# ============================================================
# CLI
# ============================================================


def default_output_path(
    input_path: Path,
) -> Path:

    return input_path.with_name(f"{input_path.stem}.lichess-clean{input_path.suffix}")


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Repair known PGN problems, then strictly validate games for Lichess."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Source PGN",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Clean PGN output",
    )

    parser.add_argument(
        "-i",
        "--invalid-output",
        type=Path,
        help=("Optional output for games that remain invalid after repairs"),
    )

    parser.add_argument(
        "-r",
        "--report",
        type=Path,
        help="Optional validation/repair report",
    )

    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"Input file does not exist: {args.input}")

    output_path = args.output or default_output_path(args.input)

    clean_pgn(
        input_path=args.input,
        output_path=output_path,
        invalid_output_path=args.invalid_output,
        report_path=args.report,
    )


if __name__ == "__main__":
    main()
