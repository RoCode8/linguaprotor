"""Audio pre-processing utilities for the LinguaProctor oral assessment pipeline.

Provides basic audio quality analysis and normalization using only the
standard-library ``wave`` module and ``numpy`` — no heavy DSP dependencies.
Designed to pre-screen candidate recordings before sending them to Azure
Speech-to-Text, catching common quality issues early.
"""

from __future__ import annotations

import io
import logging
import wave

import numpy as np

from src.oral.schemas import AudioQualityMetrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_MIN_DURATION_S: float = 1.0
_DEFAULT_MAX_DURATION_S: float = 1800.0  # 30 minutes
_TARGET_SAMPLE_RATE: int = 16_000
_TARGET_SAMPLE_WIDTH: int = 2  # 16-bit
_TARGET_CHANNELS: int = 1  # mono
_CLIPPING_THRESHOLD: float = 0.98  # fraction of max amplitude
_CLIPPING_RATIO_LIMIT: float = 0.01  # max 1% clipped samples
_SILENCE_THRESHOLD_DB: float = -40.0
_SILENCE_RATIO_LIMIT: float = 0.80  # flag if > 80% silence
_LOW_VOLUME_RMS_THRESHOLD: float = 0.01  # fraction of max amplitude
_SNR_ACCEPTABLE_THRESHOLD_DB: float = 10.0


class AudioProcessingError(Exception):
    """Raised when audio data cannot be read or processed."""


