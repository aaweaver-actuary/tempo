import { Button } from "./BaseButton";
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
    <Button onClick={() => setEditorCard(card)}>
      ✎ <span>Edit card</span>
    </Button>
  );
}
