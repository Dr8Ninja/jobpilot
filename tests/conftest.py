import pytest
from jobpilot_shared.canonical_facts import (
    CanonicalFacts,
    Education,
    Employment,
    Identity,
    Links,
)

# Deliberately synthetic. No real personal data lives in fixtures (non-negotiable #5).


@pytest.fixture
def facts() -> CanonicalFacts:
    return CanonicalFacts(
        identity=Identity(
            name="Test Candidate",
            email="test@example.invalid",
            phone="+91-00000-00000",
            location="Bengaluru, India",
        ),
        links=Links(
            linkedin="https://linkedin.com/in/example",
            github="https://github.com/example",
        ),
        experience_years=1.5,
        skills=(
            "Python",
            "JavaScript",
            "TypeScript",
            "React",
            "Node.js",
            "PostgreSQL",
            "Redis",
            "Docker",
            "FastAPI",
            "Git",
        ),
        employment=(
            Employment(
                company="Acme Corp",
                title="Software Engineer",
                start="2024-01",
                end="present",
                bullets=(
                    "Built REST endpoints for the billing service.",
                    "Cut p95 latency on the search path.",
                ),
            ),
            Employment(
                company="Beta Labs",
                title="Software Engineer Intern",
                start="2023-06",
                end="2023-12",
                bullets=("Wrote internal tooling in Python.",),
            ),
        ),
        education=(
            Education(
                degree="B.Tech Computer Science",
                institution="Example Institute of Technology",
                year="2023",
            ),
        ),
    )


# Re-export Postgres fixtures so `db` / `db_engine` resolve in any test module.
from db_fixtures import db, db_engine  # noqa: E402,F401
