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


import type { MutableRefObject, ReactNode } from 'react';
import type { PracticeCard } from '../domain/cards';
import type { LocalRepertoire } from '../types';
import type { TrainingPositionContext } from '../lib/training-builder-route';

/** Legacy-only operations invoked at the shared shell's navigation/queue boundary. */
export type LegacyWorkspaceController = {
  checkIntegrity: (preferred?: string, passive?: boolean) => Promise<void>;
  deferRepair: () => void;
  prepareNavigation: (view: View) => View;
};
export type LegacyWorkspaceSlots = {
  topActions?: ReactNode;
  banner?: ReactNode;
  trainingNotice?: ReactNode;
  repairNotice?: ReactNode;
  content?: ReactNode;
  dialogs?: ReactNode;
  onEditCard?: (card: PracticeCard | null) => void;
  onOpenPosition?: (target: 'analysis' | 'builder', position?: TrainingPositionContext) => void;
};
export type LegacyWorkspaceProps = {
  controllerRef: MutableRefObject<LegacyWorkspaceController | null>;
  currentView: View;
  changeWorkspace: (view: View) => void;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  soundOn: boolean;
  changeBoardTheme: (theme: BoardTheme) => void;
  changePieceSet: (pieces: PieceSet) => void;
  changeSound: (sound: boolean) => void;
  studyOnlyMode: boolean | null;
  safeBreakCounter: number;
  incrementSafeBreak: () => void;
  card: PracticeCard;
  currentFenString: string;
  step: number;
  attemptPhase: string;
  reviewed: number;
  cardsLeft: number;
  totalCards: number;
  refreshDatabaseQueue: (advance?: boolean, replaySavedReviews?: boolean) => Promise<void>;
  refreshQueueOnly: () => Promise<void>;
  invalidateBranchEvidence: () => void;
  refreshQueueAfterRepertoireChange: () => Promise<void>;
  refreshDatabaseQueueAfterRepertoireChange: () => Promise<void>;
  addImportedRepertoire: (repertoire: LocalRepertoire) => void;
  renameLocalRepertoire: (id: string, name: string) => void;
  deleteLocalRepertoire: (id: string) => void;
  onCardSaved: (card: PracticeCard) => void;
  finishPermanentDeletion: (cardId: string) => Promise<void>;
  children: (slots: LegacyWorkspaceSlots) => ReactNode;
};
export type LegacyWorkspace = ComponentType<LegacyWorkspaceProps>;
