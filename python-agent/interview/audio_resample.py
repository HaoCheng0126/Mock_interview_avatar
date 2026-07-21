"""Normalize platform uplink AudioFrame payloads to 16 kHz mono PCM16.

The developer-side ASR backends (Qwen/DashScope and Volcano) both expect
16 kHz **mono little-endian PCM16**. The platform relays candidate audio as
``AudioFrame``s whose 9-byte header advertises the real sample rate
(``sample_rate`` enum 0=16k / 1=24k / 2=48k), channel (0=mono / 1=stereo) and
codec (0=PCM / 1=Opus). Feeding, say, a 24 kHz payload to a 16 kHz recognizer
yields garbage — and therefore *no transcript at all*, which is exactly the
"I spoke but nothing registered" symptom.

Conversion uses only the standard library (``array``); Python 3.13+ removed the
old ``audioop`` module. Opus frames cannot be decoded here without a
third-party codec, so they return ``None`` and the caller logs + skips (which
also surfaces the fact that the platform is relaying Opus rather than PCM).
"""

from __future__ import annotations

from array import array

TARGET_RATE_HZ = 16000

# AudioFrame.sample_rate is an enum, not Hz. Map it to the real rate.
_SAMPLE_RATE_HZ = {0: 16000, 1: 24000, 2: 48000}

CODEC_PCM = 0
CODEC_OPUS = 1


def normalize_to_pcm16k_mono(
    payload: bytes,
    *,
    sample_rate: int = 0,
    channel: int = 0,
    codec: int = 0,
) -> bytes | None:
    """Return 16 kHz mono PCM16 bytes for ``payload``.

    ``sample_rate`` / ``channel`` / ``codec`` are the raw ``AudioFrame`` header
    fields. Returns ``b""`` for an empty payload, and ``None`` when the frame
    cannot be converted here (a non-PCM codec, or an unknown sample-rate enum)
    so the caller can log the format and skip it.
    """
    if not payload:
        return b""
    if codec != CODEC_PCM:
        return None
    src_hz = _SAMPLE_RATE_HZ.get(sample_rate)
    if src_hz is None:
        return None
    # Fast path: already 16 kHz mono PCM16 — feed straight through, no copy.
    if src_hz == TARGET_RATE_HZ and channel == 0:
        return payload

    samples = array("h")
    # PCM16 is 2 bytes/sample; drop a stray trailing byte rather than crash.
    samples.frombytes(payload if len(payload) % 2 == 0 else payload[:-1])

    if channel == 1:
        samples = _downmix_stereo(samples)
    if src_hz != TARGET_RATE_HZ:
        samples = _resample_linear(samples, src_hz, TARGET_RATE_HZ)
    return samples.tobytes()


def _downmix_stereo(samples: array) -> array:
    """Average interleaved L/R int16 samples into a mono stream."""
    mono = array("h", bytes(2 * (len(samples) // 2)))
    for i in range(len(mono)):
        mono[i] = (samples[2 * i] + samples[2 * i + 1]) // 2
    return mono


def _resample_linear(samples: array, src_hz: int, dst_hz: int) -> array:
    """Linear-interpolation resample of mono int16 ``samples`` to ``dst_hz``.

    Speech-grade and dependency-free. Frames are resampled independently, which
    can leave a negligible discontinuity at frame edges — inaudible to ASR.
    """
    n_in = len(samples)
    if n_in == 0:
        return samples
    n_out = max(1, round(n_in * dst_hz / src_hz))
    if n_out == n_in:
        return samples
    out = array("h", bytes(2 * n_out))
    step = (n_in - 1) / (n_out - 1) if n_out > 1 else 0.0
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        s0 = samples[j]
        s1 = samples[j + 1] if j + 1 < n_in else s0
        out[i] = int(s0 + (s1 - s0) * frac)
    return out
