"""Production Role 01 adapters for BACKEND_ADAPTER_FACTORY."""
from services.providers import Role01SourceProvider
from services.real_evidence import RealEvidenceResolver
from services.role01_import import Role01ImportService


def build_adapters(*, driver, repository):
    if driver is None:
        raise ValueError("Role 01 runtime requires a Neo4j driver")
    sources = Role01SourceProvider(driver, getattr(repository, "database", None))
    return {"source_provider": sources,
            "evidence_provider": RealEvidenceResolver(sources, sources.evidence),
            "import_service": Role01ImportService(driver, database=getattr(repository, "database", None))}
