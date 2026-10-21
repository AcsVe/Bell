"""Generates the built-in tones of the central (office) bell: static/sounds/central/*.wav

    python tools/make_tones.py

Pure standard library (math + wave). Mono, 22.05 kHz, 16-bit — loud and clear through a PA amplifier,
small enough to download once. Keys and names must match services.CENTRAL_TONES.
"""
import math
import os
import random
import struct
import wave

RATE = 22050
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'sounds', 'central')

NOTE = {'B3': 246.94, 'C4': 261.63, 'E4': 329.63, 'F#4': 369.99, 'G#4': 415.30, 'A4': 440.0, 'B4': 493.88,
        'C5': 523.25, 'D5': 587.33, 'E5': 659.25, 'F5': 698.46, 'F#5': 739.99, 'G5': 783.99, 'G#5': 830.61,
        'A5': 880.0, 'B5': 987.77, 'C6': 1046.50, 'E6': 1318.51}


def silence(sec):
    return [0.0] * int(RATE * sec)


def env(i, n, attack=0.005, release=0.03):
    a, r = int(RATE * attack), int(RATE * release)
    if i < a:
        return i / max(a, 1)
    if i > n - r:
        return max(0.0, (n - i) / max(r, 1))
    return 1.0


def bell(freq, dur, decay=2.2, partials=((1, 1.0), (2.0, 0.5), (2.76, 0.35), (5.4, 0.18), (8.93, 0.08))):
    """Church/school bell: inharmonic partials, the higher ones dying faster."""
    n = int(RATE * dur)
    out = []
    for i in range(n):
        tt = i / RATE
        v = 0.0
        for mul, amp in partials:
            v += amp * math.exp(-tt * decay * (0.6 + mul * 0.25)) * math.sin(2 * math.pi * freq * mul * tt)
        out.append(v * env(i, n, 0.002, 0.05))
    return out


def marimba(freq, dur=0.6):
    n = int(RATE * dur)
    return [(math.sin(2 * math.pi * freq * i / RATE) + 0.25 * math.sin(2 * math.pi * freq * 4 * i / RATE) * math.exp(-i / RATE * 30))
            * math.exp(-i / RATE * 6) * env(i, n, 0.002, 0.04) for i in range(n)]


def beep(freq, dur):
    n = int(RATE * dur)
    return [(0.8 * math.sin(2 * math.pi * freq * i / RATE) + 0.2 * math.sin(2 * math.pi * freq * 2 * i / RATE)) * env(i, n, 0.01, 0.04)
            for i in range(n)]


def electric(dur, freq=950, strike=24):
    """Classic electric school bell: a clapper striking a gong ~24 times a second."""
    n = int(RATE * dur)
    out = []
    for i in range(n):
        tt = i / RATE
        ph = (tt * strike) % 1.0
        hit = math.exp(-ph * 9)
        v = hit * (math.sin(2 * math.pi * freq * tt) + 0.6 * math.sin(2 * math.pi * freq * 2.71 * tt) + 0.3 * math.sin(2 * math.pi * freq * 4.2 * tt))
        out.append(math.tanh(1.6 * v) * env(i, n, 0.01, 0.15))   # soft clip: louder, like a real gong
    return out


def chirp(f0, f1, dur):
    n = int(RATE * dur)
    out, ph = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i / n)
        ph += 2 * math.pi * f / RATE
        out.append(math.sin(ph) * math.sin(math.pi * i / n))
    return out


def wood(dur=0.12, freq=900):
    rnd = random.Random(7)
    n = int(RATE * dur)
    return [math.tanh(2.0 * (0.7 * math.sin(2 * math.pi * freq * i / RATE) + 0.3 * (rnd.random() * 2 - 1)) * math.exp(-i / RATE * 30)) for i in range(n)]


def musicbox(freq, dur=0.9):
    """Music-box tine: pure tone + a bright, fast-dying overtone."""
    n = int(RATE * dur)
    return [(math.sin(2 * math.pi * freq * i / RATE) * math.exp(-i / RATE * 3.2)
             + 0.35 * math.sin(2 * math.pi * freq * 3.0 * i / RATE) * math.exp(-i / RATE * 14)
             + 0.12 * math.sin(2 * math.pi * freq * 5.9 * i / RATE) * math.exp(-i / RATE * 30)) * env(i, n, 0.001, 0.05)
            for i in range(n)]


