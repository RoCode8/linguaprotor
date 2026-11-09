"""Pydantic v2 schemas for the LinguaProctor oral assessment module.

Defines data models for:
- Interpretation modes (consecutive, simultaneous, dialogue)
- Audio quality metrics and validation
- Speech-to-text transcription results with word-level timestamps
- Transcript alignment between source and candidate
- Scoring criteria and overall assessment results
- Oral exam request payloads
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InterpretationMode(str, Enum):
    """Interpretation mode for oral translation assessments.

    Attributes:
        CONSECUTIVE: Candidate translates after the source finishes speaking.
        SIMULTANEOUS: Candidate translates in real-time alongside the source.
        DIALOGUE: Two-party conversation with alternating role switching.
    """

    CONSECUTIVE = "consecutive"
    SIMULTANEOUS = "simultaneous"
    DIALOGUE = "dialogue"


class AudioQualityMetrics(BaseModel):
    """Quality metrics extracted from an audio recording.

    Used to pre-screen candidate audio before transcription and scoring,
    flagging recordings that may produce unreliable assessments.
    """

    duration_seconds: float = Field(
        ...,
        description="Total duration of the audio in seconds.",
    )
    sample_rate: int | None = Field(
        default=None,
        description="Detected sample rate in Hz, if determinable.",
    )
    estimated_snr_db: float | None = Field(
        default=None,
        description="Estimated signal-to-noise ratio in decibels.",
    )
    is_acceptable: bool = Field(
        ...,
        description="Whether the audio quality is sufficient for reliable assessment.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Detected quality issues, e.g. 'low_volume', 'background_noise', "
            "'clipping', 'too_short', 'too_long', 'excessive_silence'."
        ),
    )


class WordTimestamp(BaseModel):
    """A single word with timing information from speech-to-text output."""

    word: str = Field(..., description="The recognized word.")
    start_ms: int = Field(..., description="Start offset in milliseconds from audio start.")
    end_ms: int = Field(..., description="End offset in milliseconds from audio start.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Recognition confidence for this word (0.0–1.0).",
    )


class TranscriptionResult(BaseModel):
    """Full transcription result from Azure Speech-to-Text.

    Includes both the full text and word-level timing information
    for alignment and fluency analysis.
    """

    text: str = Field(..., description="Complete transcribed text.")
    language: str = Field(
        ...,
        description="BCP-47 language tag of the transcription (e.g. 'en-US', 'ar-SA').",
    )
    words: list[WordTimestamp] = Field(
        default_factory=list,
        description="Word-level timestamps with confidence scores.",
    )
    duration_seconds: float = Field(
        ...,
        description="Total audio duration that was transcribed, in seconds.",
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Aggregate confidence across all recognized words.",
    )


class TranscriptAlignment(BaseModel):
    """Alignment between a source transcript segment and the corresponding candidate segment.

    Captures how well each source phrase/sentence maps to the candidate's
    translation, along with timing metadata for simultaneous mode analysis.
    """

    source_segment: str = Field(
        ...,
        description="Text of the source-language segment.",
    )
    source_start_ms: int = Field(
        ...,
        description="Start time of the source segment in milliseconds.",
    )
    source_end_ms: int = Field(
        ...,
        description="End time of the source segment in milliseconds.",
    )
    candidate_segment: str = Field(
        ...,
        description="Text of the candidate's translated segment.",
    )
    candidate_start_ms: int | None = Field(
        default=None,
        description="Start time of the candidate segment in milliseconds (if available).",
    )
    candidate_end_ms: int | None = Field(
        default=None,
        description="End time of the candidate segment in milliseconds (if available).",
    )
    alignment_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Semantic alignment score between source and candidate segments (0.0–1.0).",
    )


class OralCriterionScore(BaseModel):
    """Score for a single assessment criterion in oral evaluation.

    Criteria include accuracy, fluency, comprehension, and delivery.
    """

    criterion: str = Field(
        ...,
        description="Name of the scoring criterion (accuracy, fluency, comprehension, delivery).",
    )
    score: float = Field(
        ...,
        ge=0.0,
        description="Score awarded for this criterion.",
    )
    max_score: float = Field(
        ...,
        gt=0.0,
        description="Maximum possible score for this criterion.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence supporting the awarded score.",
    )
    summary: str = Field(
        ...,
        description="Human-readable summary of the evaluation for this criterion.",
    )


class OralAssessmentResult(BaseModel):
    """Complete result of an oral translation assessment.

    Aggregates transcription, alignment, scoring, and quality data
    for a single candidate's oral exam attempt.
    """

    id: str = Field(..., description="Unique identifier for this assessment result.")
    exam_id: str = Field(..., description="Identifier of the parent examination.")
    candidate_id: str = Field(..., description="Identifier of the assessed candidate.")
    mode: InterpretationMode = Field(
        ...,
        description="Interpretation mode used for this assessment.",
    )
    source_language: str = Field(
        ...,
        description="BCP-47 tag of the source language.",
    )
    target_language: str = Field(
        ...,
        description="BCP-47 tag of the target language.",
    )
    source_transcript: TranscriptionResult = Field(
        ...,
        description="Transcription of the source audio.",
    )
    candidate_transcript: TranscriptionResult = Field(
        ...,
        description="Transcription of the candidate's audio.",
    )
    alignments: list[TranscriptAlignment] = Field(
        default_factory=list,
        description="Segment-level alignments between source and candidate transcripts.",
    )
    criteria: list[OralCriterionScore] = Field(
        default_factory=list,
        description="Scores for each evaluation criterion.",
    )
    overall_score: float = Field(
        ...,
        ge=0.0,
        description="Aggregate overall score.",
    )
    overall_max: float = Field(
        ...,
        gt=0.0,
        description="Maximum possible aggregate score.",
    )
    audio_quality: AudioQualityMetrics = Field(
        ...,
        description="Quality metrics for the candidate's audio.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="System confidence in the overall assessment (0.0–1.0).",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Flags indicating issues or exceptional conditions.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible metadata dictionary for additional context.",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when this assessment was created.",
    )


class OralExamRequest(BaseModel):
    """Request payload to initiate an oral translation assessment.

    Supports multiple input modes:
    - Pre-recorded source audio via URL
    - Source text for TTS generation
    - Candidate audio via direct upload or blob storage URL
    """

    exam_id: str = Field(..., description="Identifier of the parent examination.")
    candidate_id: str = Field(..., description="Identifier of the candidate.")
    mode: InterpretationMode = Field(
        ...,
        description="Interpretation mode for this assessment.",
    )
    source_language: str = Field(
        default="en",
        description="BCP-47 language tag for the source material.",
    )
    target_language: str = Field(
        default="ar",
        description="BCP-47 language tag for the expected translation.",
    )
    source_audio_url: str | None = Field(
        default=None,
        description="URL to a pre-recorded source audio file.",
    )
    source_text: str | None = Field(
        default=None,
        description="Source text for TTS generation (used if source_audio_url is not provided).",
    )
    candidate_audio_data: bytes | None = Field(
        default=None,
        description="Raw candidate audio bytes (direct upload).",
        exclude=True,
    )
    candidate_audio_url: str | None = Field(
        default=None,
        description="Blob storage URL for the candidate's audio recording.",
    )
