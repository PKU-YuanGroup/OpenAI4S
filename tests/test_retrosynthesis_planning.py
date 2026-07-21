"""Offline tests for the retrosynthesis_planning skill."""
import base64
import importlib.util
import json
import re
import sys

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader


def _llm_prompt(request):
    if isinstance(request, dict):
        return request["prompt"]
    return request


def _import_skill():
    sys.path.insert(0, str(get_config().skills_dir))
    from retrosynthesis_planning.kernel import (  # noqa: PLC0415
        OpenAI4SLLMReactionEvidenceProvider,
        annotate_routes_with_llm,
        build_aizynth_command,
        build_llm_annotation_prompt,
        build_markdown_report,
        build_molecule_structure_src,
        build_pubchem_query_url,
        build_pubchem_structure_image_url,
        collect_molecule_briefs,
        collect_reaction_briefs,
        collect_reaction_evidence,
        command_to_shell,
        normalize_routes,
        parse_llm_annotations,
        rank_routes,
        render_route_tree_html,
    )

    return {
        "annotate_routes_with_llm": annotate_routes_with_llm,
        "build_aizynth_command": build_aizynth_command,
        "build_llm_annotation_prompt": build_llm_annotation_prompt,
        "build_markdown_report": build_markdown_report,
        "build_molecule_structure_src": build_molecule_structure_src,
        "build_pubchem_query_url": build_pubchem_query_url,
        "build_pubchem_structure_image_url": build_pubchem_structure_image_url,
        "command_to_shell": command_to_shell,
        "collect_molecule_briefs": collect_molecule_briefs,
        "collect_reaction_evidence": collect_reaction_evidence,
        "collect_reaction_briefs": collect_reaction_briefs,
        "normalize_routes": normalize_routes,
        "OpenAI4SLLMReactionEvidenceProvider": OpenAI4SLLMReactionEvidenceProvider,
        "parse_llm_annotations": parse_llm_annotations,
        "rank_routes": rank_routes,
        "render_route_tree_html": render_route_tree_html,
    }


def _graph_payload(html):
    match = re.search(
        r'<script type="application/json" id="andor-data">(.*?)</script>', html
    )
    assert match
    return json.loads(match.group(1))["graph"]


def _svg_source_text(source):
    """Decode the self-contained SVG payload used by dashboard structure images."""
    prefix = "data:image/svg+xml;base64,"
    assert source.startswith(prefix)
    return base64.b64decode(source.removeprefix(prefix)).decode("utf-8")


def _assert_no_duplicate_structure_uris(html, *, check_runtime=True):
    """An `onerror` fallback is emitted only when it differs from its own primary.

    Without RDKit the primary *is* the placeholder, so carrying a fallback would
    embed the identical base64 payload twice on every molecule. Runtime checks
    apply only to HTML rendered in this test environment; committed examples may
    have been generated in a different optional-dependency environment.
    """
    tags = re.findall(r"<(?:img|image)\b[^>]*?data-fallback-src[^>]*?>", html)
    for tag in tags:
        primary = re.search(r'(?:^|\s)(?:src|href)="([^"]+)"', tag).group(1)
        fallback = re.search(r'data-fallback-src="([^"]+)"', tag).group(1)
        assert fallback != primary, "fallback duplicates the primary structure URI"
    if check_runtime and importlib.util.find_spec("rdkit") is None:
        assert not tags, "without RDKit no fallback should be emitted at all"


ROUTE_PAYLOAD = {
    "routes": [
        {
            "scores": {"state score": 0.64},
            "solved": True,
            "tree": {
                "type": "mol",
                "smiles": "CC(=O)Oc1ccccc1C(=O)O",
                "children": [
                    {
                        "type": "reaction",
                        "template": "ester hydrolysis/disconnection",
                        "metadata": {"classification": "0.0 Unrecognized"},
                        "children": [
                            {"type": "mol", "smiles": "CC(=O)O", "in_stock": True},
                            {
                                "type": "mol",
                                "smiles": "O=C(O)c1ccccc1O",
                                "in_stock": True,
                            },
                        ],
                    }
                ],
            },
        },
        {
            "score": 0.91,
            "solved": False,
            "tree": {
                "type": "mol",
                "smiles": "CC(=O)Oc1ccccc1C(=O)O",
                "children": [
                    {
                        "type": "reaction",
                        "children": [
                            {"type": "mol", "smiles": "CC(=O)Cl", "in_stock": True},
                            {
                                "type": "mol",
                                "smiles": "unknown-intermediate",
                                "in_stock": False,
                            },
                        ],
                    }
                ],
            },
        },
    ]
}

