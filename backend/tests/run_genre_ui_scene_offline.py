"""Run UI/Scene integration tests with unchanged Foundation I/O guards.

From backend: python -B tests/run_genre_ui_scene_offline.py -q tests
Only evidence/task labels differ. Real libraries, historical Runs, models,
Embedding and external networking remain prohibited by the shared runner.
"""
from run_multi_genre_offline import main


if __name__ == "__main__":
    raise SystemExit(main(
        workspace_relative="tmp/genre-ui-scene-routing-integration",
        task_name="GENRE-UI-SCENE-ROUTING-INTEGRATION",
        count_label="GENRE_UI_SCENE_OFFLINE_COUNTS",
        require_user_manifest=True,
    ))
