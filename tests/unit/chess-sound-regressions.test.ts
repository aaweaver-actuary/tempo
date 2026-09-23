import { expect, it, vi } from "vitest";
import { playChessMoveSound, playMoveSound } from "../../app/lib/move-sound";

type RecordedAudio = {
  url: string;
  volume: number;
  currentTime: number;
  play: ReturnType<typeof vi.fn>;
};

it("played moves use distinct move, capture, and check recordings and respect persisted sound settings", () => {
  const sounds: RecordedAudio[] = [];
  vi.stubGlobal("Audio", class {
    volume = 1;
    currentTime = 0;
    play = vi.fn(async () => undefined);
    constructor(public url: string) {
      sounds.push(this);
    }
  });
  localStorage.setItem("tempo-move-sound", "true");
  localStorage.setItem("tempo-sound-volume", "0.4");

  playChessMoveSound(undefined, false);
  playChessMoveSound({ captured: "p" }, false);
  playChessMoveSound({ captured: "p" }, true);

  expect(sounds.map((sound) => sound.url)).toEqual([
    expect.stringContaining("/sounds/standard/Move.mp3"),
    expect.stringContaining("/sounds/standard/Capture.mp3"),
    expect.stringContaining("/sounds/standard/Check.wav"),
  ]);
  expect(sounds.every((sound) => sound.volume === 0.4)).toBe(true);

  localStorage.setItem("tempo-move-sound", "false");
  const playCountsBeforeMute = sounds.map((sound) => sound.play.mock.calls.length);
  playChessMoveSound({ captured: "p" }, false);
  playChessMoveSound(undefined, true);
  expect(sounds.map((sound) => sound.play.mock.calls.length)).toEqual(
    playCountsBeforeMute,
  );

  playMoveSound({ force: true });
  expect(sounds[0].play).toHaveBeenCalledTimes(playCountsBeforeMute[0] + 1);
});