REALISTIC_AIZYNTH_ROUTE = [
    {
        "type": "mol",
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "in_stock": False,
        "children": [
            {
                "type": "reaction",
                "smiles": "[C:1]>>A.B",
                "is_reaction": True,
                "children": [
                    {"type": "mol", "smiles": "CC(=O)OC(C)=O", "in_stock": True},
                    {"type": "mol", "smiles": "O=C(O)c1ccccc1O", "in_stock": True},
                ],
            }
        ],
        "scores": {
            "state score": 0.9976287063411217,
            "number of reactions": 1,
            "number of pre-cursors": 2,
            "number of pre-cursors in stock": 2,
        },
        "metadata": {"created_at_iteration": 1, "is_solved": True},
    }
]


def test_retrosynthesis_skill_is_discovered():
    skills = SkillLoader().discover()
    assert "retrosynthesis_planning" in skills
    skill = skills["retrosynthesis_planning"]
    assert skill.has_kernel
    assert "retrosynthesis_planning.kernel" in (skill.import_hint or "")
    assert "retrosynthesis" in skill.description.lower()


def test_retrosynthesis_skill_is_searchable():
    hits = SkillLoader().search("retrosynthesis route planning purchasable precursor")
    assert any(hit["name"] == "retrosynthesis_planning" for hit in hits)


def test_aspirin_example_dashboard_is_documented():
    skill_root = get_config().skills_dir / "retrosynthesis_planning"
    skill_doc = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    example = skill_root / "examples" / "aspirin_retrosynthesis.html"
    html = example.read_text(encoding="utf-8")

    assert "examples/aspirin_retrosynthesis.html" in skill_doc
    assert "Interactive Retrosynthesis Knowledge Graph" in html
    assert "Example LLM route analysis" in html
    assert "phenolic O-acylation" in html
    assert "pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles" not in html

    # The example is regenerated from committed source data, not hand-edited.
    assert (skill_root / "examples" / "build_example.py").exists()
    assert (skill_root / "examples" / "aspirin_routes.json").exists()
    assert (skill_root / "examples" / "aspirin_annotations.json").exists()

    # Regression lock: the target must not render with the 'unknown' color.
    graph = _graph_payload(html)
    target = next(
        n for n in graph["nodes"] if n.get("smiles") == "CC(=O)Oc1ccccc1C(=O)O"
    )
    assert target["className"] == "target"
    molecule_sources = [
        node["structureSrc"]
        for node in graph["nodes"]
        if node.get("kind") == "molecule"
    ]
    assert molecule_sources
    assert all(
        "structure renderer fallback" not in _svg_source_text(source)
        for source in molecule_sources
    )
    _assert_no_duplicate_structure_uris(html, check_runtime=False)


def test_normalize_and_rank_routes():
    funcs = _import_skill()
    routes = funcs["normalize_routes"](ROUTE_PAYLOAD)
    ranked = funcs["rank_routes"](routes)

    assert len(ranked) == 2
    assert ranked[0]["solved"] is True
    assert ranked[0]["score"] == pytest.approx(0.64)
    assert ranked[0]["steps"] == 1
    assert ranked[0]["starting_materials"] == ["CC(=O)O", "O=C(O)c1ccccc1O"]


def test_normalize_realistic_aizynth_route_uses_metadata_solved():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](REALISTIC_AIZYNTH_ROUTE))

    assert ranked[0]["solved"] is True
    assert ranked[0]["score"] == pytest.approx(0.9976287063411217)
    assert ranked[0]["steps"] == 1
    assert ranked[0]["starting_materials"] == ["CC(=O)OC(C)=O", "O=C(O)c1ccccc1O"]


