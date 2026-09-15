let context: AudioContext | undefined;

export function moveSoundEnabled() {
  return typeof window !== 'undefined' && localStorage.getItem('tempo-move-sound') !== 'false';
}

export function playMoveSound(force = false) {
  if (typeof window === 'undefined' || (!force && !moveSoundEnabled())) return;
  try {
    context ??= new AudioContext();
    if (context.state === 'suspended') void context.resume();
    const now = context.currentTime;
    const output = context.createGain();
    output.gain.setValueAtTime(0.2, now);
    output.gain.exponentialRampToValueAtTime(0.001, now + 0.085);
    output.connect(context.destination);

    const body = context.createOscillator();
    body.type = 'triangle';
    body.frequency.setValueAtTime(128, now);
    body.frequency.exponentialRampToValueAtTime(76, now + 0.075);
    body.connect(output);
    body.start(now);
    body.stop(now + 0.085);

    const sampleCount = Math.ceil(context.sampleRate * 0.045);
    const buffer = context.createBuffer(1, sampleCount, context.sampleRate);
    const samples = buffer.getChannelData(0);
    for (let index = 0; index < sampleCount; index += 1) {
      const decay = 1 - index / sampleCount;
      samples[index] = (Math.random() * 2 - 1) * decay * decay;
    }
    const impact = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const impactGain = context.createGain();
    impact.buffer = buffer;
    filter.type = 'lowpass';
    filter.frequency.value = 720;
    impactGain.gain.value = 0.09;
    impact.connect(filter).connect(impactGain).connect(context.destination);
    impact.start(now);
  } catch { /* Sound is optional on browsers that block Web Audio. */ }
}
