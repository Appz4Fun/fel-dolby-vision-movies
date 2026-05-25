from pathlib import Path


def test_pages_workflow_configures_git_identity_before_rebase():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    prepare_start = workflow.index("- name: Prepare refresh branch")
    prepare_end = workflow.index("- name: Set up Python", prepare_start)
    prepare_step = workflow[prepare_start:prepare_end]

    assert prepare_step.index('git config user.name "github-actions[bot]"') < (
        prepare_step.index("git rebase origin/main")
    )
    assert prepare_step.index(
        'git config user.email "github-actions[bot]@users.noreply.github.com"'
    ) < prepare_step.index("git rebase origin/main")
