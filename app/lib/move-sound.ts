import { assetUrl } from "../const";

let moveAudio: HTMLAudioElement | undefined;
let captureAudio: HTMLAudioElement | undefined;

export function moveSoundEnabled() {
  return typeof window !== "undefined" && localStorage.getItem("tempo-move-sound") !== "false";
}

export function playMoveSound(force = false, capture = false) {
  if (typeof window === "undefined" || (!force && !moveSoundEnabled())) return;
  try {
    const volume = Math.max(0, Math.min(1, Number(localStorage.getItem("tempo-sound-volume") ?? .72)));
    const audio = capture
      ? (captureAudio ??= new Audio(assetUrl("sounds/standard/Capture.mp3")))
      : (moveAudio ??= new Audio(assetUrl("sounds/standard/Move.mp3")));
    audio.currentTime = 0;
    audio.volume = Number.isFinite(volume) ? volume : .72;
    void audio.play().catch(() => undefined);
  } catch { /* Sound remains optional when browser audio is unavailable. */ }
}
