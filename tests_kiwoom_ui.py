"""Streamlit account isolation tests with stubbed broker/network responses."""
from pathlib import Path
import unittest
from unittest.mock import patch
import json

from streamlit.testing.v1 import AppTest
from predash.kiwoom import Kiwoom
from predash.macro import MacroError
from predash.official import Official, DataError
from predash.watchlist import export_backup

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
        self.browser_payload = None
        patch('predash.watch_storage.browser_store', side_effect=self.browser).start()
        self.at = AppTest.from_file(APP, default_timeout=10)
        self.at.secrets['APP_PASSWORD'] = 'local-test-password-only'
        self.at.session_state.authorized = True
        self.at.session_state.navigation = '연결 설정'
        self.at.run()
        self.assertFalse(self.at.exception)

    def browser(self, *, data, **kwargs):
        if data['operation'] == 'load':
            return {'result': dict(request_id=data['request_id'], status='loaded', payload=self.browser_payload)}
        self.browser_payload = data['payload']
        return {'result': dict(request_id=data['request_id'], status='saved')}

    def fresh_session(self, authorized=True, secrets=None, page='연결 설정'):
        at = AppTest.from_file(APP, default_timeout=10)
        at.secrets['APP_PASSWORD'] = 'local-test-password-only'
        for key, value in (secrets or {}).items():at.secrets[key] = value
        if authorized:at.session_state.authorized = True
        at.session_state.navigation = page
        return at

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
            if element.label.startswith('키움') or element.label.endswith('API Key'):
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

    def test_saved_keys_require_login_then_reload_in_new_session(self):
        secrets = {'KIWOOM_REAL_APP_KEY': 'dummy-saved-real', 'KIWOOM_REAL_APP_SECRET': 'dummy-saved-secret',
                   'KIWOOM_DEMO_APP_KEY': 'dummy-saved-demo', 'KIWOOM_DEMO_APP_SECRET': 'dummy-saved-demo-secret',
                   'KIWOOM_DEFAULT_MODE': 'real'}
        at = self.fresh_session(False, secrets).run()
        self.assertFalse(at.exception)
        self.assertNotIn('kiwoom_credentials', at.session_state)
        by_label(at.text_input, '대시보드 비밀번호').set_value('local-test-password-only')
        by_label(at.button, '내 대시보드 열기').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(set(at.session_state.kiwoom_credentials), {'real', 'demo'})
        self.assertEqual(at.session_state.kiwoom_active_mode, 'real')
        self.assertNotIn('dummy-saved-real', '\n'.join(e.value for e in at.info))
        by_label(at.button, '실전 조회 연결 해제').click().run()
        at.run()
        self.assertFalse(at.exception)
        self.assertNotIn('real', at.session_state.kiwoom_credentials)
        by_label(at.button, '로그아웃').click().run()
        self.assertNotIn('kiwoom_credentials', at.session_state)
        reopened = self.fresh_session(True, secrets).run()
        self.assertFalse(reopened.exception)
        self.assertEqual(reopened.session_state.kiwoom_credentials['real']['key'], 'dummy-saved-real')

    def test_incomplete_saved_pair_not_loaded_and_manual_keys_take_priority(self):
        secrets = {'KIWOOM_REAL_APP_KEY': 'dummy-incomplete',
                   'KIWOOM_DEMO_APP_KEY': 'dummy-saved-demo', 'KIWOOM_DEMO_APP_SECRET': 'dummy-saved-secret'}
        at = self.fresh_session(True, secrets)
        at.session_state.kiwoom_credentials = {'demo': dict(mode='demo', key='dummy-manual', secret='dummy-manual-secret')}
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(set(at.session_state.kiwoom_credentials), {'demo'})
        self.assertEqual(at.session_state.kiwoom_credentials['demo']['key'], 'dummy-manual')

    def test_watchlist_survives_new_session_delete_and_logout_without_api_key(self):
        self.browser_payload = export_backup(['005930'], {'005930': '삼성전자'})
        at = self.fresh_session(page='관심종목').run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.watch_names, {'005930': '삼성전자'})
        by_label(at.text_input, '종목코드로 바로 추가').set_value('000660')
        by_label(at.button, '종목코드 저장').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.watch_codes, ['005930', '000660'])
        by_label(at.button, '로그아웃').click().run()
        reopened = self.fresh_session(page='관심종목').run()
        self.assertFalse(reopened.exception)
        self.assertEqual(reopened.session_state.watch_codes, ['005930', '000660'])
        by_label(reopened.button, '관심종목 목록 비우기').click().run()
        empty = self.fresh_session(page='관심종목').run()
        self.assertFalse(empty.exception)
        self.assertEqual(empty.session_state.watch_codes, [])

    def test_watchlist_does_not_write_before_load_or_restore_corrupt_data(self):
        with patch('predash.watch_storage.browser_store', return_value={'result': None}):
            at = self.fresh_session(page='관심종목').run()
            self.assertFalse(at.exception)
            self.assertNotIn('watch_codes', at.session_state)
            self.assertNotIn('관심종목에 저장', [b.label for b in at.button])
        self.browser_payload = 'corrupt-json'
        at = self.fresh_session(page='관심종목').run()
        self.assertFalse(at.exception)
        self.assertEqual(self.browser_payload, 'corrupt-json')
        self.assertEqual(at.session_state.watch_storage_status, 'invalid')

    def test_explicit_bookmark_and_broker_switch_keep_watchlist_storage(self):
        self.browser_payload = export_backup(['005930'], {'005930': '삼성전자'})
        at = self.fresh_session()
        at.query_params['watch'] = '000660'
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.watch_codes, ['000660'])
        nonce = at.session_state.watch_storage_nonce
        self.at = at
        self.connect('demo')
        self.assertEqual(at.session_state.watch_codes, ['000660'])
        self.assertEqual(at.session_state.watch_storage_nonce, nonce)

    def test_watchlist_twenty_limit_groups_filters_and_reopening(self):
        codes=[f'{i:06d}' for i in range(20)]
        self.browser_payload=export_backup(codes[:19])
        at=self.fresh_session(page='관심종목').run()
        self.assertFalse(at.exception)
        by_label(at.text_input,'종목코드로 바로 추가').set_value(codes[19])
        by_label(at.button,'종목코드 저장').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state.watch_codes),20)
        self.assertEqual(len(by_label(at.multiselect,'조회할 종목 선택').value),5)
        by_label(at.text_input,'종목코드로 바로 추가').set_value('999999')
        by_label(at.button,'종목코드 저장').click().run()
        self.assertEqual(len(at.session_state.watch_codes),20)
        self.assertTrue(any('최대 20개' in w.value for w in at.warning))
        by_label(at.selectbox,'분류할 종목').set_value(codes[19]).run()
        by_label(at.selectbox,'관심종목 분류').set_value('보유종목')
        by_label(at.text_input,'섹터 이름').set_value('반도체')
        by_label(at.button,'분류 저장').click().run()
        self.assertFalse(at.exception)
        reopened=self.fresh_session(page='관심종목').run()
        self.assertFalse(reopened.exception)
        self.assertEqual(reopened.session_state.watch_groups[codes[19]],'보유종목')
        self.assertEqual(reopened.session_state.watch_sectors[codes[19]],'반도체')
        self.at=reopened
        by_label(reopened.radio,'메뉴').set_value('연결 설정').run()
        self.connect('demo')
        by_label(reopened.radio,'메뉴').set_value('관심종목').run()
        self.assertEqual(reopened.session_state.watch_groups[codes[19]],'보유종목')
        self.assertEqual(reopened.session_state.watch_sectors[codes[19]],'반도체')
        by_label(reopened.selectbox,'표시할 분류').set_value('보유종목').run()
        self.assertEqual(by_label(reopened.multiselect,'조회할 종목 선택').value,[codes[19]])
        by_label(reopened.selectbox,'표시할 섹터').set_value('반도체').run()
        by_label(reopened.button,'목록에서 제거').click().run()
        self.assertFalse(reopened.exception)
        self.assertNotIn(codes[19],reopened.session_state.watch_groups)
        self.assertNotIn(codes[19],reopened.session_state.watch_sectors)
        restored=self.fresh_session(page='관심종목').run()
        self.assertEqual(len(restored.session_state.watch_codes),19)

    def test_selective_queries_reuse_and_force_only_selected_with_no_background_calls(self):
        self.browser_payload=export_backup(['005930','000660','035420'])
        at=self.fresh_session(page='관심종목')
        at.session_state.classroom_api_keys={'DATA_GO_KR_SERVICE_KEY':'dummy-price'}
        lamp={'state':'상승 구간','close':1,'date':'2026-10-02','ma10':1,'ma20':1}
        with patch.object(Official,'price_history',return_value=[]) as history, \
             patch.object(Official,'search',side_effect=AssertionError('No automatic background lookup')), \
             patch('predash.market.stock_lamp',return_value=lamp), \
             patch('predash.market.price_trend',return_value=[]), \
             patch('predash.macro.benchmark',return_value=None):
            at.run()
            self.assertFalse(at.exception)
            history.assert_not_called()
            by_label(at.multiselect,'조회할 종목 선택').set_value(['005930'])
            by_label(at.button,'선택 종목 조회').click().run()
            self.assertFalse(at.exception)
            self.assertEqual([c.args[0] for c in history.call_args_list],['005930'])
            self.assertEqual(set(at.session_state.watch_results),{'005930'})
            by_label(at.multiselect,'조회할 종목 선택').set_value(['000660'])
            by_label(at.button,'선택 종목 조회').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(set(at.session_state.watch_results),{'005930','000660'})
            by_label(at.multiselect,'조회할 종목 선택').set_value(['005930'])
            by_label(at.button,'선택 종목 조회').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(history.call_count,2)
            self.assertIn('재사용 1개',at.session_state.watch_refresh_notice)
            by_label(at.checkbox,'최신 자료 다시 조회').check()
            by_label(at.button,'선택 종목 조회').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(history.call_count,3)
            self.assertEqual(history.call_args.args[0],'005930')
            saved=json.loads(self.browser_payload)
            self.assertNotIn('lamp',saved)
            self.assertNotIn('dummy-price',self.browser_payload)
        self.at=at
        by_label(at.radio,'메뉴').set_value('연결 설정').run()
        self.connect('demo')
        self.assertNotIn('watch_query_cache',at.session_state)
        self.assertEqual(at.session_state.watch_codes,['005930','000660','035420'])

    def test_dart_diagnostic_is_explicit_and_clears_on_key_change(self):
        self.assertTrue(by_label(self.at.button,'DART 연결 진단(조회 전용)').disabled)
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'dummy-dart'}
        with patch.object(Official,'dart',return_value={'status':'000','corp_name':'테스트 기업'}) as request:
            self.at.run()
            request.assert_not_called()
            by_label(self.at.button,'DART 연결 진단(조회 전용)').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[0],'company.json')
        self.assertTrue(any('DART 인증 및 기업정보 조회 성공' in x.value for x in self.at.success))
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'changed-dart'}
        self.at.run()
        self.assertFalse(any('DART 인증 및 기업정보 조회 성공' in x.value for x in self.at.success))

    def test_dart_diagnostic_failure_shows_provider_reason(self):
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'dummy-dart'}
        self.at.run()
        with patch.object(Official,'dart',side_effect=DataError('DART · 코드 012: 접근 IP 제한')):
            by_label(self.at.button,'DART 연결 진단(조회 전용)').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('코드 012' in x.value for x in self.at.error))

    def test_dart_directory_reload_is_explicit_and_key_change_clears_cache(self):
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'dummy-dart'}
        with patch.object(Official,'load_corps') as load:
            self.at.run()
            load.assert_not_called()
            by_label(self.at.button,'DART 기업목록 다시 불러오기').click().run()
        load.assert_called_once_with(force=True)
        self.assertFalse(self.at.exception)
        saved=self.at.session_state['_dart_directory']
        saved['cache']['overrides']={'005930':{'corp_code':'00126380','name':'테스트 기업'}}
        self.at.session_state['_dart_directory']=saved
        with patch.object(Official,'dart',return_value={'status':'000'}):
            by_label(self.at.button,'DART 연결 진단(조회 전용)').click().run()
        self.assertIn('overrides',self.at.session_state['_dart_directory']['cache'])
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'changed-dart'}
        with patch.object(Official,'dart',return_value={'status':'000'}):
            by_label(self.at.button,'DART 연결 진단(조회 전용)').click().run()
        self.assertNotIn('overrides',self.at.session_state['_dart_directory']['cache'])

    def test_dart_direct_company_connection_checks_stock_code(self):
        self.at.session_state.classroom_api_keys={'DART_CRTFC_KEY':'dummy-dart'}
        self.at.run()
        by_label(self.at.text_input,'직접 연결할 종목코드').set_value('005930')
        by_label(self.at.text_input,'DART 고유번호').set_value('00126380')
        with patch.object(Official,'dart',return_value={'stock_code':'000660'}):
            by_label(self.at.button,'DART 기업 확인 후 직접 연결').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('종목코드가 입력한 종목과 다릅니다' in e.value for e in self.at.error))
        self.assertNotIn('overrides',self.at.session_state['_dart_directory']['cache'])
        with patch.object(Official,'dart',return_value={'stock_code':'005930','corp_name':'테스트 기업'}):
            by_label(self.at.button,'DART 기업 확인 후 직접 연결').click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any('직접 연결 완료' in e.value for e in self.at.success))
        self.assertEqual(self.at.session_state['_dart_directory']['cache']['overrides']['005930']['corp_code'],'00126380')

    def test_evidence_page_explains_missing_dart_key_and_provider_error(self):
        self.browser_payload=export_backup(['348210'],{'348210':'넥스틴'})
        item={'code':'348210','name':'넥스틴','metrics':None,'lamp':None,'flow':None,
              'report':None,'errors':{},'fetched':'2026-10-06 23:53'}
        at=self.fresh_session(page='투자 근거').run()
        at.session_state.watch_codes=['348210']
        at.session_state.watch_names={'348210':'넥스틴'}
        at.session_state.watch_results={'348210':item}
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any('OpenDART 인증키가 없습니다' in x.value for x in at.warning))
        item['errors']={'metrics':'DART · 코드 012: 접근 IP 제한'}
        at.session_state['decision_348210_코스피']={'item':item,'chart':[],'chart_error':''}
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any('코드 012' in x.value for x in at.warning))


if __name__ == '__main__':unittest.main()
