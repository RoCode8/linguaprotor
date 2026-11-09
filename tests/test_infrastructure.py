"""Infrastructure contract tests.

Compiles the Bicep templates and asserts (1) the security baseline and (2) the
contract between infrastructure, azd and the application: every setting the
infra provides is one the code reads, every parameter a deployment needs is
supplied, and ports/targets/tags line up with azure.yaml and the Dockerfile.

Requires the Bicep CLI (``az bicep``); skipped otherwise.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import (
    AppSettings,
    AzureOpenAISettings,
    AzureSpeechSettings,
    BlobStorageSettings,
    CosmosSettings,
)

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"

pytestmark = pytest.mark.skipif(shutil.which("az") is None, reason="Azure CLI / Bicep not installed")


@cache
def compile_bicep(relative: str) -> dict[str, Any]:
    out = subprocess.run(
        ["az", "bicep", "build", "--file", str(INFRA / relative), "--stdout"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def walk_resources(template: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every resource, descending into nested module deployments."""
    resources = template.get("resources", [])
    items = resources.values() if isinstance(resources, dict) else resources
    for res in items:
        if res.get("existing"):
            continue
        if res.get("type") == "Microsoft.Resources/deployments":
            yield from walk_resources(res["properties"]["template"])
        else:
            yield res


def resources_of(template: dict[str, Any], rtype: str) -> list[dict[str, Any]]:
    return [r for r in walk_resources(template) if r["type"].lower() == rtype.lower()]


def settings_env_names() -> set[str]:
    names: set[str] = set()
    for model in (AzureOpenAISettings, AzureSpeechSettings, CosmosSettings, BlobStorageSettings, AppSettings):
        prefix = model.model_config.get("env_prefix", "")
        names |= {f"{prefix}{field}".upper() for field in model.model_fields}
    return names


def placeholders(path: Path) -> set[str]:
    """${VAR} / ${VAR=default} references in an azd parameters file (without defaults)."""
    refs = re.findall(r"\$\{([A-Z0-9_]+)(=[^}]*)?\}", path.read_text())
    return {name for name, default in refs if not default}


MAIN = "main.bicep"
APPS = {"api": "app/api.bicep", "dashboard": "app/dashboard.bicep"}


# --------------------------------------------------------------------------- security baseline


def test_templates_compile_without_lint_errors() -> None:
    for path in [MAIN, *APPS.values()]:
        compile_bicep(path)  # bicepconfig.json promotes lint findings to errors


def test_no_secrets_in_outputs_or_app_config() -> None:
    for path in [MAIN, *APPS.values()]:
        text = json.dumps(compile_bicep(path))
        assert "listKeys" not in json.dumps(compile_bicep(path).get("outputs", {})), path
        assert "listCredentials" not in text, path
    for app in APPS.values():
        for ca in resources_of(compile_bicep(app), "Microsoft.App/containerApps"):
            assert not ca["properties"]["configuration"].get("secrets"), "container apps must not hold secrets"


def test_local_auth_disabled_everywhere() -> None:
    main = compile_bicep(MAIN)
    accounts = resources_of(main, "Microsoft.CognitiveServices/accounts")
    assert {a["kind"] for a in accounts} == {"OpenAI", "SpeechServices"}
    for account in accounts:
        assert account["properties"]["disableLocalAuth"] is True
        assert account["properties"]["customSubDomainName"], "Entra ID auth needs a custom subdomain"
    (cosmos,) = resources_of(main, "Microsoft.DocumentDB/databaseAccounts")
    assert cosmos["properties"]["disableLocalAuth"] is True
    (acr,) = resources_of(main, "Microsoft.ContainerRegistry/registries")
    assert acr["properties"]["adminUserEnabled"] is False


def test_audit_role_is_append_only() -> None:
    (role,) = resources_of(compile_bicep(MAIN), "Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions")
    actions = role["properties"]["permissions"][0]["dataActions"]
    assert any(a.endswith("/items/create") for a in actions)
    forbidden = ("/items/replace", "/items/upsert", "/items/delete", "/items/*", "containers/*")
    assert not [a for a in actions if a.endswith(forbidden)]


def test_every_ai_and_data_resource_ships_diagnostics() -> None:
    main = compile_bicep(MAIN)
    diags = resources_of(main, "Microsoft.Insights/diagnosticSettings")
    assert len(diags) == 4  # OpenAI, Speech, Cosmos, Container Registry


