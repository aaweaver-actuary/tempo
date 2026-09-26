import { Button } from "./BaseButton";
import { useState } from "react";

interface SavedLocallyButtonProps {
  setShowImport: (show: boolean) => void;
}

export default function SavedLocallyButton({
  setShowImport,
}: SavedLocallyButtonProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="local-data-menu">
      <Button
        className="local-status"
        aria-expanded={open}
        aria-controls="local-data-menu"
        aria-label="Open local data menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="status-dot" aria-hidden="true" />
        <span>Local data</span>
      </Button>
      {open && (
        <div id="local-data-menu" className="local-data-popover" role="menu">
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
