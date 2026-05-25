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


def test_pages_workflow_updates_existing_refresh_pr_when_data_changes():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")

    assert "- name: Check refresh branch changes" in workflow
    assert "id: refresh_changes" in workflow
    assert "has_tracked_changes=true" in workflow

    update_start = workflow.index("- name: Update refresh branch and PR")
    update_end = workflow.index("- name: No new FEL releases", update_start)
    update_step = workflow[update_start:update_end]

    assert "steps.refresh_changes.outputs.has_tracked_changes == 'true'" in update_step
    assert "steps.release_delta.outputs.pending_release_count != '0'" in update_step
    assert "steps.release_delta.outputs.has_new_releases == 'true'" not in update_step
