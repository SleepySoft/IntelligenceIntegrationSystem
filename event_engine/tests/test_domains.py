import subprocess
import sys
import unittest

from event_engine.domains import load_pack, registry_for


class DomainBoundaryTests(unittest.TestCase):
    def run_isolated(self, code):
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)

    def test_domains_entry_does_not_load_packs_or_execution(self):
        self.run_isolated(
            "import sys; import event_engine.domains; "
            "assert not any(n.startswith(('event_engine.domains.news', "
            "'event_engine.domains.industry', 'event_engine.domains.financial', "
            "'event_engine.core')) for n in sys.modules)"
        )

    def test_financial_declarations_do_not_load_algorithms_or_other_domains(self):
        self.run_isolated(
            "import sys; from event_engine.domains.financial import PACK; "
            "assert PACK.domain_id == 'financial'; "
            "assert not any(n.startswith(('event_engine.core', 'event_engine.domains.news', "
            "'event_engine.domains.industry')) or n.endswith('.analyzer') "
            "for n in sys.modules)"
        )

    def test_selected_registry_works_with_unselected_domains_unavailable(self):
        self.run_isolated(
            """import sys
from event_engine.domains import registry_for

class BlockUnselected:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('event_engine.domains.news', 'event_engine.domains.industry')):
            raise ImportError('unselected domain unavailable')

sys.meta_path.insert(0, BlockUnselected())
registry = registry_for('financial')
assert dict(registry.pack_versions) == {'financial': '1.2'}
assert 'issue_bond' in registry and 'attack' not in registry
assert not any(n.startswith(('event_engine.domains.news', 'event_engine.domains.industry'))
               for n in sys.modules)
"""
        )

    def test_selected_packs_compose_without_news(self):
        registry = registry_for("industry", "financial")
        self.assertEqual({"industry": "1.2", "financial": "1.2"}, dict(registry.pack_versions))
        self.assertIn("build_facility", registry)
        self.assertIn("issue_bond", registry)
        self.assertNotIn("attack", registry)

    def test_no_selection_is_empty_and_invalid_selection_is_rejected(self):
        self.assertFalse(registry_for())
        with self.assertRaisesRegex(ValueError, "未知领域包"):
            load_pack("unknown")
        with self.assertRaisesRegex(ValueError, "重复领域包"):
            registry_for("financial", "financial")

    def test_news_algorithm_and_result_are_explicit_domain_modules(self):
        self.run_isolated(
            "import sys; from event_engine.domains.news import PACK; "
            "assert 'event_engine.domains.news.analyzer' not in sys.modules; "
            "from event_engine.domains.news.analyzer import NewsAnalyzer; "
            "from event_engine.domains.news.schema import WarZoneView; "
            "from event_engine.core import EventAnalyzer; "
            "assert NewsAnalyzer(EventAnalyzer(PACK.specs)).extract_war_zones(()) == (); "
            "assert WarZoneView.__module__ == 'event_engine.domains.news.schema'"
        )


if __name__ == "__main__":
    unittest.main()
