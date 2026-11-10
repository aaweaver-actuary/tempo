import { Chess } from "chess.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

type RecordedAudio = {
  url: string;
  volume: number;
  currentTime: number;
  readyState: number;
  preload: string;
  load: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  play: ReturnType<typeof vi.fn>;
};

function recordAudio() {
  const sounds: RecordedAudio[] = [];
  vi.stubGlobal("Audio", class {
    volume = 1;
    currentTime = 0;
    readyState = 0;
    preload = "none";
    load = vi.fn();
    pause = vi.fn();
    play = vi.fn(async () => undefined);
    constructor(public url: string) {
      sounds.push(this);
    }
  });
  return sounds;
}

beforeEach(() => {
  vi.resetModules();
  localStorage.setItem("tempo-move-sound", "true");
  localStorage.setItem("tempo-sound-volume", "0.4");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.removeItem("tempo-move-sound");
  localStorage.removeItem("tempo-sound-volume");
});

it("startup prepares all board sounds before the first move and drops cues while loading", async () => {
  const sounds = recordAudio();
  const { prepareMoveSounds, playMoveSound } = await import("../../app/lib/move-sound");

  prepareMoveSounds();
  prepareMoveSounds();

  expect(sounds.map((sound) => sound.url)).toEqual([
    expect.stringContaining("/sounds/move-self.mp3"),
    expect.stringContaining("/sounds/move-opponent.mp3"),
    expect.stringContaining("/sounds/capture.mp3"),
    expect.stringContaining("/sounds/check.mp3"),
    expect.stringContaining("/sounds/castle.mp3"),
    expect.stringContaining("/sounds/promote.mp3"),
    expect.stringContaining("/sounds/illegal.mp3"),
  ]);
  expect(sounds.every((sound) => sound.preload === "auto" && sound.load.mock.calls.length === 1)).toBe(true);

  playMoveSound();
  playMoveSound({ capture: true });
  expect(sounds.every((sound) => sound.play.mock.calls.length === 0)).toBe(true);
  sounds[0].readyState = 2;
  playMoveSound();
  expect(sounds[0].play).toHaveBeenCalledTimes(1);
});

it("delayed board sound requests expire instead of bursting when playback becomes available", async () => {
  vi.useFakeTimers();
  const sounds = recordAudio();
  const { prepareMoveSounds, playMoveSound } = await import("../../app/lib/move-sound");
  prepareMoveSounds();
  sounds[0].readyState = 2;
  let finishFirstPlay: (() => void) | undefined;
  sounds[0].play.mockImplementationOnce(() => new Promise<void>((resolve) => { finishFirstPlay = resolve; }));

  playMoveSound();
  expect(sounds[0].play).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(250);
  expect(sounds[0].pause).toHaveBeenCalledTimes(1);
  finishFirstPlay?.();
  await Promise.resolve();
  expect(sounds[0].play).toHaveBeenCalledTimes(1);

  playMoveSound();
  await Promise.resolve();
  expect(sounds[0].play).toHaveBeenCalledTimes(2);
  expect(sounds[0].pause).toHaveBeenCalledTimes(1);
});

it("repeated pending cues and muting cancel earlier playback requests", async () => {
  vi.useFakeTimers();
  const sounds = recordAudio();
  const { prepareMoveSounds, playMoveSound, cancelMoveSounds } = await import("../../app/lib/move-sound");
  prepareMoveSounds();
  sounds[0].readyState = 2;
  sounds[0].play.mockImplementation(() => new Promise<void>(() => undefined));

  playMoveSound();
  await vi.advanceTimersByTimeAsync(100);
  playMoveSound();
  expect(sounds[0].pause).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(100);
  cancelMoveSounds();
  expect(sounds[0].pause).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(250);
  expect(sounds[0].pause).toHaveBeenCalledTimes(2);
});

it("played moves use distinct move, capture, and check recordings and respect persisted sound settings", async () => {
  const sounds = recordAudio();
  const { prepareMoveSounds, playChessMoveSound, playMoveSound, cancelMoveSounds } = await import("../../app/lib/move-sound");
  prepareMoveSounds();
  sounds.forEach((sound) => { sound.readyState = 2; });

  playChessMoveSound(undefined, false);
  playChessMoveSound({ captured: "p" }, false);
  playChessMoveSound({ captured: "p" }, true);
  expect(sounds.map((sound) => sound.play.mock.calls.length)).toEqual([1, 0, 1, 1, 0, 0, 0]);
  expect([sounds[0], sounds[2], sounds[3]].every((sound) => sound.volume === 0.4)).toBe(true);

  localStorage.setItem("tempo-move-sound", "false");
  cancelMoveSounds();
  playChessMoveSound({ captured: "p" }, false);
  playChessMoveSound(undefined, true);
  expect(sounds.map((sound) => sound.play.mock.calls.length)).toEqual([1, 0, 1, 1, 0, 0, 0]);
  expect(sounds.every((sound) => sound.pause.mock.calls.length === 1)).toBe(true);

  playMoveSound({ force: true });
  expect(sounds[0].play).toHaveBeenCalledTimes(2);
});


it("selects exactly one cue from real move metadata and actor", async () => {
  const sounds = recordAudio();
  const { prepareMoveSounds, playChessMoveSound, playMoveSound } = await import("../../app/lib/move-sound");
  prepareMoveSounds();
  sounds.forEach((sound) => { sound.readyState = 2; });
  const move = (fen: string, from: string, to: string, promotion?: string, actor: "self" | "opponent" = "self") => {
    const board = new Chess(fen);
    const result = board.move({ from, to, promotion });
    playChessMoveSound(result, board.isCheck(), actor);
  };
  move(new Chess().fen(), "e2", "e4");
  move(new Chess().fen(), "e2", "e4", undefined, "opponent");
  move("7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", "e5", "d6");
  move("7k/8/8/8/8/8/8/KR6 w - - 0 1", "b1", "h1");
  move("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "e1", "g1");
  move("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7", "a8", "n");
  move("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7", "a8", "q");
  playMoveSound({ illegal: true });
  expect(sounds.map((sound) => sound.play.mock.calls.length)).toEqual([1, 1, 1, 2, 1, 1, 1]);
});
