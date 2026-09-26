"""Run the pinned 22-genre migration with inherited real-I/O guards.

From backend: python -B tests/run_upstream_suite_offline.py -q tests
Only the task/evidence labels differ from the existing Foundation runner.
No historical Run, real library, model, Embedding or network is authorized.
"""
from run_multi_genre_offline import main


if __name__ == "__main__":
    raise SystemExit(main(
        workspace_relative="tmp/upstream-22-genre-migration",
        task_name="UPSTREAM-22-GENRE-MIGRATION",
        count_label="UPSTREAM_SUITE_OFFLINE_COUNTS",
        require_user_manifest=True,
    ))