def test_render_html_and_report():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))

    html = funcs["render_route_tree_html"](
        ranked, target_smiles="CC(=O)Oc1ccccc1C(=O)O"
    )
    assert "<!doctype html>" in html
    assert "Route Ranking" in html
    assert "Interactive Retrosynthesis Knowledge Graph" in html
    assert 'id="andor-data"' in html
    assert '"graph"' in html
    assert '"mol:CC(=O)Oc1ccccc1C(=O)O"' in html
    assert 'id="andor-svg"' in html
    assert "Not predicted in this AiZynthFinder route export" in html
    assert "Molecule Briefs" in html
    assert '<svg class="route-svg"' in html
    assert "<image href=" in html
    assert "mol-structure" in html
    assert 'class="node stock"' in html
    assert 'class="node reaction"' in html
    assert "Visual style follows the bundled figure-style checklist" in html
    assert "O=C(O)c1ccccc1O" in html
    assert "Route 1" in html
    assert "LLM annotation required" not in html
    assert "LLM annotation not generated yet" not in html
    assert "Template-derived acyl substitution" in html
    assert "background: transparent" in html
    assert "--target: rgba(48, 117, 191, 0.09)" in html
    assert "structure-frame" in html
    assert "structure-well" in html
    assert "data:image/svg+xml;base64" in html
    _assert_no_duplicate_structure_uris(html)
    assert "pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles" not in html
    match = re.search(
        r'<script type="application/json" id="andor-data">(.*?)</script>', html
    )
    assert match
    graph = json.loads(match.group(1))["graph"]
    assert (
        sum(node["id"] == "mol:CC(=O)Oc1ccccc1C(=O)O" for node in graph["nodes"]) == 1
    )
    assert all("depth" in node for node in graph["nodes"])

    report = funcs["build_markdown_report"](
        ranked, target_smiles="CC(=O)Oc1ccccc1C(=O)O"
    )
    assert "# Retrosynthesis Planning Report" in report
    assert "Routes reaching stock" in report
    assert "Retrosynthetic rationale" in report
    assert "Suggested query" in report
    assert "`CC(=O)O`" in report

    prompt = funcs["build_llm_annotation_prompt"](
        ranked, target_smiles="CC(=O)Oc1ccccc1C(=O)O"
    )
    assert "Return strict JSON" in prompt
    assert "0.0 Unrecognized" in prompt
    assert "expected_yield_range" in prompt
    assert "route_strategy" in prompt
    assert "chemist_verdict" in prompt
    assert "suggested_conditions" in prompt
    assert "selectivity_risks" in prompt


def test_molecule_briefs_and_query_urls():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](REALISTIC_AIZYNTH_ROUTE))
    briefs = funcs["collect_molecule_briefs"](
        ranked, target_smiles="CC(=O)Oc1ccccc1C(=O)O"
    )

    by_smiles = {brief["smiles"]: brief for brief in briefs}
    target = by_smiles["CC(=O)Oc1ccccc1C(=O)O"]
    precursor = by_smiles["CC(=O)OC(C)=O"]

    assert target["role"] == "target"
    assert precursor["role"] == "stock precursor"
    assert precursor["stock_status"] == "in stock"
    assert "PubChem" not in precursor["pubchem_url"]
    assert "pubchem.ncbi.nlm.nih.gov" in precursor["pubchem_url"]
    assert funcs["build_pubchem_query_url"]("CC O").endswith("CC%20O")
    assert "PNG?image_size=260x180" in funcs["build_pubchem_structure_image_url"](
        "CC O"
    )
    assert funcs["build_molecule_structure_src"]("CC O").startswith(
        "data:image/svg+xml;base64,"
    )
    assert funcs["build_molecule_structure_src"]("unknown-intermediate").startswith(
        "data:image/svg+xml;base64,"
    )


def test_rdkit_structure_depiction_when_available():
    if importlib.util.find_spec("rdkit") is None:
        pytest.skip("RDKit is an optional chemistry dependency")

    funcs = _import_skill()
    svg = _svg_source_text(
        funcs["build_molecule_structure_src"]("CC(=O)Oc1ccccc1C(=O)O")
    )
    assert "structure renderer fallback" not in svg
    assert "#FFFFFF" not in svg.upper()


