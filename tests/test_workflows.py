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


def test_pages_workflow_regenerates_releases_from_main_data():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    prepare_start = workflow.index("- name: Prepare refresh branch")
    prepare_end = workflow.index("- name: Set up Python", prepare_start)
    prepare_step = workflow[prepare_start:prepare_end]

    assert (
        'git show origin/main:data/releases.json > "$RUNNER_TEMP/base-releases.json"'
        in prepare_step
    )
    assert 'cp data/releases.json "$RUNNER_TEMP/previous-releases.json"' in prepare_step
    assert 'cp "$RUNNER_TEMP/base-releases.json" data/releases.json' in prepare_step
    assert "git checkout origin/main -- data/posters" in prepare_step
    assert prepare_step.index(
        'cp data/releases.json "$RUNNER_TEMP/previous-releases.json"'
    ) < prepare_step.index('cp "$RUNNER_TEMP/base-releases.json" data/releases.json')
    assert prepare_step.index(
        'cp "$RUNNER_TEMP/base-releases.json" data/releases.json'
    ) < (prepare_step.index("git checkout origin/main -- data/posters"))


def test_pages_workflow_refreshes_force_with_lease_before_push():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    update_start = workflow.index("- name: Update refresh branch and PR")
    update_end = workflow.index("- name: No new FEL releases", update_start)
    update_step = workflow[update_start:update_end]

    fetch_line = 'git fetch origin "$PR_BRANCH:refs/remotes/origin/$PR_BRANCH"'
    assert fetch_line in update_step
    assert update_step.index("git commit -m") < update_step.index(fetch_line)
    assert update_step.index(fetch_line) < update_step.index(
        "git push --force-with-lease"
    )


def test_pages_workflow_dispatches_ci_after_refresh_push():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    permissions_start = workflow.index("permissions:")
    permissions_end = workflow.index("concurrency:", permissions_start)
    permissions = workflow[permissions_start:permissions_end]
    update_start = workflow.index("- name: Update refresh branch and PR")
    update_end = workflow.index("- name: No new FEL releases", update_start)
    update_step = workflow[update_start:update_end]

    assert "actions: write" in permissions
    assert 'gh workflow run ci.yml --ref "$PR_BRANCH"' in update_step
    assert update_step.index("git push --force-with-lease") < update_step.index(
        'gh workflow run ci.yml --ref "$PR_BRANCH"'
    )


def test_pages_workflow_prunes_only_added_unreferenced_posters_before_diff_check():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    ai_start = workflow.index("- name: Run AI discovery and scraping")
    prune_start = workflow.index("- name: Prune refresh-only stale posters")
    summary_start = workflow.index("- name: Summarize release additions")
    check_start = workflow.index("- name: Check refresh branch changes")
    prune_step = workflow[prune_start:summary_start]

    assert ai_start < prune_start < summary_start < check_start
    assert "python -m main prune-posters" in prune_step
    assert "--base-ref origin/main" in prune_step
    assert "--include-added" in prune_step
    assert "--include-untracked" in prune_step


def test_pages_workflow_closes_existing_refresh_pr_when_no_pending_releases():
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    cleanup_start = workflow.index("- name: Close empty refresh PR")
    cleanup_end = workflow.index("- name: No new FEL releases", cleanup_start)
    cleanup_step = workflow[cleanup_start:cleanup_end]

    assert "steps.release_delta.outputs.pending_release_count == '0'" in cleanup_step
    assert "steps.prepare_branch.outputs.pr_number != ''" in cleanup_step
    assert 'gh pr close "$PR_NUMBER"' in cleanup_step
    assert "--delete-branch" in cleanup_step


def test_ci_workflow_can_be_dispatched_for_refresh_branch():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    trigger_start = workflow.index("on:")
    trigger_end = workflow.index("permissions:", trigger_start)
    trigger_block = workflow[trigger_start:trigger_end]

    assert "workflow_dispatch:" in trigger_block


def test_trakt_sync_workflow_has_required_shape():
    workflow = Path(".github/workflows/trakt-sync.yml").read_text(encoding="utf-8")
    for marker in (
        "name: Sync Trakt FEL List",
        "paths:\n      - data/releases.json",
        'cron: "53 10,22 * * *"',
        "workflow_dispatch:",
        "concurrency:\n  group: trakt-sync",
        "${{ secrets.TRAKT_APP_CLIENT_ID }}",
        "${{ secrets.TRAKT_APP_CLIENT_SECRET }}",
        "${{ secrets.TRAKT_REFRESH_TOKEN }}",
        "${{ secrets.TRAKT_SYNC_PAT }}",
        "python -m main trakt-sync",
        "gh secret set TRAKT_REFRESH_TOKEN",
    ):
        assert marker in workflow, f"missing: {marker!r}"


def test_trakt_sync_workflow_rotates_on_always_so_invalidated_tokens_are_persisted():
    workflow = Path(".github/workflows/trakt-sync.yml").read_text(encoding="utf-8")
    rotate_start = workflow.index("- name: Rotate refresh-token secret")
    rotate_end = workflow.index("env:", rotate_start)
    rotate_header = workflow[rotate_start:rotate_end]
    assert "if: always()" in rotate_header
    assert "if: success()" not in rotate_header
