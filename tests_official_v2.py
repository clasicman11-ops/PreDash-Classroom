import unittest
from datetime import date
from unittest.mock import Mock, patch

from predash.official import DataError, Official, STOCK_PRICE_URL


class StockPriceV2Tests(unittest.TestCase):
    def setUp(self):
        self.client = Official(price_key="test+key/==")
        self.row = {"srtnCd": "348210", "itmsNm": "넥스틴", "basDt": "20261002", "clpr": "100"}
        self.payload = {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                        "body": {"items": {"item": [self.row]}, "totalCount": 1}}

    def mock_get(self, payload):
        return patch("predash.official.get", return_value=Mock(json=Mock(return_value=payload)))

    def test_official_v2_endpoint_and_lookup_parameters(self):
        self.assertEqual(STOCK_PRICE_URL, "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2")
        with self.mock_get(self.payload) as request:
            self.assertEqual(self.client.search_prices("넥스틴"), [{"code": "348210", "name": "넥스틴"}])
        url, params = request.call_args.args
        self.assertEqual(url, STOCK_PRICE_URL)
        self.assertEqual(params["likeItmsNm"], "넥스틴")
        self.assertEqual(params["serviceKey"], "test+key/==")
        self.assertEqual(params["resultType"], "json")

    def test_quotes_accept_wrapped_and_unwrapped_v2_envelopes(self):
        for payload in (self.payload, {"response": self.payload}):
            with self.mock_get(payload) as request:
                self.assertEqual(self.client.price("348210", date(2026, 10, 2)), (100, "20261002", "넥스틴"))
            self.assertEqual(request.call_args.args[0], STOCK_PRICE_URL)
            self.assertEqual(request.call_args.args[1]["basDt"], "20261002")

    def test_history_uses_v2_and_preserves_exclusive_end_date(self):
        with self.mock_get(self.payload) as request:
            self.assertEqual(self.client.price_history("348210", date(2026, 10, 2)), [self.row])
        self.assertEqual(request.call_args.args[0], STOCK_PRICE_URL)
        params = request.call_args.args[1]
        self.assertEqual(params["endBasDt"], "20261003")
        self.assertEqual(params["likeSrtnCd"], "348210")

    def test_empty_quote_day_looks_back_without_changing_endpoint(self):
        empty = {"header": {"resultCode": "00"}, "body": {"items": None}}
        with patch("predash.official.get", side_effect=[Mock(json=Mock(return_value=empty)), Mock(json=Mock(return_value=self.payload))]) as request:
            self.assertEqual(self.client.price("348210", date(2026, 10, 3)), (100, "20261002", "넥스틴"))
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[0] == STOCK_PRICE_URL for call in request.call_args_list))

    def test_json_gateway_error_is_safe_and_not_retried(self):
        for code in ("20", "31", "private-test-key"):
            payload = {"header": {"resultCode": code, "resultMsg": "private-test-key"}}
            with self.mock_get(payload) as request:
                with self.assertRaises(DataError) as caught:
                    self.client.search_prices("넥스틴")
            self.assertEqual(request.call_count, 1)
            self.assertNotIn("private-test-key", str(caught.exception))
            self.assertIn("공공데이터 코드", str(caught.exception))

    def test_malformed_response_does_not_become_empty_search(self):
        for payload in ([], {"response": None}, {"body": {}}):
            with self.mock_get(payload):
                with self.assertRaisesRegex(DataError, "응답"):
                    self.client.search_prices("넥스틴")


if __name__ == "__main__":
    unittest.main()
