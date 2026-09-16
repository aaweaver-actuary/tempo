import { PracticeCard } from "../../types";

interface EditCardButtonProps {
  card: PracticeCard;
  setEditorCard: (card: PracticeCard) => void;
}

export default function EditCardButton({
  card,
  setEditorCard,
}: EditCardButtonProps) {
  return (
    <button onClick={() => setEditorCard(card)}>
      ✎ <span>Edit card</span>
    </button>
  );
}
