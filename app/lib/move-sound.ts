import { assetUrl } from "../const";

let moveAudio: HTMLAudioElement | undefined;
let captureAudio: HTMLAudioElement | undefined;
let checkAudio: HTMLAudioElement | undefined;

export type MoveSoundEvents = {
  capture?: boolean;
  check?: boolean;
  force?: boolean;
};

export function moveSoundEnabled() {
  return typeof window !== "undefined" && localStorage.getItem("tempo-move-sound") !== "false";
}

export function playMoveSound({
  force = false,
  capture = false,
  check = false,
}: MoveSoundEvents = {}) {
  if (typeof window === "undefined" || (!force && !moveSoundEnabled())) return;
  try {
    const volume = Math.max(0, Math.min(1, Number(localStorage.getItem("tempo-sound-volume") ?? .72)));
    // A check is the most urgent event, including when the checking move captures.
    const audio = check
      ? (checkAudio ??= new Audio(assetUrl("sounds/standard/Check.wav")))
      : capture
        ? (captureAudio ??= new Audio(assetUrl("sounds/standard/Capture.mp3")))
        : (moveAudio ??= new Audio(assetUrl("sounds/standard/Move.mp3")));
    audio.currentTime = 0;
    audio.volume = Number.isFinite(volume) ? volume : .72;
    void audio.play().catch(() => undefined);
  } catch { /* Sound remains optional when browser audio is unavailable. */ }
}

export function playChessMoveSound(
  move: { captured?: string } | null | undefined,
  givesCheck: boolean,
) {
  playMoveSound({ capture: Boolean(move?.captured), check: givesCheck });
}
