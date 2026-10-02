import { Button } from "./BaseButton";
import { useRef, useState } from "react";

import { usePopupKeyboard } from "../../lib/keyboard-shortcuts";

interface SavedLocallyButtonProps {
  setShowImport: (show: boolean) => void;
}

export default function SavedLocallyButton({
  setShowImport,
}: SavedLocallyButtonProps) {
  const [open, setOpen] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);
  usePopupKeyboard(popupRef, () => setOpen(false), open);
  return (
    <div className="local-data-menu">
      <Button
        className="local-status"
        aria-expanded={open}
        aria-controls="local-data-menu"
        aria-label="Open local data menu"
        onClick={event => { event.currentTarget.focus(); setOpen((value) => !value); }}
      >
        <span className="status-dot" aria-hidden="true" />
        <span>Local data</span>
      </Button>
      {open && (
        <div ref={popupRef} id="local-data-menu" className="local-data-popover" role="menu">
          <strong>Local data</strong>
          <small>Saved on this computer</small>
          <Button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setShowImport(true);
            }}
          >
            Import or transfer data
          </Button>
        </div>
      )}
    </div>
  );
}