def test_model_deployments_are_pinned_and_never_silently_upgraded() -> None:
    main = compile_bicep(MAIN)
    chat = main["parameters"]["chatModel"]["defaultValue"]
    embedding = main["parameters"]["embeddingModel"]["defaultValue"]
    assert chat["model"] == "gpt-5.1" and chat["version"] == "2025-11-13"
    assert embedding["model"] == "text-embedding-3-small"
    for dep in resources_of(main, "Microsoft.CognitiveServices/accounts/deployments"):
        assert dep["properties"]["versionUpgradeOption"] == "OnceCurrentVersionExpired"


# --------------------------------------------------------------------------- infra <-> app contract


def test_api_environment_variables_are_all_read_by_the_application() -> None:
    (api,) = resources_of(compile_bicep(APPS["api"]), "Microsoft.App/containerApps")
    env_names = {e["name"] for e in api["properties"]["template"]["containers"][0]["env"]}
    # Read by azure-identity / OpenTelemetry rather than src/config.py:
    sdk_vars = {"AZURE_CLIENT_ID", "APPLICATIONINSIGHTS_CONNECTION_STRING"}
    unknown = env_names - settings_env_names() - sdk_vars
    assert not unknown, f"env vars the app never reads: {unknown}"
    required = {"AZURE_OPENAI_ENDPOINT", "AZURE_SPEECH_ENDPOINT", "COSMOS_ENDPOINT", "AZURE_CLIENT_ID"}
    assert required <= env_names


def test_deployment_names_match_application_defaults() -> None:
    main = compile_bicep(MAIN)
    assert main["parameters"]["chatModel"]["defaultValue"]["name"] == AzureOpenAISettings.model_fields[
        "chat_deployment"].default
    assert main["parameters"]["embeddingModel"]["defaultValue"]["name"] == AzureOpenAISettings.model_fields[
        "embedding_deployment"].default
    assert CosmosSettings.model_fields["audit_container"].default == "audit"


def test_app_parameter_files_are_satisfied_by_provision_outputs() -> None:
    outputs = set(compile_bicep(MAIN)["outputs"])
    azd_builtins = {"AZURE_ENV_NAME", "AZURE_LOCATION", "SERVICE_API_IMAGE_NAME", "SERVICE_DASHBOARD_IMAGE_NAME"}
    for service, module in APPS.items():
        params_file = INFRA / module.replace(".bicep", ".parameters.json")
        missing = placeholders(params_file) - outputs - azd_builtins
        assert not missing, f"{service}: parameters not produced by provisioning: {missing}"

        declared = compile_bicep(module)["parameters"]
        supplied = set(json.loads(params_file.read_text())["parameters"])
        required = {n for n, p in declared.items() if "defaultValue" not in p}
        assert required <= supplied, f"{service}: unsupplied required params {required - supplied}"
        assert supplied <= set(declared), f"{service}: unknown params {supplied - set(declared)}"


def test_main_parameter_file_matches_template() -> None:
    declared = compile_bicep(MAIN)["parameters"]
    supplied = set(json.loads((INFRA / "main.parameters.json").read_text())["parameters"])
    assert supplied <= set(declared)
    assert {n for n, p in declared.items() if "defaultValue" not in p} <= supplied


def test_azure_yaml_services_match_modules_tags_ports_and_docker_targets() -> None:
    project = yaml.safe_load((ROOT / "azure.yaml").read_text())
    dockerfile = (ROOT / "Dockerfile").read_text()
    stages = set(re.findall(r"^FROM \S+ AS (\w+)$", dockerfile, re.M))
    exposed = {
        stage: int(port)
        for stage, port in re.findall(r"^FROM \S+ AS (\w+)\nEXPOSE (\d+)", dockerfile, re.M)
    }
    assert set(project["services"]) == set(APPS)
    for service, cfg in project["services"].items():
        assert f"{cfg['module']}.bicep" == APPS[service]
        assert cfg["docker"]["target"] in stages
        (app,) = resources_of(compile_bicep(APPS[service]), "Microsoft.App/containerApps")
        assert app["tags"]["azd-service-name"] == service
        port = app["properties"]["configuration"]["ingress"]["targetPort"]
        target_port = compile_bicep(APPS[service])["variables"]["port"] if isinstance(port, str) else port
        assert target_port == exposed[cfg["docker"]["target"]], service


def test_api_runs_as_single_replica_because_state_is_in_process() -> None:
    (api,) = resources_of(compile_bicep(APPS["api"]), "Microsoft.App/containerApps")
    scale = api["properties"]["template"]["scale"]
    assert scale["minReplicas"] == 1 and scale["maxReplicas"] == 1
