from eval import redteam

def test_redteam_suite_has_100_prompts_and_all_defended():
    res = redteam.run()
    assert len(res) == 100 and all(r["ok"] for r in res), [r for r in res if not r["ok"]]
