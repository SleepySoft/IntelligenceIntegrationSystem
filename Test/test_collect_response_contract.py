"""提交应答契约与治理轮次计数的单元测试。

覆盖：
- Workflow.CommonFlowUtility.normalize_collect_response：/collect 各类历史/异常应答
  的规范化（含 resp 为数组的历史 bug）；
- CrawlerGovernanceCore：skip_round_step/reduce_round_step 同步 entry_round 快照。
"""
import pytest

from Workflow.CollectorSubmission import normalize_collect_response


class TestNormalizeCollectResponse:
    def test_queued(self):
        assert normalize_collect_response({'resp': 'queued', 'uuid': 'x'}) == ('queued', '')

    def test_legacy_tuple_resp(self):
        # 历史 bug：服务端 resp 曾是单元素数组。
        assert normalize_collect_response({'resp': ['queued'], 'uuid': 'x'}) == ('queued', '')

    def test_rejected_with_reason(self):
        assert normalize_collect_response(
            {'resp': 'rejected', 'reason': 'collected_data_duplicated'}
        ) == ('rejected', 'collected_data_duplicated')

    def test_legacy_invalid_token(self):
        assert normalize_collect_response({'resp': 'invalid token'}) == ('rejected', 'invalid_token')

    def test_transport_error(self):
        assert normalize_collect_response(
            {'status': 'error', 'reason': 'Timeout after 10s'}
        ) == ('error', 'Timeout after 10s')

    def test_maintenance_is_retryable_error(self):
        assert normalize_collect_response({'resp': 'maintenance'})[0] == 'error'

    def test_garbage(self):
        assert normalize_collect_response(None)[0] == 'error'
        assert normalize_collect_response({})[0] == 'error'


class TestGovernanceRoundStats:
    @pytest.fixture()
    def governor(self, tmp_path):
        from IntelligenceCrawler.CrawlerGovernanceCore import GovernanceManager
        return GovernanceManager(
            db_path=str(tmp_path / 'governance.db'),
            files_path=str(tmp_path / 'files'),
        )

    def test_skip_and_reduce_round_step_sync_entry_round(self, governor):
        group = 'spider/feed'
        feed_url = 'https://example.test/feed'
        governor.register_group_metadata(group, feed_url)

        # 入口（feed）事务建立 entry round 并标记成功。
        with governor.transaction(feed_url, group) as task:
            task.success()
        governor.start_round(group, 20)

        # 19 条已抓过 → skip；1 条空链接 → reduce。
        governor.skip_round_step(group, 19)
        governor.reduce_round_step(group, 1)
        governor.finish_round(group, 60)

        snapshot = governor.entry_round_snapshots[group]
        assert snapshot['articles_expected'] == 19
        assert snapshot['articles_processed'] == 19
        assert snapshot['articles_ignored'] == 19

        # 持久化验证：重开库读 entry_rounds。
        row = governor.db.fetch_one(
            "SELECT articles_expected, articles_processed, articles_ignored "
            "FROM entry_rounds WHERE group_path = ?", (group,))
        assert row['articles_expected'] == 19
        assert row['articles_processed'] == 19
        assert row['articles_ignored'] == 19

    def test_article_finish_accumulates_into_round(self, governor):
        group = 'spider/feed2'
        feed_url = 'https://example.test/feed2'
        governor.register_group_metadata(group, feed_url)
        with governor.transaction(feed_url, group) as task:
            task.success()
        governor.start_round(group, 2)

        with governor.transaction('https://example.test/a1', group) as task:
            task.success()
        with governor.transaction('https://example.test/a2', group) as task:
            task.fail_temp(state_msg='fetch error')
        governor.finish_round(group, 60)

        row = governor.db.fetch_one(
            "SELECT articles_processed, articles_success, articles_failed "
            "FROM entry_rounds WHERE group_path = ?", (group,))
        assert row['articles_processed'] == 2
        assert row['articles_success'] == 1
        assert row['articles_failed'] == 1
