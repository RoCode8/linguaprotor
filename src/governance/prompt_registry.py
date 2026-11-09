"""Prompt template registry for LinguaProctor.

Manages versioned YAML prompt templates with Jinja2 rendering.  Each module
stores its prompts in ``src/<module>/prompts/<version>_<template_name>.yaml``
and this registry handles loading, version listing, and variable interpolation.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment, TemplateSyntaxError, UndefinedError
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PromptTemplate(BaseModel):
    """Parsed prompt template loaded from a YAML file."""

    module: str
    name: str
    version: str
    system_prompt: str
    user_prompt_template: str
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RenderedPrompt(BaseModel):
    """A fully rendered prompt ready for LLM consumption."""

    system_prompt: str
    user_prompt: str
    template_version: str
    variables_used: dict[str, Any]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptRegistryError(Exception):
    """Base exception for prompt-registry operations."""


class TemplateNotFoundError(PromptRegistryError):
    """Raised when a requested prompt template YAML cannot be found."""


class TemplateRenderError(PromptRegistryError):
    """Raised when Jinja2 rendering fails."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# File-name pattern: <version>_<template_name>.yaml
_VERSION_RE = re.compile(r"^(v\d+)_(.+)\.yaml$")


class PromptRegistry:
    """Central registry for discovering, loading, and rendering prompt templates.

    Parameters
    ----------
    prompts_base_dir:
        Override for the root ``src/`` directory that contains per-module
        ``prompts/`` sub-directories.  When *None*, defaults to the ``src/``
        directory co-located with this file.
    """

    def __init__(self, prompts_base_dir: str | None = None) -> None:
        if prompts_base_dir is not None:
            self._base = Path(prompts_base_dir)
        else:
            # Default: <project_root>/src
            self._base = Path(__file__).resolve().parent.parent

        self._jinja_env = Environment(loader=BaseLoader(), autoescape=False)
        logger.info("PromptRegistry initialised with base=%s", self._base)

    # -- internal helpers ----------------------------------------------------

    def _prompts_dir(self, module: str) -> Path:
        """Return the ``prompts/`` directory for a given module."""
        return self._base / module / "prompts"

    # -- public API ----------------------------------------------------------

    def load_template(
        self,
        module: str,
        template_name: str,
        version: str = "v1",
    ) -> PromptTemplate:
        """Load a YAML prompt template from disk.

        The expected file path is::

            <base>/<module>/prompts/<version>_<template_name>.yaml

        The YAML file must contain at minimum ``system_prompt`` and
        ``user_prompt_template`` keys.

        Parameters
        ----------
        module:
            Module name (e.g. ``"scoring"``, ``"content_generation"``).
        template_name:
            Logical template name (e.g. ``"rubric_evaluation"``).
        version:
            Version tag, e.g. ``"v1"``, ``"v2"``.

        Returns
        -------
        PromptTemplate
            The parsed template.

        Raises
        ------
        TemplateNotFoundError
            If the YAML file does not exist.
        PromptRegistryError
            If the YAML is malformed or missing required keys.
        """
        filename = f"{version}_{template_name}.yaml"
        filepath = self._prompts_dir(module) / filename

        if not filepath.exists():
            raise TemplateNotFoundError(
                f"Prompt template not found: {filepath}"
            )

        try:
            with open(filepath, encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise PromptRegistryError(
                f"Failed to parse YAML template {filepath}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise PromptRegistryError(
                f"Expected a YAML mapping in {filepath}, got {type(data).__name__}"
            )

        missing = {"system_prompt", "user_prompt_template"} - data.keys()
        if missing:
            raise PromptRegistryError(
                f"Template {filepath} is missing required keys: {missing}"
            )

        return PromptTemplate(
            module=module,
            name=template_name,
            version=version,
            system_prompt=data["system_prompt"],
            user_prompt_template=data["user_prompt_template"],
            output_schema=data.get("output_schema"),
            metadata=data.get("metadata", {}),
        )

    def render(self, template: PromptTemplate, **variables: Any) -> RenderedPrompt:
        """Render a :class:`PromptTemplate` with the supplied variables.

        Both the ``system_prompt`` and ``user_prompt_template`` are treated as
        Jinja2 templates.  Any ``{{ variable }}`` references are resolved
        from *variables*.

        Parameters
        ----------
        template:
            The prompt template to render.
        **variables:
            Keyword arguments substituted into the Jinja2 template.

        Returns
        -------
        RenderedPrompt

        Raises
        ------
        TemplateRenderError
            If Jinja2 rendering fails (e.g. missing variable).
        """
        try:
            sys_tmpl = self._jinja_env.from_string(template.system_prompt)
            usr_tmpl = self._jinja_env.from_string(template.user_prompt_template)
            rendered_system = sys_tmpl.render(**variables)
            rendered_user = usr_tmpl.render(**variables)
        except (TemplateSyntaxError, UndefinedError) as exc:
            raise TemplateRenderError(
                f"Failed to render template {template.module}/{template.name} "
                f"v{template.version}: {exc}"
            ) from exc

        return RenderedPrompt(
            system_prompt=rendered_system,
            user_prompt=rendered_user,
            template_version=template.version,
            variables_used=variables,
        )

    def list_versions(self, module: str, template_name: str) -> list[str]:
        """Return all available versions for a template.

        Parameters
        ----------
        module:
            Module name.
        template_name:
            Logical template name.

        Returns
        -------
        list[str]
            Sorted list of version strings, e.g. ``["v1", "v2"]``.
        """
        prompts_dir = self._prompts_dir(module)
        if not prompts_dir.exists():
            return []

        versions: list[str] = []
        for entry in prompts_dir.iterdir():
            match = _VERSION_RE.match(entry.name)
            if match and match.group(2) == template_name:
                versions.append(match.group(1))

        return sorted(versions)

    def get_active_version(self, module: str, template_name: str) -> str:
        """Return the highest (latest) version for a template.

        By convention the *active* version is the one with the highest
        numeric suffix.

        Parameters
        ----------
        module:
            Module name.
        template_name:
            Logical template name.

        Returns
        -------
        str
            The latest version string, e.g. ``"v2"``.

        Raises
        ------
        TemplateNotFoundError
            If no versions exist.
        """
        versions = self.list_versions(module, template_name)
        if not versions:
            raise TemplateNotFoundError(
                f"No versions found for template {module}/{template_name}"
            )
        return versions[-1]
