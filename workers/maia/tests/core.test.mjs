import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Chess } from 'chess.js';
import { decodePredictions, mirrorFen, mirrorMove, preparePosition, SUPPORTED_RATINGS, validatePosition, validateRating, validateVocabularies, VOCABULARY_SIZE } from '../core.mjs';

const forward = JSON.parse(await readFile(new URL('../assets/move-index.json', import.meta.url), 'utf8'));
const reverse = JSON.parse(await readFile(new URL('../assets/reverse-index.json', import.meta.url), 'utf8'));
const opening = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const positions = {
  'white opening': opening,
  'black opening': 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
  'white castling': 'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',
  'black castling': 'r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1',
  'white en passant': '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
  'black en passant': '4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1',
  'white promotions': '4k3/P7/8/8/8/8/8/4K3 w - - 0 1',
  'black promotions': '4k3/8/8/8/8/8/p7/4K3 b - - 0 1',
  'pinned piece and check': '4r1k1/8/8/8/8/8/4R3/4K3 w - - 0 1',
};
const assertCode = (action, code) => assert.throws(action, (error) => error.code === code);

test('maia_vocabularies_form_an_exact_4352_index_bijection', () => {
  validateVocabularies(forward, reverse);
  for (const defect of ['missing', 'wrong_reverse', 'duplicate', 'negative', 'fractional', 'out_of_range']) {
    const altered = { ...forward };
    const reversed = { ...reverse };
    if (defect === 'missing') delete altered.e2e4;
    if (defect === 'wrong_reverse') reversed[String(altered.e2e4)] = 'e2e3';
    if (defect === 'duplicate') altered.e2e4 = altered.e2e3;
    if (defect === 'negative') altered.e2e4 = -1;
    if (defect === 'fractional') altered.e2e4 = 1.5;
    if (defect === 'out_of_range') altered.e2e4 = VOCABULARY_SIZE;
    assertCode(() => validateVocabularies(altered, reversed), 'model_provenance_mismatch');
  }
});

for (const [name, fen] of Object.entries(positions)) {
  test(`maia_orientation_preserves_complete_legal_universe_and_promotions_${name.replaceAll(' ', '_')}`, () => {
    const legal = validatePosition(fen).legalMoves;
    const prepared = preparePosition(fen, legal, forward, reverse);
    const predictions = decodePredictions(new Float32Array(VOCABULARY_SIZE), prepared);
    assert.deepEqual(predictions.map(({ move }) => move), legal);
    assert.equal(prepared.tokens.length, 768);
    assert.ok(Math.abs(predictions.reduce((sum, item) => sum + item.probability, 0) - 1) <= 1e-9);
    const board = new Chess(fen);
    for (const { move } of predictions) { board.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] }); board.undo(); }
    assert.equal(mirrorFen(mirrorFen(fen)), fen);
    for (const move of legal) assert.equal(mirrorMove(mirrorMove(move)), move);
    if (name.includes('promotions')) assert.equal(predictions.filter(({ move }) => move.length === 5).length, 4);
  });
}

test('maia_subset_softmax_is_conditional_and_preserves_exact_requested_order', () => {
  const requested = ['e2e4', 'd2d4'];
  const prepared = preparePosition(opening, requested, forward, reverse);
  const logits = new Float32Array(VOCABULARY_SIZE).fill(1000);
  logits[forward.e2e4] = -1000;
  logits[forward.d2d4] = -1000;
  assert.deepEqual(decodePredictions(logits, prepared), [{ move: 'e2e4', probability: 0.5 }, { move: 'd2d4', probability: 0.5 }]);
  assert.equal(prepared.legalMoves.length, 20);
});

test('maia_softmax_preserves_explicit_numeric_underflow_zero', () => {
  const prepared = preparePosition(opening, ['e2e4', 'd2d4'], forward, reverse);
  const logits = new Float32Array(VOCABULARY_SIZE).fill(-1000);
  logits[forward.e2e4] = 0;
  assert.deepEqual(decodePredictions(logits, prepared), [{ move: 'e2e4', probability: 1 }, { move: 'd2d4', probability: 0 }]);
});

test('maia_rejects_invalid_nonfinite_and_wrong_shape_logits_without_fallback', () => {
  const prepared = preparePosition(opening, ['e2e4'], forward, reverse);
  for (const value of [NaN, Infinity, -Infinity]) {
    const logits = new Float32Array(VOCABULARY_SIZE);
    logits[0] = value; // Invalid omitted logits are also rejected.
    assertCode(() => decodePredictions(logits, prepared), 'invalid_model_output');
  }
  for (const logits of [new Float32Array(VOCABULARY_SIZE - 1), new Float64Array(VOCABULARY_SIZE), [], null]) assertCode(() => decodePredictions(logits, prepared), 'invalid_model_output');
  for (const entries of [[], [{ move: 'd2d4', index: forward.d2d4 }], [{ move: 'e2e4', index: VOCABULARY_SIZE }]]) assertCode(() => decodePredictions(new Float32Array(VOCABULARY_SIZE), { ...prepared, entries }), 'invalid_model_output');
});

