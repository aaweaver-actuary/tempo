export type PracticeCard = {
  id: string;
  kind: 'opening' | 'puzzle';
  title: string;
  subtitle: string;
  startingFen: string;
  moves: string[];
  userMoveTarget: number;
  nextMove?: string;
  sourceUrl?: string;
};

export type LocalRepertoire = {
  id: string;
  title: string;
  sourceName: string;
  side: 'White' | 'Black';
  pgn: string;
  cards: PracticeCard[];
};
