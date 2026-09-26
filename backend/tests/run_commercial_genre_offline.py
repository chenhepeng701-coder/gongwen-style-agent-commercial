"""Run commercial infrastructure tests with the Foundation's shared I/O guards.

From backend: python -B tests/run_commercial_genre_offline.py -q tests
Only the evidence directory/label change; networking, model/Embedding blocking,
synthetic runtime isolation and historical real-Run replay policy are inherited.
"""
from run_multi_genre_offline import main


if __name__ == "__main__":
    raise SystemExit(main(
        workspace_relative="tmp/multi-genre-commercial-infrastructure",
        task_name="MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE",
        count_label="COMMERCIAL_GENRE_OFFLINE_COUNTS",
        require_user_manifest=True,
    ))