class AudioProcessor:
    """Analyses and normalizes audio data for the oral assessment pipeline.

    All methods work with raw ``bytes`` representing WAV-format audio data.
    No external DSP libraries are required — only ``wave`` and ``numpy``.

    Args:
        min_duration_s: Minimum acceptable audio duration in seconds.
        max_duration_s: Maximum acceptable audio duration in seconds.
    """

    def __init__(
        self,
        min_duration_s: float = _DEFAULT_MIN_DURATION_S,
        max_duration_s: float = _DEFAULT_MAX_DURATION_S,
    ) -> None:
        self.min_duration_s = min_duration_s
        self.max_duration_s = max_duration_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_quality(self, audio_data: bytes) -> AudioQualityMetrics:
        """Analyse an audio recording and return quality metrics.

        Checks duration, estimates SNR, and detects common quality issues
        such as silence, clipping, low volume, and extreme durations.

        Args:
            audio_data: Raw WAV-format audio bytes.

        Returns:
            An :class:`AudioQualityMetrics` instance summarising the analysis.

        Raises:
            AudioProcessingError: If the data cannot be parsed as valid WAV.
        """
        samples, sample_rate, duration_s = self._read_wav_samples(audio_data)
        issues: list[str] = []

        # Duration checks
        if duration_s < self.min_duration_s:
            issues.append("too_short")
        if duration_s > self.max_duration_s:
            issues.append("too_long")

        # Clipping detection
        if self._detect_clipping(samples):
            issues.append("clipping")

        # Silence detection
        if self._detect_excessive_silence(samples, sample_rate):
            issues.append("excessive_silence")

        # Low volume detection
        if self._detect_low_volume(samples):
            issues.append("low_volume")

        # SNR estimation
        estimated_snr_db = self._estimate_snr(samples, sample_rate)
        if estimated_snr_db is not None and estimated_snr_db < _SNR_ACCEPTABLE_THRESHOLD_DB:
            issues.append("background_noise")

        is_acceptable = len(issues) == 0

        logger.info(
            "Audio quality analysis complete: duration=%.1fs sample_rate=%d "
            "snr=%.1fdB acceptable=%s issues=%s",
            duration_s,
            sample_rate,
            estimated_snr_db if estimated_snr_db is not None else float("nan"),
            is_acceptable,
            issues,
        )

        return AudioQualityMetrics(
            duration_seconds=round(duration_s, 3),
            sample_rate=sample_rate,
            estimated_snr_db=round(estimated_snr_db, 2) if estimated_snr_db is not None else None,
            is_acceptable=is_acceptable,
            issues=issues,
        )

    def normalize_audio(self, audio_data: bytes) -> bytes:
        """Normalize audio to a consistent format for Azure Speech-to-Text.

        Converts to 16 kHz, 16-bit, mono WAV with peak-normalized volume.

        Args:
            audio_data: Raw WAV-format audio bytes.

        Returns:
            Normalized WAV-format audio bytes.

        Raises:
            AudioProcessingError: If the data cannot be parsed or converted.
        """
        samples, sample_rate, _ = self._read_wav_samples(audio_data)

        # Resample if needed
        if sample_rate != _TARGET_SAMPLE_RATE:
            samples = self._resample(samples, sample_rate, _TARGET_SAMPLE_RATE)

        # Peak normalize
        samples = self._peak_normalize(samples)

        # Convert back to WAV bytes
        return self._samples_to_wav(samples, _TARGET_SAMPLE_RATE)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_wav_samples(audio_data: bytes) -> tuple[np.ndarray, int, float]:
        """Parse WAV bytes into a float64 numpy array of samples.

        Returns:
            Tuple of (samples as float64 in [-1, 1], sample_rate, duration_seconds).
        """
        try:
            buf = io.BytesIO(audio_data)
            with wave.open(buf, "rb") as wf:
                n_channels: int = wf.getnchannels()
                sample_width: int = wf.getsampwidth()
                sample_rate: int = wf.getframerate()
                n_frames: int = wf.getnframes()
                raw_frames: bytes = wf.readframes(n_frames)
        except wave.Error as exc:
            raise AudioProcessingError(f"Failed to parse WAV data: {exc}") from exc
        except Exception as exc:
            raise AudioProcessingError(f"Unexpected error reading audio: {exc}") from exc

        if n_frames == 0:
            raise AudioProcessingError("Audio file contains zero frames.")

        # Determine numpy dtype from sample width
        if sample_width == 1:
            dtype = np.uint8
            max_val = 128.0
        elif sample_width == 2:
            dtype = np.int16
            max_val = 32768.0
        elif sample_width == 4:
            dtype = np.int32
            max_val = 2_147_483_648.0
        else:
            raise AudioProcessingError(f"Unsupported sample width: {sample_width} bytes")

        samples = np.frombuffer(raw_frames, dtype=dtype).astype(np.float64)

        # Centre uint8 around zero
        if sample_width == 1:
            samples = samples - 128.0

        # Normalize to [-1, 1]
        samples = samples / max_val

        # Mix down to mono if multi-channel
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        duration_s = len(samples) / sample_rate
        return samples, sample_rate, duration_s

    @staticmethod
    def _detect_clipping(samples: np.ndarray) -> bool:
        """Return True if the fraction of near-max samples exceeds the limit."""
        abs_samples = np.abs(samples)
        clipped = np.sum(abs_samples >= _CLIPPING_THRESHOLD)
        ratio = clipped / len(samples) if len(samples) > 0 else 0.0
        return ratio > _CLIPPING_RATIO_LIMIT

    @staticmethod
    def _detect_excessive_silence(
        samples: np.ndarray,
        sample_rate: int,
        frame_ms: int = 30,
    ) -> bool:
        """Return True if the fraction of silent frames exceeds the limit.

        Silence is determined by computing RMS energy per frame and comparing
        against a dB threshold.
        """
        frame_len = int(sample_rate * frame_ms / 1000)
        if frame_len == 0 or len(samples) < frame_len:
            return False

        n_frames = len(samples) // frame_len
        silent_frames = 0
        for i in range(n_frames):
            frame = samples[i * frame_len : (i + 1) * frame_len]
            rms = np.sqrt(np.mean(frame ** 2))
            rms_db = 20.0 * np.log10(rms + 1e-10)
            if rms_db < _SILENCE_THRESHOLD_DB:
                silent_frames += 1

        ratio = silent_frames / n_frames if n_frames > 0 else 0.0
        return ratio > _SILENCE_RATIO_LIMIT

    @staticmethod
    def _detect_low_volume(samples: np.ndarray) -> bool:
        """Return True if overall RMS is below the low-volume threshold."""
        rms = np.sqrt(np.mean(samples ** 2))
        return rms < _LOW_VOLUME_RMS_THRESHOLD

    @staticmethod
    def _estimate_snr(
        samples: np.ndarray,
        sample_rate: int,
        frame_ms: int = 30,
    ) -> float | None:
        """Estimate signal-to-noise ratio using a simple energy-based method.

        Segments frames into speech and noise based on energy, then computes
        the ratio of mean speech energy to mean noise energy in dB.

        Returns:
            Estimated SNR in dB, or ``None`` if estimation is not possible.
        """
        frame_len = int(sample_rate * frame_ms / 1000)
        if frame_len == 0 or len(samples) < frame_len:
            return None

        n_frames = len(samples) // frame_len
        energies: list[float] = []
        for i in range(n_frames):
            frame = samples[i * frame_len : (i + 1) * frame_len]
            energies.append(float(np.mean(frame ** 2)))

        if not energies:
            return None

        energy_arr = np.array(energies)
        # Use median as a threshold to split speech / noise
        median_energy = float(np.median(energy_arr))

        speech_mask = energy_arr > median_energy
        noise_mask = ~speech_mask

        speech_energy = float(np.mean(energy_arr[speech_mask])) if np.any(speech_mask) else 1e-10
        noise_energy = float(np.mean(energy_arr[noise_mask])) if np.any(noise_mask) else 1e-10

        if noise_energy <= 0:
            return None

        snr_db = 10.0 * np.log10(speech_energy / noise_energy)
        return float(snr_db)

    @staticmethod
    def _resample(samples: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
        """Resample audio using linear interpolation.

        This is a lightweight resampling suitable for speech; it avoids
        importing ``scipy`` or other heavy signal-processing libraries.
        """
        if orig_rate == target_rate:
            return samples

        duration = len(samples) / orig_rate
        target_len = int(duration * target_rate)
        indices = np.linspace(0, len(samples) - 1, num=target_len)
        resampled = np.interp(indices, np.arange(len(samples)), samples)
        return resampled

    @staticmethod
    def _peak_normalize(samples: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
        """Peak-normalize samples so the maximum absolute value equals *target_peak*."""
        peak = np.max(np.abs(samples))
        if peak < 1e-10:
            return samples
        return samples * (target_peak / peak)

    @staticmethod
    def _samples_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
        """Convert float64 samples back to 16-bit PCM WAV bytes."""
        # Clip and convert to int16
        clipped = np.clip(samples, -1.0, 1.0)
        int_samples = (clipped * 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(_TARGET_CHANNELS)
            wf.setsampwidth(_TARGET_SAMPLE_WIDTH)
            wf.setframerate(sample_rate)
            wf.writeframes(int_samples.tobytes())

        return buf.getvalue()
