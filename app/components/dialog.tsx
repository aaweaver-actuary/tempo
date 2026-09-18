import { useRef, type ReactNode } from "react";
import { useDialogFocus } from "../hooks/use-dialog-focus";
export function Dialog({
  children,
  titleId,
  onClose,
  className = "",
}: {
  children: ReactNode;
  titleId: string;
  onClose: () => void;
  className?: string;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        ref={dialogRef}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
