import io
import unittest
import zipfile
from unittest.mock import Mock, patch

import requests

from predash.official import Official, DataError, CORP_DIRECTORY_TTL


def directory():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        archive.writestr('CORPCODE.xml', '<result><list><corp_code>00126380</corp_code><corp_name>테스트 기업</corp_name><stock_code>005930</stock_code></list></result>')
    return Mock(content=raw.getvalue())


class CorpDirectoryTests(unittest.TestCase):
    def test_new_clients_reuse_directory_but_not_financial_responses(self):
        cache = {}
        with patch('predash.official.get', return_value=directory()) as fetch:
            first = Official(dart_key='test-key', directory_cache=cache)
            self.assertEqual(first.corp('005930'), '00126380')
            first.annual_cache[('test', 2026, 'CFS')] = {'profit': 1}
            second = Official(dart_key='test-key', directory_cache=cache)
            self.assertEqual(second.corp('005930'), '00126380')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(second.names['005930'], '테스트 기업')
        self.assertEqual(second.annual_cache, {})

    def test_expired_directory_is_reloaded(self):
        cache = {}
        with patch('predash.official.monotonic', return_value=100), patch('predash.official.get', return_value=directory()) as fetch:
            Official(directory_cache=cache).corp('005930')
            with patch('predash.official.monotonic', return_value=100 + CORP_DIRECTORY_TTL):
                Official(directory_cache=cache).corp('005930')
        self.assertEqual(fetch.call_count, 2)

    def test_timeout_does_not_repeat_download_for_metrics_and_disclosures(self):
        cache = {}
        with patch('predash.official.monotonic', return_value=100), patch('predash.official.requests.get', side_effect=requests.Timeout('secret-key in request')) as fetch:
            for code in ('005930', '005930', '000660'):
                with self.assertRaises(DataError) as caught:
                    Official(dart_key='secret-key', directory_cache=cache).corp(code)
                self.assertIn('응답 시간이 초과', str(caught.exception))
                self.assertNotIn('secret-key', str(caught.exception))
        # One directory attempt with its existing single network retry only.
        self.assertEqual(fetch.call_count, 2)

    def test_force_reload_recovers_during_failure_cooldown(self):
        cache = {}
        with patch('predash.official.monotonic', return_value=100), patch('predash.official.get', side_effect=[DataError('timeout'), directory()]) as fetch:
            client = Official(directory_cache=cache)
            with self.assertRaises(DataError):client.corp('005930')
            client.load_corps(force=True)
            self.assertEqual(client.corp('005930'), '00126380')
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn('error', cache)

    def test_failed_download_is_retried_after_cooldown(self):
        cache = {}
        with patch('predash.official.monotonic', return_value=100), patch('predash.official.get', side_effect=[DataError('timeout'), directory()]) as fetch:
            with self.assertRaises(DataError):Official(directory_cache=cache).corp('005930')
            with patch('predash.official.monotonic', return_value=160):
                self.assertEqual(Official(directory_cache=cache).corp('005930'), '00126380')
        self.assertEqual(fetch.call_count, 2)

    def test_direct_binding_checks_identity_and_bypasses_directory_timeout(self):
        cache = {'error': 'timeout', 'retry_after': float('inf')}
        client = Official(directory_cache=cache)
        with patch.object(client, 'dart', return_value={'stock_code': '005930', 'corp_name': '테스트 기업'}) as company:
            self.assertEqual(client.bind_corp('005930', '00126380'), '테스트 기업')
        company.assert_called_once_with('company.json', corp_code='00126380')
        with patch('predash.official.get', side_effect=AssertionError('Directory must not be fetched')):
            other = Official(directory_cache=cache)
            self.assertEqual(other.corp('005930'), '00126380')
            self.assertEqual(other.names['005930'], '테스트 기업')
            with self.assertRaises(DataError):other.corp('000660')
        with patch.object(client, 'dart', return_value={'stock_code': '000660'}):
            with self.assertRaises(DataError):client.bind_corp('005930', '00126381')
        self.assertEqual(cache['overrides']['005930']['corp_code'], '00126380')

    def test_invalid_binding_does_not_send_request_or_save_mapping(self):
        client = Official()
        with patch.object(client, 'dart') as company:
            for code, corp in (('005930', '123'), ('00593A', '00126380'), ('005930', '００１２６３８０')):
                with self.assertRaises(DataError):client.bind_corp(code, corp)
        company.assert_not_called()
        self.assertNotIn('overrides', client.directory_cache)


if __name__ == '__main__':unittest.main()
