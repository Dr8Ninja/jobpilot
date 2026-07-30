"""The skills-to-learn report.

Its only job is to answer "what should I go and learn, and who wants it?", so
the tests are about ranking and noise: a skill the candidate already has, or a
sentence fragment the model emitted, must not end up on a study list.
"""

from jobpilot_shared.skill_gaps import aggregate_gaps


def test_ranks_by_how_many_jobs_ask_for_it() -> None:
    rows = [
        ("Kubernetes", "Acme", "Backend Engineer", 1),
        ("Kubernetes", "Beta", "Platform Engineer", 2),
        ("Kubernetes", "Gamma", "SRE", 3),
        ("Rust", "Acme", "Backend Engineer", 1),
    ]
    gaps = aggregate_gaps(rows)
    assert [g.skill for g in gaps] == ["Kubernetes", "Rust"]
    assert gaps[0].job_count == 3
    assert gaps[0].companies == ["Acme", "Beta", "Gamma"]


def test_records_which_company_asked() -> None:
    gaps = aggregate_gaps([("Terraform", "Acme", "Infra Engineer", 7)])
    assert gaps[0].companies == ["Acme"]
    assert gaps[0].examples == [{"company": "Acme", "title": "Infra Engineer", "job_id": 7}]


def test_skills_the_candidate_already_has_are_not_study_items() -> None:
    """Models routinely report a known skill as missing under different casing."""
    rows = [("python", "Acme", "Backend Engineer", 1), ("Go", "Acme", "Backend Engineer", 1)]
    gaps = aggregate_gaps(rows, known_skills=("Python", "Java"))
    assert [g.skill for g in gaps] == ["Go"]


def test_the_same_skill_twice_on_one_job_counts_once() -> None:
    rows = [
        ("Kafka", "Acme", "Backend Engineer", 1),
        ("kafka", "Acme", "Backend Engineer", 1),
    ]
    assert aggregate_gaps(rows)[0].job_count == 1


def test_generic_jd_filler_is_not_a_skill() -> None:
    rows = [
        ("communication skills", "Acme", "Backend Engineer", 1),
        ("5 years experience", "Acme", "Backend Engineer", 1),
        ("teamwork", "Beta", "Backend Engineer", 2),
        ("GraphQL", "Beta", "Backend Engineer", 2),
    ]
    assert [g.skill for g in aggregate_gaps(rows)] == ["GraphQL"]


def test_sentence_fragments_are_dropped() -> None:
    rows = [
        (
            "experience building distributed systems at very large scale",
            "Acme",
            "Backend Engineer",
            1,
        ),
        ("Apache Spark", "Acme", "Backend Engineer", 1),
    ]
    assert [g.skill for g in aggregate_gaps(rows)] == ["Apache Spark"]


def test_the_common_spelling_wins() -> None:
    rows = [
        ("kubernetes", "Acme", "A", 1),
        ("Kubernetes", "Beta", "B", 2),
        ("Kubernetes", "Gamma", "C", 3),
    ]
    assert aggregate_gaps(rows)[0].skill == "Kubernetes"


def test_min_jobs_hides_one_off_noise() -> None:
    rows = [
        ("Kubernetes", "Acme", "A", 1),
        ("Kubernetes", "Beta", "B", 2),
        ("COBOL", "Gamma", "C", 3),
    ]
    assert [g.skill for g in aggregate_gaps(rows, min_jobs=2)] == ["Kubernetes"]


def test_empty_input_is_an_empty_report() -> None:
    assert aggregate_gaps([]) == []


def test_aliases_of_one_technology_are_a_single_entry() -> None:
    """Found live: "Go 7 jobs" and "Golang 3 jobs" as separate rows buried both."""
    rows = [
        ("Golang", "Acme", "A", 1),
        ("Go", "Beta", "B", 2),
        ("go lang", "Gamma", "C", 3),
    ]
    gaps = aggregate_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0].skill == "Go"
    assert gaps[0].job_count == 3


def test_an_alias_of_a_known_skill_is_still_recognised_as_known() -> None:
    """Knowing PostgreSQL means "Postgres" is not something left to learn."""
    assert aggregate_gaps([("Postgres", "Acme", "A", 1)], known_skills=("PostgreSQL",)) == []