def toypiano(freq, dur=0.5):
    """Toy piano: metal rods — slightly detuned partials, plinky."""
    n = int(RATE * dur)
    return [(math.sin(2 * math.pi * freq * i / RATE) + 0.6 * math.sin(2 * math.pi * freq * 1.005 * i / RATE)
             + 0.4 * math.sin(2 * math.pi * freq * 3.92 * i / RATE) * math.exp(-i / RATE * 18)) * math.exp(-i / RATE * 5)
            * env(i, n, 0.001, 0.04) for i in range(n)]


def flute(freq, dur, vib=5.5):
    """Soft flute-like whistle with vibrato (cuckoo)."""
    n = int(RATE * dur)
    out, ph = [], 0.0
    for i in range(n):
        tt = i / RATE
        ph += 2 * math.pi * freq * (1 + 0.006 * math.sin(2 * math.pi * vib * tt)) / RATE
        out.append((math.sin(ph) + 0.15 * math.sin(2 * ph)) * env(i, n, 0.03, 0.08))
    return out


def bubble(f0, dur=0.11):
    """Bubble 'blip': a sine that glides upward quickly."""
    n = int(RATE * dur)
    out, ph = [], 0.0
    for i in range(n):
        x = i / n
        ph += 2 * math.pi * f0 * (1 + 1.6 * x * x) / RATE
        out.append(math.sin(ph) * math.sin(math.pi * x) ** 0.7)
    return out


def tweet(f0, f1, dur, wobble=38):
    """Bird tweet: fast frequency wobble inside a glide."""
    n = int(RATE * dur)
    out, ph = [], 0.0
    for i in range(n):
        x = i / n
        f = f0 + (f1 - f0) * x + 260 * math.sin(2 * math.pi * wobble * i / RATE)
        ph += 2 * math.pi * f / RATE
        out.append(math.sin(ph) * math.sin(math.pi * x))
    return out


def boing(f0=420, f1=150, dur=0.55):
    """Cartoon spring 'boing': pitch falls with a wobble."""
    n = int(RATE * dur)
    out, ph = [], 0.0
    for i in range(n):
        x = i / n
        f = f1 + (f0 - f1) * math.exp(-x * 4) * (1 + 0.25 * math.sin(2 * math.pi * 14 * i / RATE))
        ph += 2 * math.pi * f / RATE
        out.append((math.sin(ph) + 0.3 * math.sin(3 * ph)) * math.exp(-x * 2.5) * env(i, n, 0.003, 0.05))
    return out


def mix_seq(parts):
    out = []
    for p in parts:
        out += p
    return out


def overlap(parts_at):
    """[(start_sec, samples), ...] → one track (lets bell notes ring into each other)."""
    end = max(int(s * RATE) + len(p) for s, p in parts_at)
    out = [0.0] * end
    for s, p in parts_at:
        o = int(s * RATE)
        for i, v in enumerate(p):
            out[o + i] += v
    return out


