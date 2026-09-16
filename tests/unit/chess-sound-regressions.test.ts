import { expect, it, vi } from "vitest";
import { playMoveSound } from "../../app/lib/move-sound";

it("moves and captures play distinct standard chess recordings and respect persisted sound settings", () => {
  const sounds: Array<{ url: string; volume: number; currentTime: number; play: ReturnType<typeof vi.fn> }> = [];
  vi.stubGlobal("Audio", class {
    volume = 1; currentTime = 0; play = vi.fn(async () => undefined);
    constructor(public url: string) { sounds.push(this); }
  });
  localStorage.setItem("tempo-sound-volume", "0.4");
  playMoveSound(); playMoveSound(false, true);
  expect(sounds.map(sound => sound.url)).toEqual([expect.stringContaining("/sounds/standard/Move.mp3"), expect.stringContaining("/sounds/standard/Capture.mp3")]);
  expect(sounds.every(sound => sound.volume === .4)).toBe(true);
  localStorage.setItem("tempo-move-sound", "false"); playMoveSound();
  expect(sounds[0].play).toHaveBeenCalledTimes(1);
});
