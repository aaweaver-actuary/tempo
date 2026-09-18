interface SavedLocallyButtonProps {
  setShowImport: (show: boolean) => void;
}

export default function SavedLocallyButton({
  setShowImport,
}: SavedLocallyButtonProps) {
  return (
    <button className="local-status" onClick={(event) => { event.currentTarget.focus(); setShowImport(true); }}>
      <span className="status-dot" /> Local data
    </button>
  );
}
