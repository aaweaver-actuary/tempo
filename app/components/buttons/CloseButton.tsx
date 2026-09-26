import { IconButton } from "./IconButton";
import React from "react";

interface CloseButtonProps {
  onClose: () => void;
  ariaLabel?: string;
}

export default function CloseButton({ onClose, ariaLabel }: CloseButtonProps) {
  return (
    <IconButton
      className="close-button"
      onClick={onClose}
      aria-label={ariaLabel ?? "Close window"}
    >
      ×
    </IconButton>
  );
}
