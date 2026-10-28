from Scripts.industry_prompt_gray_report import render_markdown, summarize_records


def test_industry_gray_report_groups_prompt_and_model_with_rate_means():
    records = [
        {
            "prompt_version": 1,
            "model": "model-a",
            "valuable": True,
            "total_score": 6.0,
            "rate": {"行动实质性": 8, "投资可用性": 6},
        },
        {
            "prompt_version": 1,
            "model": "model-a",
            "valuable": False,
            "total_score": None,
            "rate": {},
        },
        {
            "prompt_version": 2,
            "model": "model-a",
            "valuable": True,
            "total_score": 5.0,
            "rate": {"行动实质性": 4, "投资可用性": 4},
        },
    ]

    rows = summarize_records(records)

    assert rows[0]["completed"] == 2
    assert rows[0]["valuable_rate"] == 0.5
    assert rows[0]["mean_total_score"] == 6.0
    assert rows[0]["rate_means"]["行动实质性"] == 8.0
    assert rows[1]["prompt_version"] == 2
    assert rows[1]["rate_means"]["投资可用性"] == 4.0
    assert "v2" in render_markdown(rows)
