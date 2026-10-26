from pathlib import Path
import tempfile
import unittest
from yanyue.crawler import run_pages
from yanyue.fetch import Page
from yanyue.parser import parse_page
from yanyue.policy import BudgetExhausted, Pause, PolicyError
from yanyue.store import Store

FIX = Path(__file__).parent / "fixtures"

class FakeFetcher:
    def __init__(self, page): self.page, self.calls = page, 0
    def fetch(self, url):
        self.calls += 1
        if isinstance(self.page, Exception): raise self.page
        return Page(self.page, "offline_fixture")

class StoreCrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def product_job(self):
        self.store.enqueue({"kind": "product", "url": "https://www.yanyue.cn/product/442", "name": "中华(硬)", "brand_source_id": "13"})
        self.store.db.commit()
    def test_last_good_survives_failed_refresh(self):
        self.product_job()
        fetch = FakeFetcher((FIX / "product-442.html").read_text())
        run_pages(self.store, fetch)
        self.assertEqual(self.store.coverage()["products_parsed"], 1)
        self.store.db.execute("UPDATE queue SET status='pending'")
        self.store.db.commit()
        run_pages(self.store, FakeFetcher(Pause("source_http_403")))
        self.assertEqual(self.store.coverage()["products_parsed"], 1)
        self.assertEqual(self.store.meta("pause")["reason"], "source_http_403")
        never = FakeFetcher("anything")
        with self.assertRaises(Pause): run_pages(self.store, never)
        self.assertEqual(never.calls, 0)
    def test_budget_keeps_pending(self):
        self.product_job()
        run_pages(self.store, FakeFetcher(BudgetExhausted()))
        self.assertEqual(self.store.coverage()["queue"]["product"]["pending"], 1)
    def test_discovered_links_only_and_page_one_dedup(self):
        self.store.enqueue({"kind": "brand", "url": "https://www.yanyue.cn/sort/13"})
        self.store.db.commit()
        run_pages(self.store, FakeFetcher((FIX / "brand-13.html").read_text()), max_pages=1)
        counts = self.store.coverage()["queue"]
        self.assertEqual(counts["brand"]["discovered"], 6)
        self.assertEqual(counts["product"]["discovered"], 10)
    def test_wrong_page_rejected(self):
        with self.assertRaises(PolicyError):
            parse_page((FIX / "brand-13.html").read_text(), url="https://www.yanyue.cn/sort/sort/sortid/13/p/2")
    def test_lock_excludes_second_worker(self):
        other = Store(self.temp.name)
        try:
            with self.store.exclusive(), self.assertRaises(PolicyError), other.exclusive(): pass
        finally: other.close()

if __name__ == '__main__': unittest.main()
