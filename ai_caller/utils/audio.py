"""Audio format conversion utilities.

Twilio Media Streams: 8kHz, mono, μ-law (mulaw), 64kbps
Deepgram accepts: mulaw, 8kHz directly
ElevenLabs outputs: MP3 (44.1kHz) → must convert to 8kHz mulaw
"""
import base64
import numpy as np

try:
    import audioop
    HAS_AUDIOOP = True
except ImportError:
    HAS_AUDIOOP = False

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False


def decode_twilio_media(payload: str) -> bytes:
    """Decode base64 μ-law payload from Twilio."""
    return base64.b64decode(payload)


def encode_twilio_media(data: bytes) -> str:
    """Encode raw bytes to base64 string for Twilio."""
    return base64.b64encode(data).decode("utf-8")


def pcm16_to_mulaw(pcm: np.ndarray) -> bytes:
    """Convert 16-bit PCM numpy array to μ-law bytes."""
    pcm = np.clip(pcm, -32768, 32767).astype(np.int16)
    if HAS_AUDIOOP:
        return audioop.lin2ulaw(pcm.tobytes(), 2)
    # Pure numpy fallback (Python 3.13+ compatibility)
    x = pcm.astype(np.float32)
    sign = np.sign(x)
    x = np.abs(x)
    x = np.clip(x, 0, 32767)
    mu = 255.0
    encoded = sign * (np.log(1.0 + mu * x / 32767.0) / np.log(1.0 + mu))
    ulaw = np.round((encoded + 1.0) / 2.0 * 255.0).astype(np.uint8)
    return ulaw.tobytes()


def mulaw_to_pcm16(data: bytes) -> np.ndarray:
    """Convert μ-law bytes to 16-bit PCM numpy array."""
    if HAS_AUDIOOP:
        pcm_bytes = audioop.ulaw2lin(data, 2)
        return np.frombuffer(pcm_bytes, dtype=np.int16)
    u = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
    sign = np.where(u & 0x80, -1, 1)
    u = u & 0x7F
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    pcm = sign * (((mantissa << 4) + 0x08) << (exponent + 1))
    return pcm.astype(np.int16)


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1-D audio using linear interpolation."""
    if orig_sr == target_sr:
        return audio
    ratio = target_sr / orig_sr
    new_len = int(len(audio) * ratio)
    old_indices = np.linspace(0, len(audio) - 1, new_len)
    indices = old_indices.astype(np.int32)
    frac = old_indices - indices
    next_indices = np.clip(indices + 1, 0, len(audio) - 1)
    resampled = audio[indices] * (1.0 - frac) + audio[next_indices] * frac
    return resampled.astype(audio.dtype)


def mp3_to_mulaw_8k(mp3_bytes: bytes) -> bytes:
    """Convert MP3 bytes → mono 8kHz 16-bit PCM → μ-law."""
    if not HAS_PYDUB:
        raise ImportError(
            "pydub is required for MP3 decoding. "
            "Install: pip install pydub (also requires ffmpeg)"
        )
    audio = AudioSegment.from_mp3(mp3_bytes)
    audio = audio.set_channels(1).set_frame_rate(8000).set_sample_width(2)
    pcm = np.array(audio.get_array_of_samples(), dtype=np.int16)
    return pcm16_to_mulaw(pcm)


def chunk_audio(audio_bytes: bytes, frame_duration_ms: int = 20, sample_rate: int = 8000):
    """Split μ-law byte stream into Twilio-friendly chunks."""
    frame_size = int(sample_rate * frame_duration_ms / 1000)
    for i in range(0, len(audio_bytes), frame_size):
        yield audio_bytes[i : i + frame_size]