def write(name, samples):
    peak = max(abs(v) for v in samples) or 1.0
    k = 0.89 / peak
    samples = silence(0.05) + samples + silence(0.15)
    os.makedirs(OUT, exist_ok=True)
    with wave.open(os.path.join(OUT, name + '.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b''.join(struct.pack('<h', int(max(-1.0, min(1.0, v * k)) * 32767)) for v in samples))
    print(f'{name}.wav  {len(samples) / RATE:.1f}s')


def main():
    # ── period start (class) ──
    write('class-1', electric(3.5))
    write('class-2', overlap([(0, bell(NOTE['E5'], 2.2)), (0.7, bell(NOTE['C5'], 2.6))]))
    write('class-3', overlap([(0, bell(NOTE['C5'], 1.8)), (0.45, bell(NOTE['E5'], 1.8)), (0.9, bell(NOTE['G5'], 2.4))]))
    write('class-4', overlap([(0, bell(NOTE['E5'], 1.6, 1.6)), (0.6, bell(NOTE['G#5'], 1.6, 1.6)), (1.2, bell(NOTE['F#5'], 1.6, 1.6)),
                              (1.8, bell(NOTE['B4'], 2.8, 1.2))]))
    write('class-5', mix_seq([beep(1320, 0.28), silence(0.12), beep(1320, 0.28), silence(0.5), beep(1320, 0.28), silence(0.12), beep(1320, 0.28)]))
    write('class-6', overlap([(0, bell(NOTE['A5'], 1.3, 2.5)), (0.55, bell(NOTE['A5'], 1.3, 2.5)), (1.1, bell(NOTE['A5'], 2.2, 2.0))]))
    write('class-7', mix_seq([marimba(NOTE['G5'], 0.45), marimba(NOTE['E5'], 0.45), marimba(NOTE['C5'], 1.0)]))
    write('class-8', electric(6.0, 880, 22))
    # ── break start ──
    write('break-1', mix_seq([marimba(NOTE[n], 0.22) for n in ('C5', 'D5', 'E5', 'F5', 'G5', 'A5')] + [marimba(NOTE['G5'], 0.9)]))
    write('break-2', mix_seq([chirp(2200, 3600, 0.09), silence(0.05), chirp(2200, 3600, 0.09), silence(0.25)] * 3))
    write('break-3', overlap([(0, bell(NOTE['C6'], 1.5)), (0.45, bell(NOTE['A5'], 1.5)), (0.9, bell(NOTE['F5'], 1.5)), (1.35, bell(NOTE['C5'], 2.4))]))
    write('break-4', mix_seq([marimba(NOTE[n], 0.2) for n in ('C5', 'E5', 'G5', 'C6', 'G5', 'E5')] + [marimba(NOTE['C5'], 0.9)]))
    write('break-5', mix_seq([wood(), silence(0.1), wood(), silence(0.1), wood(0.12, 700), silence(0.3)] * 3))
    write('break-6', mix_seq([electric(1.8, 1100, 26), silence(0.5), electric(1.8, 1100, 26)]))
    # ── children (kindergarten / primary) — soft and playful, no electric ringing ──
    N = NOTE
    write('kids-1', overlap([(i * 0.3, musicbox(N[n], 1.2)) for i, n in
                             enumerate(('C6', 'G5', 'E5', 'G5', 'C6', 'E6'))]))                          # music box
    write('kids-2', mix_seq([marimba(N[n], 0.16) for n in ('C5', 'E5', 'G5', 'E5', 'C5', 'E5', 'G5', 'C6')]
                            + [marimba(N['C6'], 0.9)]))                                                    # happy xylophone
    write('kids-3', mix_seq([flute(N['G5'], 0.32), silence(0.06), flute(N['E5'], 0.5), silence(0.45),
                             flute(N['G5'], 0.32), silence(0.06), flute(N['E5'], 0.5)]))   # cuckoo
    write('kids-4', mix_seq([p for f in (500, 640, 560, 760, 680, 900, 820, 1050) for p in (bubble(f), silence(0.07))]))  # bubbles
    write('kids-5', mix_seq([tweet(2600, 3400, 0.12), silence(0.05), tweet(3000, 2400, 0.1), silence(0.18),
                             tweet(2800, 3600, 0.09), silence(0.04), tweet(2800, 3600, 0.09), silence(0.04),
                             tweet(3400, 2600, 0.16), silence(0.3), tweet(2600, 3400, 0.12), silence(0.05), tweet(3200, 2500, 0.18)]))  # birds
    write('kids-6', mix_seq([toypiano(N[n], 0.28) for n in ('C5', 'D5', 'E5', 'F5', 'G5', 'A5', 'B5')]
                            + [toypiano(N['C6'], 1.0)]))                                                   # toy piano scale
    write('kids-7', mix_seq([musicbox(N[n], d) for n, d in (('C5', .38), ('C5', .38), ('G5', .38), ('G5', .38),
                                                             ('A5', .38), ('A5', .38), ('G5', .9))]))      # little-star folk tune
    write('kids-8', mix_seq([boing(), silence(0.12), boing(520, 180, 0.5), silence(0.12), boing(640, 220, 0.6)]))  # bouncy


if __name__ == '__main__':
    main()
