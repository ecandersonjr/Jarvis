"""
core/waveform_dsp.py

Shared audio-to-visual math for Jarvis's waveform displays. Kept
separate from any specific UI (terminal or tkinter) so both
frontends process audio identically -- same noise gate, same FFT
binning, same smoothing -- and only differ in how they draw the
result. If you ever add a third UI (a browser dashboard, say), this
is what it should import too.
"""

import numpy as np

# Raw int16 amplitude below which we treat the buffer as "no real
# signal" rather than normalizing it. Without this, dividing by a
# frame's own peak stretches quiet room noise up to fill the whole
# display -- it *looks* like activity but it's just noise floor being
# auto-gained. Tune this up if your mic/room is noisy and idle traces
# still look busy; tune it down if quiet speech gets gated out.
AUDIO_SILENCE_THRESHOLD = 350

SPECTRUM_FMIN = 80    # Hz -- below typical voice fundamental, not much useful energy
SPECTRUM_FMAX = 4000  # Hz -- most speech intelligibility lives under this


def downsample_peak(samples, columns: int):
    """Map a run of raw audio samples onto `columns` normalized
    (-1..1) values, using the peak-magnitude sample in each bucket
    (not an average) so short transients survive instead of getting
    smoothed away. Returns None if there are no samples at all, or a
    flat list of zeros if the signal is below the noise floor.
    """
    arr = np.asarray(samples, dtype=np.float32)
    n = len(arr)
    if n == 0:
        return None
    peak = float(np.max(np.abs(arr)))
    if peak < AUDIO_SILENCE_THRESHOLD:
        return [0.0] * columns
    bucket = max(1, n // columns)
    values = []
    for i in range(columns):
        chunk = arr[i * bucket : i * bucket + bucket]
        if len(chunk) == 0:
            values.append(values[-1] if values else 0.0)
            continue
        idx = int(np.argmax(np.abs(chunk)))
        values.append(float(chunk[idx]) / peak)
    return values


def fft_bins(samples, n_bins: int, sample_rate: int = 16000):
    """Turn a run of raw audio samples into n_bins normalized (0..1)
    magnitude values, log-spaced across the voice-relevant frequency
    range so low frequencies (which carry more energy but less
    detail) don't just dominate every bar. Returns None if there
    aren't enough samples to bother with an FFT, or a flat list of
    zeros if the signal is below the noise floor.
    """
    arr = np.asarray(samples, dtype=np.float32)
    if len(arr) < 32:
        return None

    if float(np.max(np.abs(arr))) < AUDIO_SILENCE_THRESHOLD:
        return [0.0] * n_bins

    windowed = arr * np.hanning(len(arr))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(arr), d=1.0 / sample_rate)

    fmax = min(SPECTRUM_FMAX, sample_rate / 2)
    edges = np.logspace(np.log10(SPECTRUM_FMIN), np.log10(fmax), n_bins + 1)

    bins = np.zeros(n_bins, dtype=np.float32)
    for i in range(n_bins):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if np.any(mask):
            bins[i] = np.mean(spectrum[mask])

    # sqrt compression -- raw FFT magnitude is dominated by a few loud
    # bins; compressing spreads it out into something that actually
    # looks like a spectrum instead of one tall spike.
    bins = np.sqrt(bins)

    peak = float(bins.max())
    if peak < 1e-6:
        return [0.0] * n_bins
    return (bins / peak).tolist()


class BarTracker:
    """Per-bar attack/release smoothing plus a decaying peak-hold
    cap, in normalized 0..1 units. One instance per panel (mic /
    jarvis); call update() once per frame with the latest raw
    bin/column values -- it mutates its own smoothed/peak state in
    place and hands back both.

    attack/release: how fast a bar reacts to getting louder vs
    quieter. Fast attack + slow release is what makes a bar read as
    "moving with the audio" instead of flickering with every frame's
    raw FFT noise.
    peak_decay: how fast the peak-hold cap falls back down once a
    bar stops reaching it, in the same 0..1 units per frame.
    """

    def __init__(self, n_bars: int, attack: float = 0.6, release: float = 0.2, peak_decay: float = 0.02):
        self.n = n_bars
        self.smoothed = [0.0] * n_bars
        self.peaks = [0.0] * n_bars
        self.attack = attack
        self.release = release
        self.peak_decay = peak_decay

    def update(self, raw_values):
        for i in range(self.n):
            target = raw_values[i]
            if target > self.smoothed[i]:
                self.smoothed[i] += (target - self.smoothed[i]) * self.attack
            else:
                self.smoothed[i] += (target - self.smoothed[i]) * self.release

            if self.smoothed[i] > self.peaks[i]:
                self.peaks[i] = self.smoothed[i]
            else:
                self.peaks[i] = max(self.smoothed[i], self.peaks[i] - self.peak_decay)
        return self.smoothed, self.peaks
