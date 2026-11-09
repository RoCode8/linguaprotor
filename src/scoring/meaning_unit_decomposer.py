"""Meaning unit decomposer for translation evaluation.

Decomposes source passages into atomic semantic units for granular,
evidence-anchored assessment.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from jinja2 import BaseLoader, Environment

from src.infrastructure.azure_openai_client import AzureOpenAIClient
from src.scoring.schemas import MeaningUnit

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class MeaningUnitDecomposer:
    """Decomposes source text into verifiable atomic meaning units."""

    def __init__(self, openai_client: AzureOpenAIClient | None = None) -> None:
        self.openai_client = openai_client or AzureOpenAIClient()
        self._jinja_env = Environment(loader=BaseLoader(), autoescape=False)

    def _load_prompt_template(self, filename: str) -> dict[str, str]:
        path = _PROMPTS_DIR / filename
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    async def decompose(
        self, source_text: str, source_language: str = "en"
    ) -> list[MeaningUnit]:
        """Decompose text into atomic meaning units."""
        template_data = self._load_prompt_template("v1_meaning_units.yaml")

        system_tmpl = self._jinja_env.from_string(template_data["system"])
        user_tmpl = self._jinja_env.from_string(template_data["user"])

        system_msg = system_tmpl.render()
        user_msg = user_tmpl.render(
            source_language=source_language,
            source_text=source_text,
        )

        response = await self.openai_client.chat_completion_json(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            operation="written_scoring.decompose",
        )

        raw_units = response.get("meaning_units", []) if isinstance(response, dict) else []
        meaning_units: list[MeaningUnit] = []

        seen_ids: set[str] = set()
        for idx, u in enumerate(raw_units):
            if not isinstance(u, dict):
                continue
            # calculate or verify span
            text = str(u.get("source_text") or "").strip()
            if not text:
                continue
            span = u.get("source_span")
            if not span or len(span) != 2:
                # Find span dynamically
                start = source_text.find(text)
                span = (start, start + len(text)) if start != -1 else (0, len(text))
            else:
                span = (int(span[0]), int(span[1]))

            try:
                weight = float(u.get("importance_weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            unit_id = str(u.get("id") or f"mu_{idx + 1}")
            if unit_id in seen_ids:
                unit_id = f"{unit_id}_{idx + 1}"
            seen_ids.add(unit_id)

            meaning_units.append(
                MeaningUnit(
                    id=unit_id,
                    unit_type=str(u.get("unit_type") or "main_clause"),
                    source_text=text,
                    source_span=span,
                    # Methodology: weights live in [0.1, 1.0]
                    importance_weight=min(max(weight, 0.1), 1.0),
                )
            )

        return meaning_units