def test_route_step_evidence_cards_are_source_backed_and_graph_visible():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    briefs = funcs["collect_reaction_briefs"](ranked)

    assert len(briefs) == 2
    assert briefs[0]["reaction_key"].startswith("rxn:")

    evidence = {
        "reactions": {
            briefs[0]["reaction_key"]: [
                {
                    "source_type": "literature",
                    "title": "Verified O-acylation precedent",
                    "identifier": "DOI:10.0000/example",
                    "url": "https://example.org/precedent",
                    "match_level": "exact_substrate",
                    "verified": True,
                    "conditions": {"solvent": "ethyl acetate", "temperature": "20 C"},
                    "yield_range": "82-88%",
                    "risk_flags": ["controlled quench required"],
                    "notes": "Evidence record supplied by the reviewer.",
                }
            ]
        }
    }
    html = funcs["render_route_tree_html"](
        ranked,
        target_smiles="CC(=O)Oc1ccccc1C(=O)O",
        reaction_evidence=evidence,
    )

    assert "Step Evidence" in html
    assert "Verified exact-substrate evidence" in html
    assert "88/100 coverage" in html
    assert "Verified O-acylation precedent" in html
    assert 'href="https://example.org/precedent"' in html
    assert "No external evidence attached for this step." in html

    graph = _graph_payload(html)
    reaction_nodes = [node for node in graph["nodes"] if node.get("kind") == "reaction"]
    matched = next(
        node
        for node in reaction_nodes
        if node["details"].get("Evidence status") == "Verified exact-substrate evidence"
    )
    assert matched["details"]["Evidence coverage"] == "88/100 heuristic coverage"
    assert matched["details"]["Supporting evidence"][0]["Identifier"] == (
        "DOI:10.0000/example"
    )


def test_openai4s_llm_evidence_provider_only_links_retrieved_sources():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    calls = {"searches": []}

    def fake_llm(request):
        prompt = _llm_prompt(request)
        if "planning source retrieval" in prompt:
            briefs = json.loads(prompt.split("Reaction briefs:\n", 1)[1])
            return json.dumps(
                {
                    "queries": [
                        {
                            "reaction_key": briefs[0]["reaction_key"],
                            "query": "aspirin O-acylation synthesis precedent",
                        }
                    ]
                }
            )
        if "screening retrieved sources" in prompt:
            sources = json.loads(prompt.split("Retrieved sources:\n", 1)[1])
            source = sources[0]
            return json.dumps(
                {
                    "candidates": [
                        {
                            "reaction_key": source["reaction_key"],
                            "source_id": source["source_id"],
                            "match_level": "exact_substrate",
                            "rationale": "Retrieved title and snippet indicate a close precedent.",
                        },
                        {
                            "reaction_key": source["reaction_key"],
                            "source_id": "invented-source-id",
                            "match_level": "exact_substrate",
                        },
                    ]
                }
            )
        raise AssertionError("unexpected LLM prompt")

    def fake_search(query, **kwargs):
        calls["searches"].append((query, kwargs))
        return {
            "results": [
                {
                    "title": "Retrieved O-acylation precedent",
                    "url": "https://doi.org/10.5555/retrosynthesis",
                    "snippet": "Source snippet returned by the search skill.",
                }
            ]
        }

    provider = funcs["OpenAI4SLLMReactionEvidenceProvider"](
        llm=fake_llm,
        search=fake_search,
        doi_verifier=lambda dois: {doi: {"ok": True} for doi in dois},
        max_queries_per_reaction=1,
    )
    evidence = funcs["collect_reaction_evidence"](ranked, [provider])

    assert calls["searches"][0][0] == "aspirin O-acylation synthesis precedent"
    assert len(evidence) == 1
    record = next(iter(evidence.values()))[0]
    assert record["title"] == "Retrieved O-acylation precedent"
    assert record["url"] == "https://doi.org/10.5555/retrosynthesis"
    assert record["identifier"] == "10.5555/retrosynthesis"
    assert record["candidate"] is True
    assert record["verified"] is False
    assert record["identifier_verified"] is True

    html = funcs["render_route_tree_html"](ranked, reaction_evidence=evidence)
    assert "Retrieved source candidates need review" in html
    assert "30/100 coverage" in html
    assert "Retrieved O-acylation precedent" in html
    assert "invented-source-id" not in html


