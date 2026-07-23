"""The versioned science-workflow benchmark: manifests, steps, and a runner.

The proposal asks for ten representative workflows and at least twenty
versioned cases, and it is specific about what would make them worthless: a
directory of fixtures nobody executes, or cases that pass because the thing
they exercise is a mock.

So every step here calls the real subsystem — the real Store, the real kernel
manager, the real host dispatcher, the real compute manager, the real
connector service, the real environment transaction. What is injected is only
what cannot run offline: the LLM (mocked by the suite already), the network
(connector fetches are fed recorded bodies), and the package manager (an
environment build cannot download a solver in a unit test). Everything those
inject *into* is production code.

A case declares what it expects, and "expects" includes the outcomes that
matter as much as success: a failure, a cancellation, a recovery, a refused
permission, a provenance claim. A benchmark that only measures the happy path
measures the half of the system that was never in doubt.
"""
from openai4s.benchmark.model import Case, Workflow, load_workflows
from openai4s.benchmark.runner import CaseResult, run_all, run_case

__all__ = [
    "Case",
    "CaseResult",
    "Workflow",
    "load_workflows",
    "run_all",
    "run_case",
]
