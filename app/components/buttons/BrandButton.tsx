import { View } from "@/app/types";

interface BrandButtonProps {
  setView: (view: View) => void;
}

export default function BrandButton({ setView }: BrandButtonProps) {
  return (
    <button
      className="brand"
      onClick={() => setView("train")}
      aria-label="Tempo home"
    >
      <span className="brand-mark">T</span>
      <span>Tempo</span>
    </button>
  );
}