def test_execution_scoring_keeps_candidate_evidence_and_constraints_auditable():
    funcs = _import_skill()
    routes = funcs["normalize_routes"](ROUTE_PAYLOAD)
    first_reaction = funcs["collect_reaction_briefs"](routes)[0]
    evidence = {
        first_reaction["reaction_key"]: [
            {
                "source_type": "literature",
                "title": "Retrieved candidate only",
                "url": "https://doi.org/10.5555/candidate",
                "match_level": "exact_substrate",
                "candidate": True,
            }
        ]
    }
    ranked = funcs["rank_routes"](
        routes,
        reaction_evidence=evidence,
        constraints={
            "require_solved": True,
            "minimum_evidence_coverage": 40,
            "forbidden_starting_materials": ["CC(=O)O"],
        },
        decision_weights={"evidence_coverage": 40},
    )

    first = next(route for route in ranked if "CC(=O)O" in route["starting_materials"])
    second = next(route for route in ranked if not route["solved"])
    assert first["decision_breakdown"]["evidence_coverage"]["value"] == 30
    assert "forbidden starting material: CC(=O)O" in first["constraint_violations"]
    assert "route is not solved" in second["constraint_violations"]
    assert any("evidence coverage" in item for item in second["constraint_violations"])
    assert sum(item["weight"] for item in first["decision_breakdown"].values()) == 100


def test_llm_annotations_drive_reaction_and_molecule_html():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))

    def fake_llm(prompt):
        payload = json.loads(_llm_prompt(prompt).split("Route data:\n", 1)[1])
        return json.dumps(
            {
                "routes": {
                    str(route["rank"]): {
                        "route_strategy": "LLM proposes a concise aspirin disconnection strategy centered on ester construction.",
                        "key_disconnections": [
                            "Break the aryl ester into salicylate and acetyl donor"
                        ],
                        "reaction_sequence": [
                            "Prepare or buy salicylic acid precursor",
                            "Acylate the phenolic oxygen under controlled basic conditions",
                        ],
                        "conditions_strategy": "Screen acetyl donor, weak base, temperature, and workup to control acid/base compatibility.",
                        "yield_outlook": "Moderate to high planning yield is plausible but must be benchmarked against literature.",
                        "route_risks": [
                            "Hydrolysis during workup",
                            "Competing carboxylate activation",
                        ],
                        "recommended_next_steps": [
                            "Run LCMS condition screen",
                            "Search exact salicylate O-acylation precedent",
                        ],
                        "chemist_verdict": "optimize: attractive short route with condition evidence still required.",
                    }
                    for route in payload["routes"]
                },
                "molecules": {
                    molecule["smiles"]: {
                        "description": f"{molecule['role']} molecule in the planned route."
                    }
                    for molecule in payload["molecules"]
                },
                "reactions": {
                    reaction["reaction_key"]: {
                        "reaction_type": "Acylation / ester formation",
                        "description": "Retrosynthetic cleavage of the aryl ester to a salicylate-like phenol and an acetyl donor.",
                        "mechanistic_rationale": "Forward direction is a nucleophilic acyl substitution at an activated acetyl electrophile.",
                        "bond_changes": [
                            "Form aryl O-C(O)Me ester bond",
                            "Consume phenolic O-H nucleophile",
                        ],
                        "suggested_conditions": {
                            "reagents": "acetyl chloride or acetic anhydride",
                            "solvent": "dichloromethane or ethyl acetate",
                            "base_or_catalyst": "triethylamine or pyridine",
                            "temperature": "0-25 C",
                        },
                        "expected_yield_range": "50-85% as a planning estimate",
                        "yield_rationale": "Simple phenolic acylations are often productive, but substrate electronics and purification can lower yield.",
                        "selectivity_risks": [
                            "Competing carboxylic acid activation",
                            "Over-acylation if other nucleophiles are present",
                        ],
                        "safety_notes": [
                            "Acid chloride quench is exothermic",
                            "Check corrosive reagent handling",
                        ],
                        "validation_plan": [
                            "Search Reaxys/SciFinder for salicylate O-acylation precedent"
                        ],
                        "confidence": "medium",
                    }
                    for reaction in payload["reactions"]
                },
            }
        )

    annotations = funcs["annotate_routes_with_llm"](
        ranked, fake_llm, target_smiles="CC(=O)Oc1ccccc1C(=O)O"
    )
    assert annotations["reactions"]

    html = funcs["render_route_tree_html"](
        ranked,
        target_smiles="CC(=O)Oc1ccccc1C(=O)O",
        llm=fake_llm,
    )

    assert "Acylation / ester formation" in html
    assert "LLM Route Analysis" in html
    assert "LLM proposes a concise aspirin disconnection strategy" in html
    assert "Run LCMS condition screen" in html
    assert "Chemist verdict" in html
    assert "Retrosynthetic cleavage of the aryl ester" in html
    assert "Expected yield" in html
    assert "50-85% as a planning estimate" in html
    assert "Suggested conditions" in html
    assert "nucleophilic acyl substitution" in html
    assert "Over-acylation" in html
    assert "detail-kv" in html
    assert "target molecule in the planned route" in html
    assert "0.0 Unrecognized" not in html
    assert "LLM annotation required" not in html
    assert "LLM annotation not generated yet" not in html


