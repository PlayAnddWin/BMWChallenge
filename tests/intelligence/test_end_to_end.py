from tests.intelligence.demo import run_demo


def test_account_free_extraction_draft_and_pending_review_flow():
    result = run_demo()
    assert result["mode"].startswith("SYNTHETIC")
    assert (result["support"], result["counterexamples"], result["cohort"]) == (2, 1, 3)
    assert result["generation_state"] == "succeeded" and result["suggestion_state"] == "pending"
    assert result["target_review_state"] == "unreviewed" and result["requirement_unchanged_by_review"]
