import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import Home from '../../app/views/home_view';
import { useTrainingStore } from '../../app/state/training-store';
import { fetchAndInitializeQueue } from '../../app/views/fetchAndInitializeQueue';
import { demoCards } from '../../app/samples';

vi.mock('../../app/lib/study-connection', async importOriginal => ({
  ...await importOriginal<typeof import('../../app/lib/study-connection')>(), independentStudy: true,
}));
vi.mock('../../app/views/fetchAndInitializeQueue', async importOriginal => ({
  ...await importOriginal<typeof import('../../app/views/fetchAndInitializeQueue')>(), fetchAndInitializeQueue: vi.fn(),
}));
vi.mock('../../app/components/board/chessboard', () => ({ Chessboard: () => <div /> }));
vi.mock('../../app/lib/move-sound', () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ study_only_mode: false,
    new_cards_per_day: 10, study_new_per_day: 2, tactics_new_per_day: 5,
    include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 })));
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    useTrainingStore.getState().hydrateLocalQueue([demoCards[0]], true);
    useTrainingStore.setState({ queueReadiness: 'ready' });
    return 'installed';
  });
});

it('Study shell trains without optional capabilities and omits unsupported authoring actions', async () => {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe('ready'));
  expect(screen.queryByLabelText('Open review position')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Edit card' })).toBeNull();
  for (const name of ['Games', 'Builder', 'Repertoire', 'Studies', 'Insights']) {
    expect(screen.queryByRole('button', { name })).toBeNull();
  }
  expect(vi.mocked(fetch).mock.calls.some(([input]) => /repertoires|discoveries|games|integrity/.test(String(input)))).toBe(false);
});

it('Study appearance settings change persisted browser preferences without loading workspace administration', async () => {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe('ready'));
  fireEvent.click(screen.getAllByRole('button', { name: 'Settings' })[0]);
  const settings = await screen.findByRole('region', { name: 'Study settings' });
  expect(settings).not.toBeNull();
  fireEvent.change(screen.getByLabelText('Board colors'), { target: { value: 'blue' } });
  fireEvent.change(screen.getByLabelText('Piece set'), { target: { value: 'merida' } });
  fireEvent.click(screen.getByLabelText('Chess-piece sounds'));
  expect(useTrainingStore.getState()).toMatchObject({ boardTheme: 'blue', pieceSet: 'merida', isSoundEnabled: true });
  expect(localStorage.getItem('tempo-board-theme')).toBe('blue');
  expect(localStorage.getItem('tempo-piece-set')).toBe('merida');
  expect(localStorage.getItem('tempo-move-sound')).toBe('true');
  expect(screen.queryByText('Lichess username')).toBeNull();
  expect(screen.queryByText('Encrypted backup')).toBeNull();
});
