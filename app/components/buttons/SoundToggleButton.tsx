interface SoundToggleButtonProps {
  soundOn: boolean;
  changeSound: (soundOn: boolean) => void;
}

export default function SoundToggleButton({
  soundOn,
  changeSound,
}: SoundToggleButtonProps) {
  return (
    <button
      className="sound-toggle"
      aria-pressed={soundOn}
      aria-label={`${soundOn ? "Turn off" : "Turn on"} board sounds`}
      onClick={() => changeSound(!soundOn)}
    >
      <span aria-hidden="true">{soundOn ? "🔊" : "🔇"}</span>
      <span>Sound</span>
    </button>
  );
}
