import type { ComponentType } from 'react';
import type { BoardTheme, PieceSet } from './chessboard';
import type { View } from '../types';

/** The full workspace supplies optional views; Study supplies no implementation. */
export type OptionalWorkspaceProps = {
  view: View;
  theme: BoardTheme;
  pieceSet: PieceSet;
  positionFilter: string;
  repertoireFilter: string;
  onClearFilter: () => void;
  onQueueUpdated: () => Promise<void>;
  onNavigate: (view: View) => void;
};
export type OptionalWorkspace = {
  views: readonly View[];
  View: ComponentType<OptionalWorkspaceProps>;
};
