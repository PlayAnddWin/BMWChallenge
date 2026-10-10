import pytest

from services.retrieval import build_evidence_pack
from services.signals import extract_signals
from tests.intelligence.fakes import FixtureEvidence, MemorySignals, RecordingAudit, SCOPE, ScriptedModel, signal_response


@pytest.fixture
def evidence():
    return FixtureEvidence()


@pytest.fixture
def audit():
    return RecordingAudit()


@pytest.fixture
def repository():
    return MemorySignals()


@pytest.fixture
def signal(evidence, repository, audit):
    response = {"results": [signal_response(bucket) for bucket in evidence.buckets]}
    result = extract_signals(SCOPE, evidence, model=ScriptedModel(response), repository=repository, audit=audit)
    assert result.state == "succeeded"
    return result.items[0]


@pytest.fixture
def pack(signal, evidence):
    return build_evidence_pack(signal, SCOPE, evidence_provider=evidence)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="Explicitly enable real OpenAI/source integration")
    parser.addoption("--live-factory", default=None, help="Trusted module:function supplying Role 1/3 live adapters")
    parser.addoption("--live-openai-fixture", action="store_true", default=False,
                     help="Run real OpenAI calls on synthetic Role 2 evidence; no external backend required")
    parser.addoption("--live-no-retries", action="store_true", default=False,
                     help="Disable SDK retries during an explicitly enabled live diagnostic")
    parser.addoption("--live-openai-evaluation", action="store_true", default=False,
                     help="Run curated real-model robustness scenarios on synthetic evidence")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "rep_" + report.when, report)
