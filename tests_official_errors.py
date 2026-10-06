import unittest
from unittest.mock import patch

import requests

from predash.official import DataError, Official, get


PRICE_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"


def response(status, content):
    result = requests.Response()
    result.status_code = status
    result._content = content
    return result


class OfficialErrorTests(unittest.TestCase):
    def call_error(self, status, content, url=PRICE_URL):
        with patch("predash.official.requests.get", return_value=response(status, content)) as request:
            with self.assertRaises(DataError) as caught:
                get(url, {"serviceKey": "private-key"})
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("private-key", str(caught.exception))
        return str(caught.exception)

    def test_http_403_preserves_gateway_reason_and_hides_raw_message(self):
        error = self.call_error(403, b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>20</returnReasonCode><errMsg>private-key https://secret.example</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")
        self.assertIn("HTTP 403", error)
        self.assertIn("공공데이터 코드 20", error)
        self.assertIn("활용신청", error)
        self.assertNotIn("secret.example", error)

    def test_expiry_invalid_key_and_ip_have_separate_guidance(self):
        for code, hint in (("31", "만료"), ("30", "등록되지"), ("29", "차단 해제"), ("32", "IP 제한")):
            with self.subTest(code=code):
                error = self.call_error(403, f"<error><returnReasonCode>{code}</returnReasonCode></error>".encode())
                self.assertIn(hint, error)

    def test_namespaced_success_status_with_gateway_error_is_supported(self):
        error = self.call_error(200, b'<e:error xmlns:e="urn:error"><e:returnReasonCode>22</e:returnReasonCode></e:error>')
        self.assertIn("공공데이터 코드 22", error)
        self.assertIn("일일 호출", error)

    def test_plain_html_403_does_not_claim_ip_block(self):
        error = self.call_error(403, b"<html><body>Forbidden private-key</body></html>")
        self.assertIn("확정할 수 없습니다", error)
        self.assertIn("활용승인", error)

    def test_malformed_or_sensitive_reason_is_not_echoed(self):
        for content in (b"<error><returnReasonCode>private-key</returnReasonCode></error>", b"<broken", b"<error><returnReasonCode>12345678</returnReasonCode></error>"):
            error = self.call_error(403, content)
            self.assertNotIn("12345678", error)
            self.assertIn("HTTP 403", error)

    def test_unknown_numeric_code_has_safe_fallback(self):
        error = self.call_error(403, b"<error><returnReasonCode>999</returnReasonCode></error>")
        self.assertIn("공공데이터 코드 999", error)
        self.assertIn("서비스 오류", error)

    def test_dart_xml_error_is_still_handled(self):
        error = self.call_error(403, b"<result><status>012</status><message>private-key</message></result>", "https://opendart.fss.or.kr/api/corpCode.xml")
        self.assertIn("DART 코드 012", error)
        self.assertIn("IP 제한", error)

    def test_success_json_and_zip_are_unchanged(self):
        for content in (b'{"response": {}}', b"PK\x03\x04zip", b"<result><status>000</status></result>"):
            result = response(200, content)
            with patch("predash.official.requests.get", return_value=result):
                self.assertIs(get(PRICE_URL, {}), result)

    def test_transient_error_retries_once(self):
        ok = response(200, b'{}')
        with patch("predash.official.requests.get", side_effect=[response(503, b"unavailable"), ok]) as request:
            self.assertIs(get(PRICE_URL, {}), ok)
        self.assertEqual(request.call_count, 2)

    def test_encoding_key_is_decoded_once_before_requests(self):
        raw = "private+key/=="
        for key in (raw, "private%2Bkey%2F%3D%3D"):
            client = Official(price_key=key)
            prepared = requests.Request("GET", PRICE_URL, params={"serviceKey": client.price_key}).prepare()
            self.assertEqual(client.price_key, raw)
            self.assertIn("serviceKey=private%2Bkey%2F%3D%3D", prepared.url)
            self.assertNotIn("%252B", prepared.url)


if __name__ == "__main__":
    unittest.main()
