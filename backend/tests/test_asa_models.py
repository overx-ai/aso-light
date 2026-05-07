import pytest
from app.models.asa import (
    ASACredential, ASAOrg, ASACampaign, ASAAdGroup, ASAKeyword,
    ASANegativeKeyword, ASASearchTerm, ASAMetricDaily, ASASyncOperation,
)


def test_asa_credential_table_name():
    assert ASACredential.__tablename__ == "asa_credentials"


def test_asa_org_table_name():
    assert ASAOrg.__tablename__ == "asa_orgs"


def test_asa_campaign_table_name():
    assert ASACampaign.__tablename__ == "asa_campaigns"


def test_asa_ad_group_table_name():
    assert ASAAdGroup.__tablename__ == "asa_ad_groups"


def test_asa_keyword_table_name():
    assert ASAKeyword.__tablename__ == "asa_keywords"


def test_asa_negative_keyword_table_name():
    assert ASANegativeKeyword.__tablename__ == "asa_negative_keywords"


def test_asa_search_term_table_name():
    assert ASASearchTerm.__tablename__ == "asa_search_terms"


def test_asa_metric_daily_table_name():
    assert ASAMetricDaily.__tablename__ == "asa_metric_daily"


def test_asa_sync_operation_table_name():
    assert ASASyncOperation.__tablename__ == "asa_sync_operations"
