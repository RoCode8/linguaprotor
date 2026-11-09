"""End-to-end oral examination and speech processing pipeline.

Handles audio ingestion, quality screening, speech-to-text with word-level
timestamps, transcript alignment, and multi-criteria oral assessment.

Integrity rule: the pipeline never scores anything other than what the
candidate actually said.  Missing audio, unreadable audio, or a failed
transcription raise an error instead of falling back to placeholder content.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.infrastructure.azure_speech_client import SUPPORTED_LANGUAGES, AzureSpeechClient
from src.oral.audio_processor import AudioProcessingError, AudioProcessor
from src.oral.oral_scorer import OralScorer
from src.oral.schemas import (
    OralAssessmentResult,
    OralExamRequest,
    TranscriptionResult,
    WordTimestamp,
)
from src.oral.transcript_aligner import TranscriptAligner

logger = logging.getLogger(__name__)

_DEFAULT_LOCALES = {"en": "en-US", "ar": "ar-AE"}
_LOW_STT_CONFIDENCE = 0.70
_REPAIRED_OUTPUT_CONFIDENCE_CAP = 0.74
_MISSING_CRITERION_CONFIDENCE_CAP = 0.5


class OralPipelineError(RuntimeError):
    """Base class for oral pipeline failures."""


class OralInputError(OralPipelineError):
    """The submission is incomplete or the audio is unreadable (client error)."""


class OralTranscriptionError(OralPipelineError):
    """Speech-to-text failed; the attempt cannot be scored automatically."""


def resolve_locale(language: str) -> str:
    """Map a language code (``ar``) or tag (``ar-SA``) to a supported STT locale."""
    if language in SUPPORTED_LANGUAGES:
        return language
    base = language.split("-")[0].lower()
    if base in _DEFAULT_LOCALES:
        return _DEFAULT_LOCALES[base]
    raise OralInputError(
        f"Unsupported language '{language}'. Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
    )


class InterpretationPipeline:
    """Orchestrates consecutive, simultaneous, and dialogue oral assessments."""

    def __init__(
        self,
        speech_client: AzureSpeechClient | None = None,
        openai_client: AzureOpenAIClient | None = None,
    ) -> None:
        self.speech_client = speech_client or AzureSpeechClient()
        self.openai_client = openai_client or AzureOpenAIClient()
        self.audio_processor = AudioProcessor()
        self.aligner = TranscriptAligner(self.openai_client)
        self.oral_scorer = OralScorer(self.openai_client)

    async def process_exam(self, request: OralExamRequest) -> OralAssessmentResult:
        """Process an oral examination submission."""
        logger.info(
            "Processing oral exam %s for candidate %s in %s mode",
            request.exam_id,
            request.candidate_id,
            request.mode.value,
        )

        # 1. Validate inputs — no placeholders, ever.
        if not request.source_text or not request.source_text.strip():
            raise OralInputError(
                "source_text is required (source-audio transcription is not supported yet)."
            )
        if not request.candidate_audio_data:
            raise OralInputError("Candidate audio recording is required.")
        locale = resolve_locale(request.target_language)

        source_transcript = TranscriptionResult(
            text=request.source_text.strip(),
            language=request.source_language,
            words=[],
            duration_seconds=0.0,
            overall_confidence=1.0,
        )

        # 2. Audio quality screening + normalisation (16 kHz mono 16-bit)
        try:
            audio_quality = self.audio_processor.analyze_quality(request.candidate_audio_data)
            normalised_audio = self.audio_processor.normalize_audio(request.candidate_audio_data)
        except AudioProcessingError as exc:
            raise OralInputError(f"Candidate audio could not be read: {exc}") from exc

        # 3. Speech-to-text (failure is surfaced, never papered over)
        candidate_transcript = await self._transcribe_candidate(normalised_audio, locale)

        # 4. Align source and candidate transcripts
        alignments = await self.aligner.align_transcripts(
            source=source_transcript,
            candidate=candidate_transcript,
            source_language=request.source_language,
            target_language=request.target_language,
        )

        # 5. Score the interpretation
        criteria, scorer_flags = await self.oral_scorer.score(
            source_transcript=source_transcript,
            candidate_transcript=candidate_transcript,
            alignments=alignments,
            mode=request.mode,
            source_language=request.source_language,
            target_language=request.target_language,
        )

        flags: list[str] = list(scorer_flags)
        confidence = candidate_transcript.overall_confidence
        if not audio_quality.is_acceptable:
            flags.append("audio_quality_warning")
            flags.extend(f"audio_issue:{i}" for i in audio_quality.issues)
        if confidence < _LOW_STT_CONFIDENCE:
            flags.append("low_stt_confidence")
        if any(f.startswith("score_clamped") for f in scorer_flags):
            confidence = min(confidence, _REPAIRED_OUTPUT_CONFIDENCE_CAP)
        if any(f.startswith("criterion_missing") for f in scorer_flags):
            confidence = min(confidence, _MISSING_CRITERION_CONFIDENCE_CAP)

        total_score = sum(c.score for c in criteria)
        total_max = sum(c.max_score for c in criteria)

        return OralAssessmentResult(
            id=str(uuid.uuid4()),
            exam_id=request.exam_id,
            candidate_id=request.candidate_id,
            mode=request.mode,
            source_language=request.source_language,
            target_language=request.target_language,
            source_transcript=source_transcript,
            candidate_transcript=candidate_transcript,
            alignments=alignments,
            criteria=criteria,
            overall_score=round(total_score, 2),
            overall_max=round(total_max, 2),
            audio_quality=audio_quality,
            confidence=round(confidence, 3),
            flags=flags,
            metadata={"mode": request.mode.value, "stt_locale": locale},
            created_at=datetime.now(UTC),
        )

    async def _transcribe_candidate(self, audio: bytes, locale: str) -> TranscriptionResult:
        """Transcribe candidate audio with Azure Speech; raise on any failure."""
        try:
            res = await self.speech_client.speech_to_text(audio, language=locale)
        except Exception as exc:
            logger.error("Speech-to-text failed: %s", exc)
            raise OralTranscriptionError(f"Speech-to-text failed: {exc}") from exc

        if not res.text or not res.text.strip():
            raise OralTranscriptionError("Speech-to-text returned an empty transcript.")

        return TranscriptionResult(
            text=res.text,
            language=res.language or locale,
            words=[
                WordTimestamp(
                    word=w.word,
                    start_ms=w.offset_ms,
                    end_ms=w.offset_ms + w.duration_ms,
                    confidence=min(max(w.confidence, 0.0), 1.0),
                )
                for w in res.words
            ],
            duration_seconds=res.duration_seconds,
            overall_confidence=min(max(res.confidence, 0.0), 1.0),
        )
