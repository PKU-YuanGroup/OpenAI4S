"""Contracts for provider-neutral native control-tool declarations."""

from dataclasses import FrozenInstanceError

import pytest

from openai4s.tools import REGISTRY, Tool, ToolSpec, control_tool_specs
from openai4s.tools import native as native_mod


def test_specs_are_frozen_fresh_copies_of_the_existing_registry():
    specs = control_tool_specs()

    assert isinstance(specs, tuple)
    assert [spec.name for spec in specs] == [tool.name for tool in REGISTRY]
    assert all(isinstance(spec, ToolSpec) for spec in specs)
    assert all(spec.strict is False for spec in specs)

    by_name = {tool.name: tool for tool in REGISTRY}
    for spec in specs:
        source = by_name[spec.name]
        assert spec.description == source.description
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema["properties"] == source.parameters["properties"]
        assert spec.input_schema["required"] == source.parameters["required"]
        assert spec.input_schema is not source.parameters
        assert spec.input_schema["properties"] is not source.parameters["properties"]

    with pytest.raises(FrozenInstanceError):
        specs[0].name = "renamed"


def test_each_call_returns_schemas_independent_of_registry_and_other_calls():
    first = control_tool_specs()
    second = control_tool_specs()
    original = REGISTRY[0].parameters["properties"]["path"]["description"]

    first[0].input_schema["properties"]["path"]["description"] = "changed"

    assert second[0].input_schema["properties"]["path"]["description"] == original
    assert REGISTRY[0].parameters["properties"]["path"]["description"] == original


@pytest.mark.parametrize(
    "name",
    [
        "9_starts_with_a_digit",
        "contains.dot",
        "contains space",
        "naïve",
        "x" * 65,
    ],
)
def test_names_must_fit_the_four_provider_intersection(monkeypatch, name):
    invalid = Tool(
        name=name,
        host_method="list_dir",
        description="invalid portable name",
        parameters={"properties": {}, "required": []},
    )
    monkeypatch.setattr(native_mod, "REGISTRY", [invalid])

    with pytest.raises(ValueError, match="not portable across"):
        native_mod.control_tool_specs()


def test_native_catalogue_never_exposes_completion_or_shell(monkeypatch):
    schema = {"properties": {}, "required": []}
    monkeypatch.setattr(
        native_mod,
        "REGISTRY",
        [
            Tool("bash", "bash", "shell", schema),
            Tool("submit_output", "submit_output", "completion", schema),
            Tool("shell_alias", "bash", "aliased shell", schema),
            Tool("safe_control", "list_dir", "safe", schema),
        ],
    )

    specs = native_mod.control_tool_specs()

    assert [spec.name for spec in specs] == ["safe_control"]
    assert {spec.name for spec in control_tool_specs()}.isdisjoint(
        {"bash", "submit_output"}
    )
