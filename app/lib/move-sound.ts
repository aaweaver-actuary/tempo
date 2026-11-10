import { assetUrl } from "../const";

type SoundKind = "move-self" | "move-opponent" | "capture" | "check" | "castle" | "promote" | "illegal";
type SoundPlayback = {
  audio: HTMLAudioElement;
  generation: number;
  pendingDeadline?: number;
};

const soundPaths: Record<SoundKind, string> = {
  "move-self": "sounds/move-self.mp3",
  "move-opponent": "sounds/move-opponent.mp3",
  capture: "sounds/capture.mp3",
  check: "sounds/check.mp3",
  castle: "sounds/castle.mp3",
  promote: "sounds/promote.mp3",
  illegal: "sounds/illegal.mp3",
};
const soundPlaybacks: Partial<Record<SoundKind, SoundPlayback>> = {};

export type MoveSoundEvents = {
  capture?: boolean;
  check?: boolean;
  force?: boolean;
  actor?: "self" | "opponent";
  promotion?: boolean;
  castle?: boolean;
  illegal?: boolean;
};

export function moveSoundEnabled() {
  return typeof window !== "undefined" && localStorage.getItem("tempo-move-sound") !== "false";
}

function prepareSound(soundKind: SoundKind): SoundPlayback {
  const existingPlayback = soundPlaybacks[soundKind];
  if (existingPlayback) return existingPlayback;
  const audio = new Audio(assetUrl(soundPaths[soundKind]));
  audio.preload = "auto";
  const playback = { audio, generation: 0 };
  soundPlaybacks[soundKind] = playback;
  audio.load();
  return playback;
}

export function prepareMoveSounds() {
  if (typeof window === "undefined") return;
  try {
    for (const kind of Object.keys(soundPaths) as SoundKind[]) prepareSound(kind);
  } catch { /* Board interaction remains available when audio is unsupported. */ }
}

function cancelPlayback(playback: SoundPlayback) {
  playback.generation++;
  if (playback.pendingDeadline !== undefined) {
    window.clearTimeout(playback.pendingDeadline);
    playback.pendingDeadline = undefined;
  }
  playback.audio.pause();
}

export function cancelMoveSounds() {
  if (typeof window === "undefined") return;
  for (const playback of Object.values(soundPlaybacks)) {
    if (!playback) continue;
    try { cancelPlayback(playback); } catch { /* Audio remains optional. */ }
  }
}

export function playMoveSound({
  force = false,
  capture = false,
  check = false,
  actor = "self",
  promotion = false,
  castle = false,
  illegal = false,
}: MoveSoundEvents = {}) {
  if (typeof window === "undefined" || (!force && !moveSoundEnabled())) return;
  try {
    // A check is the most urgent event, including when the checking move captures.
    const soundKind: SoundKind = illegal ? "illegal" : check ? "check" : promotion ? "promote" : castle ? "castle" : capture ? "capture" : `move-${actor}`;
    const playback = prepareSound(soundKind);
    const audio = playback.audio;
    // A cue belongs to its move. Loading it later must not replay old moves.
    if (audio.readyState < 2) return;
    if (playback.pendingDeadline !== undefined) cancelPlayback(playback);
    const volume = Math.max(0, Math.min(1, Number(localStorage.getItem("tempo-sound-volume") ?? .72)));
    audio.currentTime = 0;
    audio.volume = Number.isFinite(volume) ? volume : .72;
    const generation = ++playback.generation;
    const playRequest = audio.play();
    playback.pendingDeadline = window.setTimeout(() => {
      if (playback.generation !== generation) return;
      cancelPlayback(playback);
    }, 250);
    void playRequest.then(
      () => {
        if (playback.generation !== generation) return;
        window.clearTimeout(playback.pendingDeadline);
        playback.pendingDeadline = undefined;
      },
      () => {
        if (playback.generation !== generation) return;
        window.clearTimeout(playback.pendingDeadline);
        playback.pendingDeadline = undefined;
      },
    );
  } catch { /* Sound remains optional when browser audio is unavailable. */ }
}

export function playChessMoveSound(
  move: { captured?: string; promotion?: string; flags?: string } | null | undefined,
  givesCheck: boolean,
  actor: "self" | "opponent" = "self",
) {
  playMoveSound({ capture: Boolean(move?.captured), check: givesCheck, promotion: Boolean(move?.promotion), castle: Boolean(move?.flags?.match(/[kq]/)), actor });
}
