import { expect, it, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Navbar from '../../app/components/Navbar';
vi.mock('../../app/lib/study-connection', () => ({ independentStudy: true }));
afterEach(cleanup);

it('Study composition exposes only its supported workspaces without optional preloads', () => {
  const navigate = vi.fn();
  render(<Navbar view="train" setView={navigate} />);
  expect(screen.queryByRole('button', { name: 'Games', exact: true })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Builder', exact: true })).toBeNull();
  fireEvent.click(screen.getAllByRole('button', { name: 'Tactics', exact: true })[0]);
  expect(navigate).toHaveBeenCalledWith('tactics');
});
