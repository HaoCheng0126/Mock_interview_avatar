"""Tests for the uplink-audio normalizer (platform AudioFrame -> 16 kHz mono PCM16)."""

from array import array

from interview.audio_resample import TARGET_RATE_HZ, normalize_to_pcm16k_mono


def _pcm(samples: list[int]) -> bytes:
    return array("h", samples).tobytes()


def _samples(pcm_bytes: bytes) -> list[int]:
    a = array("h")
    a.frombytes(pcm_bytes)
    return list(a)


def test_empty_payload_returns_empty():
    assert normalize_to_pcm16k_mono(b"", sample_rate=1, codec=0) == b""


def test_16k_mono_pcm_is_passed_through_unchanged():
    pcm = _pcm([0, 100, -100, 32767, -32768, 5])
    out = normalize_to_pcm16k_mono(pcm, sample_rate=0, channel=0, codec=0)
    assert out == pcm  # identity fast-path, no resample


def test_opus_codec_returns_none():
    # Opus can't be decoded here — caller logs + skips.
    assert normalize_to_pcm16k_mono(b"\x00\x01\x02\x03", sample_rate=0, codec=1) is None


def test_unknown_sample_rate_enum_returns_none():
    assert normalize_to_pcm16k_mono(_pcm([1, 2, 3, 4]), sample_rate=3, codec=0) is None


def test_24k_mono_downsamples_to_16k_length():
    n_in = 300  # 24 kHz -> 16 kHz is a 2/3 ratio
    out = normalize_to_pcm16k_mono(_pcm([0] * n_in), sample_rate=1, channel=0, codec=0)
    assert len(_samples(out)) == round(n_in * TARGET_RATE_HZ / 24000) == 200


def test_48k_mono_downsamples_to_16k_length():
    n_in = 300  # 48 kHz -> 16 kHz is a 1/3 ratio
    out = normalize_to_pcm16k_mono(_pcm([7] * n_in), sample_rate=2, channel=0, codec=0)
    assert len(_samples(out)) == round(n_in * TARGET_RATE_HZ / 48000) == 100


def test_constant_signal_survives_resample():
    # A DC-constant input stays constant after linear interpolation.
    out = normalize_to_pcm16k_mono(_pcm([1234] * 240), sample_rate=1, codec=0)
    assert set(_samples(out)) == {1234}


def test_stereo_is_downmixed_to_mono():
    # Interleaved (L,R),(L,R) -> averaged mono, at 16 kHz so no resample.
    out = normalize_to_pcm16k_mono(_pcm([100, 200, -50, 50]), sample_rate=0, channel=1, codec=0)
    assert _samples(out) == [150, 0]


def test_odd_length_payload_does_not_crash():
    # A stray trailing byte (not a full int16) is dropped, not fatal.
    out = normalize_to_pcm16k_mono(_pcm([5, 6]) + b"\x01", sample_rate=1, codec=0)
    assert out is not None