def test_complex_multistep_routes_keep_structures_visible():
    funcs = _import_skill()
    target = "CC1=NC(=NO1)c2ccc(NC(=O)c3ccc(Cl)cc3)cc2"
    shared_intermediate = "O=C(Nc1ccc(C)cc1)c1ccc(Cl)cc1"
    complex_payload = {
        "routes": [
            {
                "score": 0.84,
                "solved": True,
                "tree": {
                    "type": "mol",
                    "smiles": target,
                    "children": [
                        {
                            "type": "reaction",
                            "metadata": {
                                "classification": "0.0 Unrecognized",
                                "policy_probability": 0.62,
                                "template": "[C:1](=[O:2])[N:3]>>[C:1](=[O:2])Cl.[N:3]",
                            },
                            "children": [
                                {
                                    "type": "mol",
                                    "smiles": shared_intermediate,
                                    "children": [
                                        {
                                            "type": "reaction",
                                            "template": "amide coupling",
                                            "children": [
                                                {
                                                    "type": "mol",
                                                    "smiles": "O=C(Cl)c1ccc(Cl)cc1",
                                                    "in_stock": True,
                                                },
                                                {
                                                    "type": "mol",
                                                    "smiles": "Cc1ccc(N)cc1",
                                                    "in_stock": True,
                                                },
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "type": "mol",
                                    "smiles": "late-stage-intermediate",
                                    "in_stock": False,
                                },
                            ],
                        }
                    ],
                },
            },
            {
                "score": 0.78,
                "solved": True,
                "tree": {
                    "type": "mol",
                    "smiles": target,
                    "children": [
                        {
                            "type": "reaction",
                            "template": "heteroaryl installation",
                            "children": [
                                {"type": "mol", "smiles": shared_intermediate},
                                {
                                    "type": "mol",
                                    "smiles": "CC1=NC(=NO1)B(O)O",
                                    "in_stock": True,
                                },
                            ],
                        }
                    ],
                },
            },
        ]
    }
    ranked = funcs["rank_routes"](funcs["normalize_routes"](complex_payload))

    def fake_llm(prompt):
        payload = json.loads(_llm_prompt(prompt).split("Route data:\n", 1)[1])
        return json.dumps(
            {
                "routes": {
                    str(route["rank"]): {
                        "route_strategy": "LLM reads the route as a convergent medicinal-chemistry disconnection with a late-stage bond-forming event.",
                        "key_disconnections": [
                            "Disconnect amide or heteroaryl linkage into validated partners"
                        ],
                        "reaction_sequence": [
                            "Assemble the aryl amide core",
                            "Install the heteroaryl fragment under screened coupling conditions",
                        ],
                        "conditions_strategy": "Use a small DOE around base, solvent, temperature, and catalyst/activating agent.",
                        "yield_outlook": "30-70% hypothetical route-step range; route yield will depend on the difficult late-stage step.",
                        "route_risks": [
                            "Poor solubility",
                            "Metal coordination by heteroatoms",
                            "Purification burden",
                        ],
                        "recommended_next_steps": [
                            "Run small-scale condition screen",
                            "Confirm exact-match literature or internal precedent",
                        ],
                        "chemist_verdict": "optimize: promising but evidence-limited route.",
                    }
                    for route in payload["routes"]
                },
                "molecules": {
                    molecule["smiles"]: {
                        "description": f"{molecule['role']} molecule with route-specific feasibility notes."
                    }
                    for molecule in payload["molecules"]
                },
                "reactions": {
                    reaction["reaction_key"]: {
                        "reaction_type": "Chemoselective bond-forming disconnection",
                        "description": "LLM assigns a conservative named disconnection based on the reaction template.",
                        "mechanistic_rationale": "The route is interpreted as a late-stage bond construction requiring chemoselectivity control.",
                        "bond_changes": [
                            "Forge the key C-N or C-C linkage indicated by the template"
                        ],
                        "suggested_conditions": {
                            "reagents": "coupling partner, base, and activating agent chosen by literature precedent",
                            "solvent": "polar aprotic solvent screen",
                            "temperature": "ambient to 80 C screen",
                        },
                        "expected_yield_range": "30-70% hypothetical range",
                        "yield_rationale": "Complex heteroaryl substrates often require optimization and may suffer from solubility or selectivity limits.",
                        "selectivity_risks": [
                            "Heteroaryl coordination",
                            "Competing N-acylation or protodeboronation",
                        ],
                        "safety_notes": [
                            "Assess exotherm and metal/base compatibility before scale-up"
                        ],
                        "validation_plan": [
                            "Search exact substructure precedent",
                            "Run small-scale condition screen",
                        ],
                        "confidence": "medium",
                    }
                    for reaction in payload["reactions"]
                },
            }
        )

    html = funcs["render_route_tree_html"](
        ranked,
        target_smiles=target,
        max_routes=5,
        llm=fake_llm,
    )
    graph_json = re.search(
        r'<script type="application/json" id="andor-data">(.*?)</script>', html
    ).group(1)
    graph = json.loads(graph_json)["graph"]

    assert len(graph["nodes"]) >= 9
    assert sum(node["id"] == f"mol:{target}" for node in graph["nodes"]) == 1
    assert (
        sum(node["id"] == f"mol:{shared_intermediate}" for node in graph["nodes"]) == 1
    )
    assert "Chemoselective bond-forming disconnection" in html
    assert "late-stage-intermediate" in html
    assert "data:image/svg+xml;base64" in html
    _assert_no_duplicate_structure_uris(html)
    assert "0.0 Unrecognized" not in html


def test_build_aizynth_command_is_shell_safe():
    funcs = _import_skill()
    command = funcs["build_aizynth_command"](
        "CC(=O)Oc1ccccc1C(=O)O",
        config_path="~/Documents/Openai4S/retro_data/config.yml",
        output_path="aspirin routes.json",
        conda_env="retro",
    )
    shell = funcs["command_to_shell"](command)

    assert command[:4] == ["conda", "run", "-n", "retro"]
    assert "aizynthcli" in command
    assert "--config" in command
    assert "'aspirin routes.json'" in shell or '"aspirin routes.json"' in shell


# --- LLM response robustness -------------------------------------------------
# host.llm has no JSON mode, so a conversation model routinely wraps its JSON in
# prose or a mid-string fence. The dashboard must survive every one of these.

CHATTY_JSON = '{"routes": {"1": {"route_strategy": "Recovered strategy."}}}'


@pytest.mark.parametrize(
    "reply",
    [
        CHATTY_JSON,
        f"```json\n{CHATTY_JSON}\n```",
        f"Sure! Here is the analysis:\n```json\n{CHATTY_JSON}\n```\nLet me know.",
        f"Here you go: {CHATTY_JSON} — hope that helps!",
    ],
)
def test_llm_annotations_survive_prose_wrapped_json(reply):
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))

    annotations = funcs["annotate_routes_with_llm"](ranked, lambda _: reply)
    assert annotations["routes"]["1"]["route_strategy"] == "Recovered strategy."

    html = funcs["render_route_tree_html"](ranked, llm=lambda _: reply)
    assert "Recovered strategy." in html
    assert "LLM Route Analysis" in html


@pytest.mark.parametrize("reply", ["I cannot help with that.", ""])
def test_llm_annotations_warn_and_degrade_when_unparseable(reply):
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))

    with pytest.warns(RuntimeWarning, match="not valid JSON"):
        assert funcs["annotate_routes_with_llm"](ranked, lambda _: reply) == {}

    with pytest.warns(RuntimeWarning, match="not valid JSON"):
        html = funcs["render_route_tree_html"](ranked, llm=lambda _: reply)
    # The renderer falls back to its own readout instead of losing the dashboard.
    assert "Route Planning Readout" in html
    assert "Route 1" in html


