"""Unit tests for the oral assessment and speech pipeline."""

from __future__ import annotations

import pytest

from src.oral.audio_processor import AudioProcessingError, AudioProcessor
from src.oral.interpretation_pipeline import (
    InterpretationPipeline,
    OralInputError,
    OralTranscriptionError,
    resolve_locale,
)
from src.oral.schemas import InterpretationMode, OralExamRequest
from tests.conftest import FakeLLM, FakeSpeech, make_wav


def test_audio_processor_metrics_on_real_wav() -> None:
    metrics = AudioProcessor().analyze_quality(make_wav(seconds=2.0))
    assert metrics.duration_seconds == pytest.approx(2.0)
    assert metrics.sample_rate == 16_000


def test_audio_processor_rejects_header_only_wav() -> None:
    with pytest.raises(AudioProcessingError):
        AudioProcessor().analyze_quality(b"RIFF" + b"\x00" * 36)


def test_normalize_resamples_to_16k_mono() -> None:
    out = AudioProcessor().normalize_audio(make_wav(seconds=1.0, rate=44_100))
    m = AudioProcessor().analyze_quality(out)
    assert m.sample_rate == 16_000 and m.duration_seconds == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize(
    "lang,expected", [("ar", "ar-AE"), ("ar-SA", "ar-SA"), ("en", "en-US"), ("en-GB", "en-US")]
)
def test_resolve_locale(lang: str, expected: str) -> None:
    assert resolve_locale(lang) == expected


def _request(**kw: object) -> OralExamRequest:
    base: dict[str, object] = dict(
        exam_id="oral-1",
        candidate_id="cand-2",
        mode=InterpretationMode.CONSECUTIVE,
        source_text="The committee convened this morning.",
        candidate_audio_data=make_wav(),
    )
    base.update(kw)
    return OralExamRequest(**base)  # type: ignore[arg-type]


async def test_interpretation_pipeline_flow() -> None:
    pipeline = InterpretationPipeline(speech_client=FakeSpeech(), openai_client=FakeLLM())  # type: ignore[arg-type]
    res = await pipeline.process_exam(_request())
    assert res.overall_score == 17.0
    assert [c.criterion for c in res.criteria] == ["accuracy", "fluency", "comprehension", "delivery"]
    assert res.mode == InterpretationMode.CONSECUTIVE
    assert res.candidate_transcript.duration_seconds == 3.0


async def test_pipeline_never_fabricates_a_transcript() -> None:
    pipeline = InterpretationPipeline(
        speech_client=FakeSpeech(RuntimeError("network down")), openai_client=FakeLLM()  # type: ignore[arg-type]
    )
    with pytest.raises(OralTranscriptionError):
        await pipeline.process_exam(_request())
    with pytest.raises(OralInputError):
        await pipeline.process_exam(_request(candidate_audio_data=None))
    with pytest.raises(OralInputError):
        await pipeline.process_exam(_request(source_text=None))
