import React from "react";

interface CloseButtonProps {
  onClose: () => void;
  ariaLabel?: string;
}

export default function CloseButton({ onClose, ariaLabel }: CloseButtonProps) {
  return (
    <button
      className="close-button"
      onClick={onClose}
      aria-label={ariaLabel ?? "Close window"}
    >
      ×
    </button>
  );
}
