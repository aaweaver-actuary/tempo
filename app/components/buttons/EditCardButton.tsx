import { Button } from "./BaseButton";
import { PracticeCard } from "../../types";

interface EditCardButtonProps {
  card: PracticeCard;
  disabled?: boolean;
  setEditorCard: (card: PracticeCard) => void;
}

export default function EditCardButton({
  card,
  setEditorCard,
  disabled = false,
}: EditCardButtonProps) {
  return (
    <Button disabled={disabled} onClick={() => setEditorCard(card)}>
      ✎ <span>Edit card</span>
    </Button>
  );
}
