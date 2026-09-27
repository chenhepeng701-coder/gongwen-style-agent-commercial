"""Business Domain tests using the unchanged shared offline I/O guards."""
from run_multi_genre_offline import main


if __name__ == "__main__":
    raise SystemExit(main(
        workspace_relative="tmp/business-domain-taxonomy-01",
        task_name="BUSINESS-DOMAIN-TAXONOMY-01",
        count_label="BUSINESS_DOMAIN_OFFLINE_COUNTS",
        require_user_manifest=True,
    ))
