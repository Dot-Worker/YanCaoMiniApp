import pathlib
import unittest
from yanyue.parser import parse_index, parse_product
from yanyue.policy import Pause, PolicyError, canonical_url

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

class ParserTests(unittest.TestCase):
    def test_product_strict_scope_and_unknown_images(self):
        product = parse_product((FIXTURES / "product-442.html").read_text(), url="https://www.yanyue.cn/product/442")["product"]
        self.assertEqual(product["name"], "中华(硬)")
        self.assertEqual(product["fields"]["package_form"]["value"], "纸质硬盒")
        self.assertIsNone(product["measurements"]["tar_mg"]["value"])
        self.assertEqual(product["measurements"]["tar_mg"]["verification_status"], "needs_review")
        self.assertEqual(len(product["barcodes"][0]["evidence_urls"]), 2)
        self.assertIsNone(product["barcodes"][0]["value"])
        self.assertEqual(len(product["images"]), 1)
        self.assertNotIn("763a416", str(product))  # Forbidden price image.
        self.assertNotIn("更多", str(product))
        self.assertFalse(product["manufacturer_verified"])

    def test_index_deduplicates_and_preserves_memberships(self):
        parsed = parse_index((FIXTURES / "index.html").read_text())
        self.assertEqual(len(parsed["brands"]), 2)
        self.assertEqual(parsed["brands"][0]["source_groups"], ["大陆品牌", "国外品牌"])
        self.assertNotIn("99", [x["source_id"] for x in parsed["brands"]])

    def test_loader_and_missing_identity_rejected(self):
        with self.assertRaises(Pause):
            parse_product((FIXTURES / "loader.html").read_text(), url="https://www.yanyue.cn/product/442")
        with self.assertRaises(PolicyError):
            parse_product("<h3>中华</h3>", url="https://www.yanyue.cn/product/442")

    def test_urls_reject_secret_private_and_other_paths(self):
        for url in ["http://www.yanyue.cn/product/1", "https://127.0.0.1/product/1", "https://www.yanyue.cn/product/1?loading=1", "/product/1/token/secret", "/gallery/442", "//evil.test/product/1", "/sort/%31", "/product/1#anything"]:
            with self.subTest(url=url), self.assertRaises(PolicyError):
                canonical_url(url)
        self.assertEqual(canonical_url("/product/442"), "https://www.yanyue.cn/product/442")

if __name__ == "__main__":
    unittest.main()
