---
name: is-research-design
description: Design Information Systems (IS, 信息系统) research by connecting IT-specific mechanisms, constructs and rival explanations to behavioral, economic, organizational or design-science evaluation plans. Use before data collection or artifact evaluation; reuse existing skills for literature retrieval and estimation.
origin: openai4s
category: research-methodology
license: MIT
capabilities:
  network:
    mode: none
    domains: []
---

# Information Systems research design

Turn an IS question into a traceable claim–evidence plan. This recipe adapts
Bryce Wang's MISQ theory-development and methods skills; it is community
guidance, not a journal endorsement. The source pin and adaptation record are
in [UPSTREAM.json](UPSTREAM.json), with the original [MIT license](LICENSE).
`origin: openai4s` identifies bundled distribution, not original authorship.

## Establish the research claim

Identify the digital artifact/property, actors, task, setting, unit of analysis,
time ordering and intended claim. Distinguish a research question, a theoretical
prediction, an observed association and an identified causal effect. If results
were already inspected, label new explanations exploratory; do not rewrite
them as a priori hypotheses or claim preregistration without a dated record.

Choose the form of theory that serves the question:

| Tradition | Theory to articulate | Evidence the design must seek |
| --- | --- | --- |
| Behavioral | How a specific IT property changes cognition, behavior or outcomes; mediators, moderators and boundaries | Construct validity and a design that can distinguish the proposed mechanism from alternatives |
| Economics of IS | Information, incentives, matching or network effects yielding falsifiable predictions | A justified source of variation, comparison group and identifying assumptions |
| Organizational | Processes or relationships through which technology changes practice in context | Sampling logic, temporal/process evidence and a traceable path from records to interpretations |
| Design science | Generalizable design principles grounded in a problem and justificatory theory | Evaluation of each principle's utility, limitations and tradeoffs against credible alternatives |

A mixed-method study may combine rows, but name what each component establishes
and how discordant results will be handled. Do not force every study into a
causal or hypothesis-testing template.

## Define constructs and mechanisms before choosing a method

For each construct record its conceptual definition, level (person/team/firm/
platform), proposed observable indicators, source and measurement timing. Explain
why the observable represents the construct; a convenient log count is not
automatically engagement, trust, learning or performance. Identify aggregation
assumptions when observations and claims refer to different levels.

Write each mechanism as **IT property → actor response/process → outcome**,
then state a boundary condition and a rival explanation that could produce the
same observation. Record what evidence would distinguish them. A variable
label such as “AI adoption” does not explain which affordance or constraint
changes behavior. Statistical mediation alone does not establish a causal
channel; temporal ordering and confounding assumptions still need evidence.

Attach a source locator to theoretical premises and prior findings: DOI/URL
plus page/section, or an artifact version plus a relevant location. Keep an
unsupported premise visible as missing evidence. For literature search and
DOI checking, load the existing `literature-review` skill; this local planning
recipe neither searches externally nor verifies citations by itself.

## Match evidence to claim strength

- Experiments: define assignment unit, treatment contrast, allocation,
  primary outcome, timing, compliance, spillovers, attrition and exclusions.
  Plan power/precision and multiplicity handling before outcome inspection.
- Surveys: plan measurement validation, sampling and missingness handling;
  consider temporal/source separation to reduce common-method bias. A
  cross-sectional association does not identify a causal effect. Load
  `is-survey-measurement` for measurement/SEM work when available.
- Observational causal studies: name the independently documented intervention,
  assignment mechanism and identifying assumptions. For panels, reuse
  `panel-data-preprocessing`; for supported DiD designs, load `did-analysis`.
  An outcome jump found during exploration is not an exogenous shock. IV/RD
  require separate justified methods and available tooling; this skill does
  not provide those estimators.
- Organizational/qualitative studies: specify case selection, access and
  consent, data sources, researcher position and rival interpretations.
  Preserve contrary cases and source locators. Use `is-qualitative-analysis`
  for coding when available; software cannot establish saturation on its own.

Record open decisions without inventing inputs. Human recruitment, contacting
participants, data collection or external services need the applicable user
authorization; a design plan alone does not perform those actions.

## Design science: evaluate the principle, not just the implementation

Connect **problem → justificatory theory → design principle → implementation
choice → falsifiable utility proposition → evaluation**. State a principle so
another system could instantiate it. For each proposition predefine:

- target users/tasks/context, baseline and why it is credible;
- primary metric, measurement procedure, uncertainty and a meaningful decision
  criterion;
- ablation or alternative design that isolates the proposed principle;
- workload/data selection, held-out evaluation and reproducibility records;
- failure conditions, costs, harms and limits to generalization.

For example, a claim that inspectable provenance helps researchers detect
unsupported conclusions needs an evaluation of detection performance against
an appropriate baseline. A claim about calibrated trust needs operationalized
trust/reliance measures and a relevant participant study. Passing automated
tests establishes implementation behavior, not either human outcome. Label
these as proposed studies until evidence exists. For ML benchmarks, reuse
`plan-ml-experiment`, `audit-dataset` and `evaluate-model` as applicable.

## Save a design and claim–evidence record

Read [design-template.json](design-template.json) through `read_skill_file` or
`host.skills.read`, not a workspace file reader. The template is a planning
worksheet, not a runtime schema or an enforced completion gate. Replace nulls
only with supplied facts or explicitly labeled proposed choices. Copy entries
for additional constructs/claims; keep `evidence_status: "missing"` until real
evidence is linked. A design can be delivered with unresolved issues; report
them and the strongest claim currently supportable.

This runnable Python cell saves an explicitly unfilled draft through the
existing Host API. It fabricates no study, result, citation or participant:

```python
import json

design = json.loads(host.skills.read("is-research-design", "design-template.json"))
# Populate from the research brief and inspected evidence before advancing
# status beyond draft. The template is useful even when inputs are missing.
design["open_questions"] = [
    "Specify the research question, IT property, actors and context.",
    "Choose the claim type and record evidence needed to distinguish rivals.",
]
saved = host.write_file(
    "is-research-design.json",
    json.dumps(design, ensure_ascii=False, indent=2) + "\n",
)
print(json.dumps({"status": design["status"], "saved": saved}, ensure_ascii=False))
```

For a real handoff, deliver the populated design, its claim–evidence links,
alternative explanations, pending decisions and selected next skill. Preserve
the planning version and document later deviations. These methodological
checks guide the agent; they do not create an Engine completion policy.
