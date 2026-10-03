"""Streamlit account isolation tests with stubbed broker/network responses."""
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from predash.kiwoom import Kiwoom
from predash.macro import MacroError

APP = Path(__file__).with_name('app.py')


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


def snapshot(client):
    return {'positions': [], 'value': 0, 'pnl': 0, 'cash': 10000, 'cash_error': '',
            'mode': client.mode, 'fetched': '2026-10-02T20:00:00+09:00'}


class KiwoomUITests(unittest.TestCase):
    def setUp(self):
        patch('predash.macro.fetch_vix', side_effect=MacroError('테스트: 외부 조회 생략')).start()
        patch('requests.post', side_effect=AssertionError('Unexpected live API request')).start()
        self.addCleanup(patch.stopall)
        self.at = AppTest.from_file(APP, default_timeout=10)
        self.at.secrets['APP_PASSWORD'] = 'local-test-password-only'
        self.at.session_state.authorized = True
        self.at.session_state.navigation = '연결 설정'
        self.at.run()
        self.assertFalse(self.at.exception)

    def connect(self, mode):
        by_label(self.at.radio, '연결할 환경').set_value(mode)
        by_label(self.at.text_input, '키움 App Key').set_value('dummy-' + mode)
        by_label(self.at.text_input, '키움 App Secret').set_value('dummy-secret-' + mode)
        with patch.object(Kiwoom, 'balance', autospec=True, side_effect=snapshot):
            by_label(self.at.button, '키움 연결 확인').click().run()
        self.assertFalse(self.at.exception)

    def test_all_unconnected_pages_render(self):
        for page in ('오늘의 점검','관심종목','투자 근거','내 계좌','모의투자','매매 연습','매매 습관','연결 설정'):
            by_label(self.at.radio,'메뉴').set_value(page).run()
            self.assertFalse(self.at.exception, page)

    def test_both_modes_keep_separate_keys_and_clear_account_results(self):
        self.connect('demo')
        self.assertEqual(self.at.session_state.kiwoom_credentials['demo']['key'], 'dummy-demo')
        self.connect('real')
        self.assertEqual(set(self.at.session_state.kiwoom_credentials), {'demo','real'})
        self.assertEqual(self.at.session_state.kiwoom_active_mode, 'real')
        self.at.session_state.snapshot = {'sensitive_test_marker': 'old-real-result'}
        self.at.session_state.watch_results = {'005930': {'old-real-flow': True}}
        by_label(self.at.radio,'일반 화면에서 사용할 계좌').set_value('demo').run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state.kiwoom_active_mode, 'demo')
        self.assertNotIn('snapshot', self.at.session_state)
        self.assertNotIn('watch_results', self.at.session_state)
        self.assertEqual(self.at.session_state.kiwoom_credentials['real']['key'], 'dummy-real')
        by_label(self.at.button,'실전 조회 연결 해제').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(set(self.at.session_state.kiwoom_credentials), {'demo'})
        self.assertNotIn('_kiwoom_client_real', self.at.session_state)

    def test_account_number_not_requested_and_password_fields_masked(self):
        broker_inputs=[e.label for e in self.at.text_input if e.label.startswith('키움')]
        self.assertEqual(broker_inputs, ['키움 App Key','키움 App Secret'])
        for element in self.at.text_input:
            self.assertEqual(element.proto.type, 1)

    def test_connected_demo_refresh_and_main_account_render(self):
        self.connect('demo')
        by_label(self.at.radio, '메뉴').set_value('모의투자').run()
        with patch.object(Kiwoom, 'balance', autospec=True, side_effect=snapshot):
            by_label(self.at.button, '모의계좌 연결 확인 · 잔고 새로고침').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state.demo_snapshot['mode'], 'demo')
        by_label(self.at.radio, '메뉴').set_value('내 계좌').run()
        with patch.object(Kiwoom, 'balance', autospec=True, side_effect=snapshot):
            by_label(self.at.button, '내 계좌 새로고침').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state.snapshot['mode'], 'demo')

    def test_public_api_key_survives_broker_connection(self):
        by_label(self.at.text_input, 'DART API Key').set_value('dummy-dart')
        by_label(self.at.button, '입력한 API 키 적용').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state.classroom_api_keys['DART_CRTFC_KEY'], 'dummy-dart')
        self.connect('demo')
        self.assertEqual(self.at.session_state.classroom_api_keys['DART_CRTFC_KEY'], 'dummy-dart')

    def test_logout_removes_both_credentials(self):
        self.connect('demo')
        by_label(self.at.button,'로그아웃').click().run()
        self.assertFalse(self.at.exception)
        self.assertNotIn('kiwoom_credentials', self.at.session_state)
        self.assertNotIn('_kiwoom_client_demo', self.at.session_state)


if __name__ == '__main__':unittest.main()
