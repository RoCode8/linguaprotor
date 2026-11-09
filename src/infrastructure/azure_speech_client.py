"""Azure AI Speech service wrapper for LinguaProctor.

Provides speech-to-text (STT) and text-to-speech (TTS) capabilities
with support for English and Arabic languages, word-level timestamps,
confidence scores, and configurable audio formats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import azure.cognitiveservices.speech as speechsdk

from src.config import get_settings

logger = logging.getLogger(__name__)

# ── Supported languages ───────────────────────────────────────────────
SUPPORTED_LANGUAGES = frozenset({"en-US", "ar-AE", "ar-SA", "ar-EG"})


class AudioFormat(str, Enum):
    """Supported audio output formats for TTS."""

    WAV = "wav"
    MP3 = "mp3"


class SpeechServiceError(Exception):
    """Raised when a Speech SDK operation fails."""


class SpeechNotConfiguredError(SpeechServiceError):
    """Raised when STT/TTS is attempted without Azure Speech credentials."""


class UnsupportedLanguageError(SpeechServiceError):
    """Raised when a requested language is not in :data:`SUPPORTED_LANGUAGES`."""


class AudioQualityError(SpeechServiceError):
    """Raised when the input audio has quality issues (too short, noisy, etc.)."""


# ── Data models ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class WordInfo:
    """A single recognised word with timing and confidence metadata."""

    word: str
    offset_ms: int
    duration_ms: int
    confidence: float


@dataclass(frozen=True)
class TranscriptionResult:
    """Full result of a speech-to-text operation.

    Attributes:
        text: The concatenated recognised text.
        words: Per-word details including timestamps and confidence.
        language: The BCP-47 language tag used for recognition.
        duration_ms: Total audio duration in milliseconds.
    """

    text: str
    words: list[WordInfo] = field(default_factory=list)
    language: str = ""
    duration_ms: int = 0
    confidence: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0


# ── Voice mapping ─────────────────────────────────────────────────────

_DEFAULT_VOICES: dict[str, str] = {
    "en-US": "en-US-JennyNeural",
    "ar-AE": "ar-AE-FatimaNeural",
    "ar-SA": "ar-SA-HamedNeural",
    "ar-EG": "ar-EG-SalmaNeural",
}

# SDK output format mapping
_AUDIO_FORMAT_MAP: dict[AudioFormat, speechsdk.SpeechSynthesisOutputFormat] = {
    AudioFormat.WAV: speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm,
    AudioFormat.MP3: speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3,
}


def _validate_language(language: str) -> None:
    """Raise :class:`UnsupportedLanguageError` if *language* is not supported."""
    if language not in SUPPORTED_LANGUAGES:
        raise UnsupportedLanguageError(
            f"Language '{language}' is not supported. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
        )


def stream_format_bytes_per_second(wav_bytes: bytes) -> int:
    """Return the PCM byte rate of a WAV payload (0 if unreadable)."""
    import io
    import wave

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getframerate() * wf.getsampwidth() * wf.getnchannels()
    except wave.Error:
        return 0


class AzureSpeechClient:
    """Wrapper around the Azure AI Speech SDK for STT and TTS.

    The underlying SDK objects are initialised lazily on first use.

    Usage::

        client = AzureSpeechClient()
        result = await client.speech_to_text(audio_bytes, language="en-US")
        audio  = await client.text_to_speech("Hello world", language="en-US")
    """

    def __init__(self) -> None:
        self._speech_config: speechsdk.SpeechConfig | None = None

    # ── Lazy init ──────────────────────────────────────────────────────

    def _get_speech_config(self) -> speechsdk.SpeechConfig:
        """Create or return the cached :class:`SpeechConfig`."""
        if self._speech_config is None:
            settings = get_settings().azure_speech
            if not settings.is_configured:
                raise SpeechNotConfiguredError(
                    "Azure Speech is not configured (set AZURE_SPEECH_ENDPOINT, or "
                    "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)."
                )
            if settings.uses_key_auth:
                assert settings.key is not None
                self._speech_config = speechsdk.SpeechConfig(
                    subscription=settings.key.get_secret_value(),
                    region=settings.region,
                )
            else:
                # Keyless: Microsoft Entra ID against the resource's custom-domain
                # endpoint (requires the "Cognitive Services Speech User" role).
                from azure.identity import DefaultAzureCredential

                self._speech_config = speechsdk.SpeechConfig(
                    endpoint=settings.endpoint,
                    token_credential=DefaultAzureCredential(),
                )
            # Request word-level detail by default
            self._speech_config.request_word_level_timestamps()
            self._speech_config.output_format = speechsdk.OutputFormat.Detailed
            logger.info(
                "Initialized Azure Speech config — auth=%s",
                "key" if settings.uses_key_auth else "entra-id",
            )
        return self._speech_config

    # ── Speech-to-Text ─────────────────────────────────────────────────

    async def speech_to_text(
        self,
        audio_data: bytes,
        language: str,
    ) -> TranscriptionResult:
        """Transcribe *audio_data* to text with word-level timestamps.

        The method runs the synchronous SDK recogniser in a thread pool
        so that the caller's event loop is not blocked.

        Args:
            audio_data: Raw audio bytes (WAV format recommended).
            language: BCP-47 language tag (e.g. ``"en-US"``).

        Returns:
            A :class:`TranscriptionResult` with per-word details.

        Raises:
            UnsupportedLanguageError: If *language* is not supported.
            AudioQualityError: If the audio could not be recognised due
                to quality issues.
            SpeechServiceError: On any other SDK error.
        """
        import asyncio
        import io
        import json
        import threading
        import wave

        _validate_language(language)

        if not audio_data or len(audio_data) < 1024:
            raise AudioQualityError(
                "Audio data is too short or empty to perform recognition."
            )

        # The push stream expects raw PCM; strip the RIFF/WAV header and
        # declare the real format instead of feeding header bytes as audio.
        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wf:
                stream_format = speechsdk.audio.AudioStreamFormat(
                    samples_per_second=wf.getframerate(),
                    bits_per_sample=wf.getsampwidth() * 8,
                    channels=wf.getnchannels(),
                )
                pcm = wf.readframes(wf.getnframes())
        except wave.Error as exc:
            raise AudioQualityError(f"Audio is not valid PCM WAV: {exc}") from exc

        config = self._get_speech_config()
        config.speech_recognition_language = language

        stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        stream.write(pcm)
        stream.close()

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=config,
            audio_config=speechsdk.audio.AudioConfig(stream=stream),
        )

        # Continuous recognition: interpretation recordings contain many
        # utterances; recognize_once() would stop after the first one.
        segments: list[str] = []
        words: list[WordInfo] = []
        utterance_conf: list[float] = []
        errors: list[str] = []
        done = threading.Event()

        def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            res = evt.result
            if res.reason != speechsdk.ResultReason.RecognizedSpeech or not res.text:
                return
            segments.append(res.text)
            try:
                detail = json.loads(
                    res.properties.get(
                        speechsdk.PropertyId.SpeechServiceResponse_JsonResult, ""
                    ) or "{}"
                )
                best = (detail.get("NBest") or [{}])[0]
                if "Confidence" in best:
                    utterance_conf.append(float(best["Confidence"]))
                for w in best.get("Words", []):
                    words.append(
                        WordInfo(
                            word=w.get("Word", ""),
                            offset_ms=int(w.get("Offset", 0) / 10_000),  # ticks → ms
                            duration_ms=int(w.get("Duration", 0) / 10_000),
                            confidence=float(w.get("Confidence", best.get("Confidence", 0.0))),
                        )
                    )
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                logger.warning("Could not parse word-level detail: %s", exc)

        def on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
            if evt.reason == speechsdk.CancellationReason.Error:
                errors.append(f"{evt.error_code}: {evt.error_details}")
            done.set()

        recognizer.recognized.connect(on_recognized)
        recognizer.canceled.connect(on_canceled)
        recognizer.session_stopped.connect(lambda _evt: done.set())

        def _run() -> None:
            recognizer.start_continuous_recognition()
            done.wait(timeout=max(60.0, len(pcm) / 16_000))  # generous upper bound
            recognizer.stop_continuous_recognition()

        await asyncio.get_running_loop().run_in_executor(None, _run)

        if errors:
            msg = f"Speech recognition cancelled — {'; '.join(errors)}"
            logger.error(msg)
            raise SpeechServiceError(msg)
        if not segments:
            raise AudioQualityError(
                "No speech could be recognised. Check audio quality, volume, or language setting."
            )

        if utterance_conf:
            confidence = sum(utterance_conf) / len(utterance_conf)
        elif words:
            confidence = sum(w.confidence for w in words) / len(words)
        else:
            confidence = 0.0
        duration_ms = int(
            len(pcm) / (stream_format_bytes_per_second(audio_data) or 1) * 1000
        )

        return TranscriptionResult(
            text=" ".join(segments),
            words=words,
            language=language,
            duration_ms=duration_ms,
            confidence=min(max(confidence, 0.0), 1.0),
        )

    # ── Text-to-Speech ─────────────────────────────────────────────────

    async def text_to_speech(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        audio_format: AudioFormat = AudioFormat.WAV,
    ) -> bytes:
        """Synthesise *text* into audio bytes.

        Args:
            text: The text to speak.
            language: BCP-47 language tag (e.g. ``"ar-SA"``).
            voice: Optional voice name override.  Defaults to the
                language-specific default voice.
            audio_format: Desired output format (:class:`AudioFormat`).

        Returns:
            The synthesised audio as raw bytes.

        Raises:
            UnsupportedLanguageError: If *language* is not supported.
            SpeechServiceError: On synthesis failure.
        """
        import asyncio

        _validate_language(language)

        config = self._get_speech_config()
        resolved_voice = voice or _DEFAULT_VOICES.get(language, "en-US-JennyNeural")
        config.speech_synthesis_voice_name = resolved_voice
        config.set_speech_synthesis_output_format(_AUDIO_FORMAT_MAP[audio_format])

        # Synthesise to in-memory stream (no speaker)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=config,
            audio_config=None,  # no default speaker output
        )

        loop = asyncio.get_running_loop()
        result: speechsdk.SpeechSynthesisResult = await loop.run_in_executor(
            None,
            synthesizer.speak_text,
            text,
        )

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            logger.info(
                "TTS completed — lang=%s voice=%s bytes=%d",
                language,
                resolved_voice,
                len(result.audio_data),
            )
            return result.audio_data

        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation = result.cancellation_details
            error_msg = (
                f"Speech synthesis cancelled — "
                f"reason={cancellation.reason}, detail={cancellation.error_details}"
            )
            logger.error(error_msg)
            raise SpeechServiceError(error_msg)

        raise SpeechServiceError(f"Unexpected synthesis result: {result.reason}")
