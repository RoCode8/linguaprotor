"""Oral examination and speech assessment API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import AppState, get_pipeline, get_state
from src.infrastructure.azure_openai_client import LLMResponseError
from src.oral.interpretation_pipeline import (
    InterpretationPipeline,
    OralInputError,
    OralTranscriptionError,
)
from src.oral.oral_scorer import ORAL_CRITERION_MAX
from src.oral.schemas import InterpretationMode, OralAssessmentResult, OralExamRequest
from src.oral.transcript_aligner import TranscriptAlignerError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oral", tags=["Oral Examination & Speech"])

MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB ≈ 50 min of 16 kHz mono PCM


@router.post(
    "/assess",
    response_model=OralAssessmentResult,
    status_code=status.HTTP_200_OK,
    summary="Submit candidate oral recording for transcription and rubric scoring",
)
async def assess_oral_examination(
    exam_id: str = Form(...),
    candidate_id: str = Form(...),
    mode: InterpretationMode = Form(...),
    source_text: str = Form(..., description="Source passage the candidate interpreted"),
    source_language: str = Form("en"),
    target_language: str = Form("ar"),
    audio_file: UploadFile = File(..., description="Candidate recording (PCM WAV)"),
    pipeline: InterpretationPipeline = Depends(get_pipeline),
    state: AppState = Depends(get_state),
) -> OralAssessmentResult:
    """Transcribe, align and score an interpretation; enqueue it for HITL triage."""
    audio_bytes = await audio_file.read(MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio exceeds {MAX_AUDIO_BYTES // (1024 * 1024)} MB limit.",
        )

    request = OralExamRequest(
        exam_id=exam_id,
        candidate_id=candidate_id,
        mode=mode,
        source_language=source_language,
        target_language=target_language,
        source_text=source_text,
        candidate_audio_data=audio_bytes,
    )

    try:
        result = await pipeline.process_exam(request)
    except OralInputError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except (OralTranscriptionError, TranscriptAlignerError, LLMResponseError) as e:
        logger.error("Oral assessment failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{e} — the attempt was NOT scored; route to manual assessment.",
        ) from e
    except Exception as e:
        logger.exception("Oral pipeline error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Oral pipeline processing failed: {e}",
        ) from e

    state.oral_results[result.id] = result
    await state.audit_logger.log_event(
        event_type="oral_scoring_completed",
        actor="system",
        resource_id=result.id,
        details={
            "exam_id": result.exam_id,
            "candidate_id": result.candidate_id,
            "mode": result.mode.value,
            "overall_score": result.overall_score,
            "confidence": result.confidence,
            "flags": result.flags,
        },
    )
    await state.review_engine.create_review_item(
        assessment_id=result.id,
        assessment_type="oral",
        candidate_id=result.candidate_id,
        exam_id=result.exam_id,
        ai_score=result.overall_score,
        ai_confidence=result.confidence,
        flags=result.flags,
        ai_criterion_scores={c.criterion: c.score for c in result.criteria},
        criterion_max=ORAL_CRITERION_MAX,
    )
    return result


@router.get(
    "/{result_id}",
    response_model=OralAssessmentResult,
    summary="Get oral assessment result with transcript alignment metadata",
)
async def get_oral_result(
    result_id: str, state: AppState = Depends(get_state)
) -> OralAssessmentResult:
    """Fetch oral assessment packet."""
    if result_id not in state.oral_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Oral assessment {result_id} not found.",
        )
    return state.oral_results[result_id]