@pytest.mark.parametrize("reply", ["null", "[1, 2]", '"a string"'])
def test_llm_annotations_ignore_non_object_json(reply):
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))

    assert funcs["annotate_routes_with_llm"](ranked, lambda _: reply) == {}
    html = funcs["render_route_tree_html"](ranked, llm=lambda _: reply)
    assert "Route Planning Readout" in html


def test_parse_llm_annotations_raises_when_no_json_present():
    funcs = _import_skill()
    with pytest.raises(ValueError):
        funcs["parse_llm_annotations"]("there is no object here")


# --- target identification ----------------------------------------------------


def test_target_molecule_is_styled_as_target_in_knowledge_graph():
    funcs = _import_skill()
    target = "CC(=O)Oc1ccccc1C(=O)O"
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    graph = _graph_payload(
        funcs["render_route_tree_html"](ranked, target_smiles=target)
    )

    node = next(n for n in graph["nodes"] if n.get("smiles") == target)
    assert node["meta"] == "target"
    assert (
        node["className"] == "target"
    ), "target must use the target color, not 'unknown'"

    # ...and the per-route SVG must agree with the knowledge graph.
    html = funcs["render_route_tree_html"](ranked, target_smiles=target)
    assert 'class="node target"' in html


def test_target_is_identified_without_an_explicit_target_smiles():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    graph = _graph_payload(funcs["render_route_tree_html"](ranked))

    roots = [
        n for n in graph["nodes"] if n["kind"] == "molecule" and n["meta"] == "target"
    ]
    assert len(roots) == 1


