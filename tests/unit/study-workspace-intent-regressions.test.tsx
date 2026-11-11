import { expect, it, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import Navbar from '../../app/components/Navbar';
import { prepareLegacyWorkspaceNavigation } from '../../app/views/legacy-workspace';
vi.mock('../../app/lib/study-connection', async importOriginal => ({
  ...await importOriginal<typeof import('../../app/lib/study-connection')>(), independentStudy: false,
}));
afterEach(cleanup);

it('exposes retained desktop and phone destinations without preloads', () => {
  const navigate = vi.fn();
  const fetchSpy = vi.spyOn(globalThis, 'fetch');
  const { container } = render(<Navbar view="train" setView={navigate} />);
  for (const selector of ['.desktop-navigation', '.phone-navigation']) {
    const nav = within(container.querySelector(selector) as HTMLElement);
    expect(nav.getAllByRole('button').map(button => button.textContent)).toEqual(
      selector === '.desktop-navigation' ? ['Train', 'Tactics', 'Game Review', 'Settings'] : ['Train', 'Tactics', 'Game Review']);
    fireEvent.focus(nav.getByRole('button', { name: 'Game Review' }));
    fireEvent.pointerEnter(nav.getByRole('button', { name: 'Tactics' }));
  }
  expect(fetchSpy).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole('button', { name: 'Tactics' })[0]);
  expect(navigate).toHaveBeenCalledWith('tactics');
  fetchSpy.mockRestore();
});

it('restores retired workspace intents to Train while preserving maintenance paths', () => {
  for (const view of ['endgames', 'studies', 'builder', 'compare', 'insights', 'progress', 'statistics', 'analysis'] as const)
    expect(prepareLegacyWorkspaceNavigation(view)).toBe('train');
  expect(prepareLegacyWorkspaceNavigation('repertoire')).toBe('repertoire');
  expect(prepareLegacyWorkspaceNavigation('settings')).toBe('settings');
});
