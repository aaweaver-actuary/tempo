import type { BoardTheme, PieceSet } from '../components/chessboard';
import { SelectInput } from '../components/inputs/SelectInput';
import { setLetterShortcutsEnabled, useLetterShortcutsEnabled } from '../lib/keyboard-shortcuts';

/** Browser-owned Study preferences, with no authoring/provider settings dependency. */
export default function StudySettingsView({ theme, pieceSet, sound, onTheme, onPieces, onSound }: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  sound: boolean;
  onTheme: (theme: BoardTheme) => void;
  onPieces: (pieces: PieceSet) => void;
  onSound: (sound: boolean) => void;
}) {
  const letterKeysEnabled = useLetterShortcutsEnabled();
  return <section className="settings-panel" aria-label="Study settings">
    <h1>Study settings</h1>
    <label>Board colors <SelectInput value={theme} onChange={event => onTheme(event.target.value as BoardTheme)}>
      <option value="brown">Brown</option><option value="blue">Blue</option><option value="green">Green</option>
    </SelectInput></label>
    <label>Piece set <SelectInput value={pieceSet} onChange={event => onPieces(event.target.value as PieceSet)}>
      <option value="cburnett">Cburnett</option><option value="merida">Merida</option>
    </SelectInput></label>
    <label>Chess-piece sounds <input type="checkbox" checked={sound} onChange={event => onSound(event.target.checked)} /></label>
    <label>Letter keyboard shortcuts <input type="checkbox" checked={letterKeysEnabled} onChange={event => setLetterShortcutsEnabled(event.target.checked)} /></label>
    <p>Arrow navigation, ? and Escape stay available.</p>
  </section>;
}