test('maia_rejects_illegal_duplicate_malformed_and_empty_requested_moves', () => {
  for (const requested of [[], ['e2e5'], ['e2e4', 'e2e4'], ['e2e4,d2d4'], ['e7e8Q'], [null], null]) assertCode(() => preparePosition(opening, requested, forward, reverse), 'invalid_legal_moves');
});

test('maia_rejects_requested_vocabulary_omissions_and_mismappings', () => {
  const altered = { ...forward };
  delete altered.e2e4;
  assertCode(() => preparePosition(opening, ['d2d4'], altered, reverse), 'model_provenance_mismatch');
  const reversed = { ...reverse, [String(forward.e2e4)]: 'e2e3' };
  assertCode(() => preparePosition(opening, ['e2e4'], forward, reversed), 'model_provenance_mismatch');
});

for (const [name, fen] of Object.entries({
  malformed: 'this is not FEN',
  zero_placement_digit: '4k3/8/8/8/8/8/8/0K7 w - - 0 1',
  missing_king: '8/8/8/8/8/8/8/4K3 w - - 0 1',
  adjacent_kings: '8/8/8/8/8/8/4k3/4K3 w - - 0 1',
  nonactive_king_in_check: '4k3/4R3/8/8/8/8/8/4K3 w - - 0 1',
  phantom_castling: '4k3/8/8/8/8/8/8/4K3 w KQkq - 0 1',
  duplicate_castling_rights: 'r3k2r/8/8/8/8/8/8/R3K2R w KK - 0 1',
  fractional_halfmove: '4k3/8/8/8/8/8/8/4K3 w - - 1.5 1',
  invalid_fullmove: '4k3/8/8/8/8/8/8/4K3 w - - 0 1oops',
  unsafe_counter: '4k3/8/8/8/8/8/8/4K3 w - - 0 99999999999999999999999999',
  ep_without_double_pushed_pawn: '4k3/8/8/8/8/8/8/4K3 w - d6 0 1',
  ep_occupied_source: '4k3/3p4/8/3pP3/8/8/8/4K3 w - d6 0 1',
  ep_occupied_target: '4k3/8/3N4/3pP3/8/8/8/4K3 w - d6 0 1',
  ep_wrong_halfmove: '4k3/8/8/3pP3/8/8/8/4K3 w - d6 1 1',
})) test(`maia_rejects_locally_illegal_fen_${name}`, () => assertCode(() => validatePosition(fen), 'invalid_fen'));

test('maia_accepts_valid_uncapturable_and_pinned_en_passant_targets', () => {
  const uncapturable = validatePosition('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1');
  const pinned = validatePosition('k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1');
  assert.equal(uncapturable.legalMoves.length, 20);
  assert.ok(!pinned.legalMoves.includes('e5d6'));
});

for (const [name, fen] of Object.entries({
  excess_pawns: '4k3/8/8/8/8/P7/PPPPPPPP/4K3 w - - 0 1',
  excess_pieces: '4k3/8/8/8/8/NN6/PPPPPPPP/RNBQKQBR w - - 0 1',
  extra_queen_with_eight_pawns: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKQNR w KQkq - 0 1',
  same_color_bishops_with_eight_pawns: 'rnbqkbnr/pppppppp/8/8/8/4B3/PPPPPPPP/RNBQK1NR w KQkq - 0 1',
})) test(`maia_rejects_impossible_local_material_both_colors_${name}`, () => {
  assertCode(() => validatePosition(fen), 'invalid_fen');
  assertCode(() => validatePosition(mirrorFen(fen)), 'invalid_fen');
});

test('maia_accepts_promoted_queen_and_same_color_bishops_when_a_pawn_is_missing', () => {
  for (const fen of [
    'rnbqkbnr/pppppppp/8/8/8/8/1PPPPPPP/RNBQKQNR w KQkq - 0 1',
    'rnbqkbnr/pppppppp/8/8/8/4B3/1PPPPPPP/RNBQK1NR w KQkq - 0 1',
  ]) {
    assert.ok(validatePosition(fen).legalMoves.length);
    assert.ok(validatePosition(mirrorFen(fen)).legalMoves.length);
  }
});

test('maia_terminal_checkmate_and_stalemate_are_nonretryable_explicit_failures', () => {
  for (const [fen, reason] of [['7k/6Q1/6K1/8/8/8/8/8 b - - 0 1', 'checkmate'], ['7k/5Q2/6K1/8/8/8/8/8 b - - 0 1', 'stalemate']]) {
    assert.equal(validatePosition(fen).terminalReason, reason);
    assert.throws(() => preparePosition(fen, ['a1a2'], forward, reverse), (error) => error.code === 'terminal_position' && error.retryable === false);
  }
});

test('maia_worker_ratings_are_exact_levels_without_rounding_or_two_player_aliases', () => {
  for (const rating of SUPPORTED_RATINGS) validateRating(rating);
  for (const rating of [1000, 1550, 2000, 0, NaN, Infinity, '1500', true, { self_rating: 1500 }]) assertCode(() => validateRating(rating), 'unsupported_rating');
  validateRating(1550, { legacy: true });
});
