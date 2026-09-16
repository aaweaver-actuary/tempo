interface SavedLocallyButtonProps {
  setShowImport: (show: boolean) => void;
}

export default function SavedLocallyButton({
  setShowImport,
}: SavedLocallyButtonProps) {
  return (
    <button className="local-status" onClick={() => setShowImport(true)}>
      <span className="status-dot" /> Saved locally
    </button>
  );
}