def test_target_matches_a_non_canonical_smiles():
    pytest.importorskip("rdkit")
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    # Same molecule as the tree root, written differently.
    graph = _graph_payload(
        funcs["render_route_tree_html"](ranked, target_smiles="OC(=O)c1ccccc1OC(C)=O")
    )
    node = next(
        n for n in graph["nodes"] if n["kind"] == "molecule" and n["meta"] == "target"
    )
    assert node["className"] == "target"


# --- payload hygiene ----------------------------------------------------------


def test_graph_payload_escapes_html_delimiters():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    hostile = {"routes": {"1": {"route_strategy": "<!--<script>alert(1)</script>-->"}}}
    html = funcs["render_route_tree_html"](ranked, annotations=hostile)

    raw = re.search(
        r'<script type="application/json" id="andor-data">(.*?)</script>', html
    ).group(1)
    assert "<" not in raw and ">" not in raw
    assert json.loads(raw)  # still valid JSON after escaping


def test_molecule_briefs_warn_when_truncated():
    funcs = _import_skill()
    ranked = funcs["rank_routes"](funcs["normalize_routes"](ROUTE_PAYLOAD))
    with pytest.warns(RuntimeWarning, match="truncated"):
        briefs = funcs["collect_molecule_briefs"](ranked, max_molecules=1)
    assert len(briefs) == 1
