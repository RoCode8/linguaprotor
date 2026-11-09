"""Transcript alignment for the LinguaProctor oral assessment pipeline.

Aligns source-language transcript segments with candidate translation
segments using Azure OpenAI embeddings for semantic similarity. Produces
:class:`TranscriptAlignment` records that feed into the oral scorer.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.oral.schemas import TranscriptAlignment, TranscriptionResult, WordTimestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_MIN_SEGMENT_WORDS = 3


class TranscriptAlignerError(Exception):
    """Raised when transcript alignment fails."""


class TranscriptAligner:
    """Aligns source transcript segments to candidate translation segments.

    Uses Azure OpenAI embeddings to compute semantic similarity between
    sentence-level segments, then applies a greedy best-match alignment.

    Args:
        openai_client: The project's :class:`AzureOpenAIClient` wrapper.
        embedding_model: Embedding deployment override (defaults to settings).
    """

    def __init__(
        self,
        openai_client: AzureOpenAIClient,
        embedding_model: str | None = None,
    ) -> None:
        self._openai = openai_client
        self._embedding_model = embedding_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def align_transcripts(
        self,
        source: TranscriptionResult,
        candidate: TranscriptionResult,
        source_language: str,
        target_language: str,
    ) -> list[TranscriptAlignment]:
        """Align source transcript segments with candidate translation segments.

        1. Segment both transcripts into sentence-level chunks with timing.
        2. Compute embeddings for all segments via Azure OpenAI.
        3. Greedily align each source segment to its best-matching candidate segment.

        Args:
            source: Transcription of the source audio.
            candidate: Transcription of the candidate's audio.
            source_language: BCP-47 tag for the source language.
            target_language: BCP-47 tag for the target language.

        Returns:
            List of :class:`TranscriptAlignment` with one entry per source segment.

        Raises:
            TranscriptAlignerError: If embedding computation or alignment fails.
        """
        logger.info(
            "Aligning transcripts: source_lang=%s target_lang=%s "
            "source_len=%d candidate_len=%d",
            source_language,
            target_language,
            len(source.words),
            len(candidate.words),
        )

        # 1. Segment transcripts
        source_segments = self._segment_transcript(source)
        candidate_segments = self._segment_transcript(candidate)

        if not source_segments:
            logger.warning("No source segments found for alignment.")
            return []

        if not candidate_segments:
            logger.warning(
                "No candidate segments found; returning zero-score alignments."
            )
            return [
                TranscriptAlignment(
                    source_segment=seg["text"],
                    source_start_ms=seg["start_ms"],
                    source_end_ms=seg["end_ms"],
                    candidate_segment="",
                    candidate_start_ms=None,
                    candidate_end_ms=None,
                    alignment_score=0.0,
                )
                for seg in source_segments
            ]

        # 2. Compute embeddings
        source_texts = [seg["text"] for seg in source_segments]
        candidate_texts = [seg["text"] for seg in candidate_segments]

        try:
            source_embeddings = await self._compute_embeddings(source_texts)
            candidate_embeddings = await self._compute_embeddings(candidate_texts)
        except Exception as exc:
            raise TranscriptAlignerError(
                f"Failed to compute embeddings: {exc}"
            ) from exc

        # 3. Compute similarity matrix
        similarity_matrix = self._cosine_similarity_matrix(
            source_embeddings, candidate_embeddings
        )

        # 4. Greedy alignment
        alignments = self._greedy_align(
            source_segments, candidate_segments, similarity_matrix
        )

        logger.info(
            "Alignment complete: %d source segments → %d alignments",
            len(source_segments),
            len(alignments),
        )
        return alignments

    # ------------------------------------------------------------------
    # Segmentation
    # ------------------------------------------------------------------

    @staticmethod
    def _segment_transcript(
        transcript: TranscriptionResult,
    ) -> list[dict[str, Any]]:
        """Split a transcript into sentence-level segments with timing.

        Uses punctuation-based splitting on the full text, then maps each
        sentence back to its constituent :class:`WordTimestamp` entries to
        recover timing information.

        Returns:
            List of dicts with keys: ``text``, ``start_ms``, ``end_ms``.
        """
        if not transcript.words:
            # Fallback: treat the entire text as one segment
            if transcript.text.strip():
                return [
                    {
                        "text": transcript.text.strip(),
                        "start_ms": 0,
                        "end_ms": int(transcript.duration_seconds * 1000),
                    }
                ]
            return []

        # Split on sentence-ending punctuation (handles Arabic ۔ as well)
        sentences = re.split(r'(?<=[.!?؟۔])\s+', transcript.text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            # No punctuation — treat the entire text as one segment
            return [
                {
                    "text": transcript.text.strip(),
                    "start_ms": transcript.words[0].start_ms,
                    "end_ms": transcript.words[-1].end_ms,
                }
            ]

        # Map words to sentences sequentially
        segments: list[dict[str, Any]] = []
        word_idx = 0
        words = transcript.words

        for sentence in sentences:
            sentence_words: list[WordTimestamp] = []
            sentence_lower = sentence.lower()

            # Find the run of words that belong to this sentence
            scan_idx = word_idx
            collected_text = ""
            while scan_idx < len(words):
                collected_text += (" " if collected_text else "") + words[scan_idx].word
                sentence_words.append(words[scan_idx])
                scan_idx += 1
                # Check if we've consumed enough words to cover this sentence
                if len(collected_text) >= len(sentence_lower) * 0.7:
                    break

            if sentence_words:
                segments.append(
                    {
                        "text": sentence,
                        "start_ms": sentence_words[0].start_ms,
                        "end_ms": sentence_words[-1].end_ms,
                    }
                )
                word_idx = scan_idx

        # Assign any remaining words to the last segment
        if word_idx < len(words) and segments:
            segments[-1]["end_ms"] = words[-1].end_ms

        return segments

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def _compute_embeddings(self, texts: list[str]) -> np.ndarray:
        """Compute embeddings for a list of texts via Azure OpenAI.

        Args:
            texts: Strings to embed.

        Returns:
            A 2-D numpy array of shape ``(len(texts), embedding_dim)``.
        """
        if not texts:
            return np.empty((0, 0), dtype=np.float64)

        embeddings = await self._openai.embeddings(texts, deployment=self._embedding_model)
        return np.array(embeddings, dtype=np.float64)

    # ------------------------------------------------------------------
    # Similarity & alignment
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Compute the cosine similarity matrix between two embedding arrays.

        Args:
            a: Shape ``(m, d)`` — source embeddings.
            b: Shape ``(n, d)`` — candidate embeddings.

        Returns:
            Shape ``(m, n)`` cosine similarity matrix.
        """
        if a.size == 0 or b.size == 0:
            return np.empty((a.shape[0], b.shape[0]), dtype=np.float64)

        # Normalize rows
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
        b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
        return a_norm @ b_norm.T

    @staticmethod
    def _greedy_align(
        source_segments: list[dict[str, Any]],
        candidate_segments: list[dict[str, Any]],
        similarity_matrix: np.ndarray,
    ) -> list[TranscriptAlignment]:
        """Greedily align each source segment to its highest-scoring candidate.

        Each candidate segment may be used by at most one source segment
        (consumed in order of descending similarity). If no candidate remains,
        a zero-score alignment is produced.

        Args:
            source_segments: Source segments with ``text``, ``start_ms``, ``end_ms``.
            candidate_segments: Candidate segments with the same keys.
            similarity_matrix: Shape ``(num_source, num_candidate)`` similarities.

        Returns:
            A list of :class:`TranscriptAlignment`, one per source segment.
        """
        n_src = len(source_segments)
        n_cand = len(candidate_segments)
        used_candidates: set[int] = set()
        alignments: list[TranscriptAlignment] = []

        # Build a list of (score, src_idx, cand_idx) sorted descending
        pairs: list[tuple[float, int, int]] = []
        for i in range(n_src):
            for j in range(n_cand):
                pairs.append((float(similarity_matrix[i, j]), i, j))
        pairs.sort(key=lambda x: x[0], reverse=True)

        # Track which source segments have been matched
        src_to_cand: dict[int, int] = {}

        for _score, src_idx, cand_idx in pairs:
            if src_idx in src_to_cand:
                continue
            if cand_idx in used_candidates:
                continue
            src_to_cand[src_idx] = cand_idx
            used_candidates.add(cand_idx)
            if len(src_to_cand) == n_src:
                break

        # Build alignment objects in source order
        for i in range(n_src):
            src = source_segments[i]
            if i in src_to_cand:
                j = src_to_cand[i]
                cand = candidate_segments[j]
                score = float(similarity_matrix[i, j])
                # Clamp to [0, 1]
                score = max(0.0, min(1.0, score))
                alignments.append(
                    TranscriptAlignment(
                        source_segment=src["text"],
                        source_start_ms=src["start_ms"],
                        source_end_ms=src["end_ms"],
                        candidate_segment=cand["text"],
                        candidate_start_ms=cand["start_ms"],
                        candidate_end_ms=cand["end_ms"],
                        alignment_score=round(score, 4),
                    )
                )
            else:
                alignments.append(
                    TranscriptAlignment(
                        source_segment=src["text"],
                        source_start_ms=src["start_ms"],
                        source_end_ms=src["end_ms"],
                        candidate_segment="",
                        candidate_start_ms=None,
                        candidate_end_ms=None,
                        alignment_score=0.0,
                    )
                )

        return alignments
