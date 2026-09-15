export type PracticeCard = {
  id: string;
  kind: 'opening' | 'puzzle' | 'endgame';
  title: string;
  subtitle: string;
  startingFen: string;
  moves: string[];
  userMoveTarget: number;
  nextMove?: string;
  sourceUrl?: string;
  backendId?: string;
  queueEntryId?: number;
  orientation?: 'white' | 'black';
};
