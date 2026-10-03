"""Offline Kiwoom contract tests: no real credentials or network calls."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from predash.kiwoom import Kiwoom, BrokerError, KST, response_codes
from predash.trades import normalize_kiwoom, TradeDataError


def response(data, headers=None, status=200):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: data)


def ok(**data):
    return {'return_code': 0, **data}


def account_row(**changes):
    return {'stk_cd': 'A005930', 'stk_nm': '예시주식', 'rmnd_qty': '0000000003',
            'cur_prc': '-000059000', 'pur_amt': '0000000180000',
            'evlt_amt': '0000000177000', 'evltv_prft': '-00000003000', **changes}


def fill_row(**changes):
    return {'_query_date': '20260930', 'ord_no': '0000050', 'stk_cd': 'A069500',
            'io_tp_nm': '현금매수', 'trde_tp': '시장가', 'ord_tm': '13:05:43',
            'cntr_qty': '0000000002', 'cntr_uv': '0000004900', 'stk_nm': '예시ETF',
            'mdfy_cncl': '', **changes}


class KiwoomContracts(unittest.TestCase):
    def setUp(self):
        self.client = Kiwoom(settings={'mode': 'demo', 'key': 'dummy-key', 'secret': 'dummy-secret'})
        self.sleep = patch('predash.kiwoom.time.sleep').start()
        self.addCleanup(patch.stopall)

    def ready(self):
        self.client.token = 'dummy-token'
        self.client.expires = float('inf')

    def test_real_demo_hosts_and_invalid_modes(self):
        self.assertEqual(self.client.base, 'https://mockapi.kiwoom.com')
        real = Kiwoom(settings={'mode': 'real', 'key': 'other-key', 'secret': 'other-secret'})
        self.assertEqual(real.base, 'https://api.kiwoom.com')
        with self.assertRaises(BrokerError):
            Kiwoom('real', settings={'mode': 'demo', 'key': 'x', 'secret': 'y'})
        with self.assertRaises(BrokerError):
            Kiwoom(settings={'mode': 'demo'})

    @patch('predash.kiwoom.requests.post')
    def test_token_contract_and_reuse(self, post):
        expires = (datetime.now(KST)+timedelta(hours=2)).strftime('%Y%m%d%H%M%S')
        post.return_value = response(ok(token='dummy-token', expires_dt=expires))
        self.client.authorize(); self.client.authorize()
        self.assertEqual(post.call_count, 1)
        args = post.call_args
        self.assertEqual(args.args[0], 'https://mockapi.kiwoom.com/oauth2/token')
        self.assertEqual(args.kwargs['json'], {'grant_type': 'client_credentials', 'appkey': 'dummy-key', 'secretkey': 'dummy-secret'})
        self.assertFalse(args.kwargs['allow_redirects'])
        self.assertNotIn('authorization', args.kwargs['headers'])

    @patch('predash.kiwoom.requests.post')
    def test_order_api_rejected_before_network(self, post):
        with self.assertRaises(BrokerError):self.client._post('kt10000', {})
        post.assert_not_called()

    @patch('predash.kiwoom.requests.post')
    def test_errors_never_echo_keys_and_mock_does_not_fallback(self, post):
        self.ready()
        post.return_value = response({'return_code': 8104, 'return_msg': 'dummy-secret dummy-key'})
        with self.assertRaisesRegex(BrokerError, '모의투자') as caught:
            self.client.balance()
        self.assertNotIn('dummy-secret', str(caught.exception))
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.args[0].startswith('https://mockapi.kiwoom.com/'))

    def test_ip_and_environment_diagnostics(self):
        self.assertIn('IP', str(Kiwoom.result_error(8010)))
        self.assertIn('실전·모의', str(Kiwoom.result_error(8030)))

    @patch('predash.kiwoom.requests.post')
    def test_wrapped_auth_error_identifies_ip_and_stage_without_echoing_message(self, post):
        post.return_value = response({'return_code': 3,
            'return_msg': '인증에 실패했습니다[8010:dummy-key dummy-secret 12345678] <script>bad</script>'})
        with self.assertRaises(BrokerError) as caught:self.client.authorize()
        message=str(caught.exception)
        for expected in ('응답 3 / 세부 8010', '모의투자', '토큰 발급', 'IP'):
            self.assertIn(expected, message)
        for private in ('dummy-key', 'dummy-secret', '12345678', '<script>'):
            self.assertNotIn(private, message)

    @patch('predash.kiwoom.requests.post')
    def test_wrapped_balance_error_identifies_environment(self, post):
        self.ready()
        post.return_value = response({'return_code': '3', 'return_msg': '거부[8030:private-message]'})
        with self.assertRaisesRegex(BrokerError, '세부 8030.*잔고 조회.*실전·모의'):
            self.client.balance()

    def test_generic_auth_failure_does_not_invent_a_specific_cause(self):
        message=str(Kiwoom.result_error(3, 'private-message'))
        self.assertIn('인증에 실패', message)
        self.assertIn('원인을 확정할 수 없습니다', message)
        self.assertNotIn('private-message', message)

    def test_only_known_embedded_codes_are_displayed(self):
        self.assertEqual(response_codes(3, '[12345:private-account]'), ('3','3'))
        self.assertEqual(response_codes(3, 'CODE=8011 private-secret'), ('3','8011'))
        self.assertEqual(response_codes(8030, '[8010:conflicting-message]'), ('8030','8030'))
        self.assertEqual(response_codes(False), ('미확인','미확인'))

    @patch('predash.kiwoom.requests.post')
    def test_zero_padded_success_code_is_accepted(self, post):
        self.ready()
        post.return_value=response({'return_code':'0000','acnt_evlt_remn_indv_tot':[]})
        _, data=self.client._post('kt00018', {})
        self.assertEqual(data['return_code'], '0000')

    @patch('predash.kiwoom.requests.post')
    def test_wrapped_rate_limit_retries_same_read_only_request(self, post):
        self.ready()
        post.side_effect=[response({'return_code':5,'return_msg':'실패[1700:private-message]'}),
                          response(ok(acnt_evlt_remn_indv_tot=[]))]
        self.client._post('kt00018', {})
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['headers']['api-id'], 'kt00018')
            self.assertTrue(call.args[0].startswith('https://mockapi.kiwoom.com/'))

    @patch('predash.kiwoom.requests.post')
    def test_balance_pagination_and_credit_lot_aggregation(self, post):
        self.ready()
        post.side_effect = [
            response(ok(acnt_evlt_remn_indv_tot=[account_row()]), {'cont-yn': 'Y', 'next-key': 'page-2'}),
            response(ok(acnt_evlt_remn_indv_tot=[account_row(rmnd_qty='2', pur_amt='118000', evlt_amt='118000', evltv_prft='0')])),
            response(ok(entr='00000100000'))]
        result = self.client.balance()
        self.assertEqual(len(result['positions']), 1)
        p = result['positions'][0]
        self.assertEqual((p['quantity'], p['value'], p['pnl'], p['average_cost']), (5, 295000, -3000, 59600))
        self.assertEqual(p['price'], 59000)
        self.assertEqual(p['weight'], 100)
        self.assertEqual(result['cash'], 100000)
        self.assertEqual(post.call_args_list[1].kwargs['headers']['next-key'], 'page-2')
        for call in post.call_args_list:
            self.assertNotIn('appkey', call.kwargs['json'])
            self.assertNotIn('secretkey', call.kwargs['json'])

    @patch('predash.kiwoom.requests.post')
    def test_cash_failure_is_visible_and_not_zero(self, post):
        self.ready()
        post.side_effect = [response(ok(acnt_evlt_remn_indv_tot=[])), response({'return_code': 8104})]
        result = self.client.balance()
        self.assertIsNone(result['cash'])
        self.assertTrue(result['cash_error'])

    @patch('predash.kiwoom.requests.post')
    def test_pagination_cycle_refuses_partial_account(self, post):
        self.ready()
        post.return_value = response(ok(acnt_evlt_remn_indv_tot=[account_row()]), {'cont-yn': 'Y', 'next-key': 'same'})
        with self.assertRaisesRegex(BrokerError, '연속조회'):self.client.balance()
        self.assertEqual(post.call_count, 2)

    @patch('predash.kiwoom.requests.post')
    def test_failed_second_page_refuses_partial_account(self, post):
        self.ready()
        post.side_effect = [response(ok(acnt_evlt_remn_indv_tot=[account_row()]), {'cont-yn': 'Y', 'next-key': 'next'}), response({'return_code': 8005})]
        with self.assertRaises(BrokerError):self.client.balance()

    @patch('predash.kiwoom.requests.post')
    def test_nonfinite_account_values_are_rejected(self, post):
        self.ready()
        post.return_value = response(ok(acnt_evlt_remn_indv_tot=[account_row(evlt_amt='NaN')]))
        with self.assertRaises(BrokerError):self.client.balance()

    @patch('predash.kiwoom.requests.post')
    def test_official_integer_index_scale_and_market_mapping(self, post):
        self.ready()
        post.return_value = response(ok(inds_dt_pole_qry=[{'dt': '20250210', 'cur_prc': '252127'}]))
        result = self.client.index_bars('0001', date(2025,2,10))
        self.assertEqual(result[0]['bstp_nmix_prpr'], 2521.27)
        self.assertEqual(post.call_args.kwargs['json']['inds_cd'], '001')
        self.client.index_bars('1001', date(2025,2,10))
        self.assertEqual(post.call_args.kwargs['json']['inds_cd'], '101')

    @patch('predash.kiwoom.requests.post')
    def test_original_prices_end_before_purchase(self, post):
        self.ready()
        post.return_value = response(ok(stk_dt_pole_chart_qry=[{'dt': '20260929', 'high_pric': '+63000', 'low_pric': '-61000'}]))
        bars = self.client.daily_bars('005930', date(2026,9,30))
        self.assertEqual(post.call_args.kwargs['json'], {'stk_cd':'005930', 'base_dt':'20260929', 'upd_stkpc_tp':'0'})
        self.assertEqual((bars[0]['stck_hgpr'], bars[0]['stck_lwpr']), (63000,61000))

    @patch('predash.kiwoom.requests.post')
    def test_future_candle_rejected(self, post):
        self.ready()
        post.return_value = response(ok(stk_dt_pole_chart_qry=[{'dt': '20260930', 'high_pric': '63000', 'low_pric': '61000'}]))
        with self.assertRaisesRegex(BrokerError, '기준일 이후'):
            self.client.daily_bars('005930', date(2026,9,30))

    @patch('predash.kiwoom.requests.post')
    def test_stock_flow_uses_shares_not_money(self, post):
        self.ready()
        today=datetime.now(KST).date()
        day=today-timedelta(days=1)
        post.return_value = response(ok(stk_invsr_orgn=[{'dt':day.strftime('%Y%m%d'), 'ind_invsr':'-5','frgnr_invsr':'+7','orgn':'-2'}]))
        with patch('predash.kiwoom.closed_day',return_value=day):result=self.client.investor_flow('005930')
        self.assertEqual(result['daily'], {'individual':-5,'foreign':7,'institution':-2})
        body=post.call_args.kwargs['json']
        self.assertEqual((body['amt_qty_tp'],body['unit_tp'],body['trde_tp']),('2','1','0'))
        self.assertEqual(result['unit'],'주')

    @patch('predash.kiwoom.requests.post')
    def test_market_flow_uses_aggregate_and_explicit_date_basis(self, post):
        self.ready()
        today=datetime.now(KST).date()
        post.return_value=response(ok(inds_netprps=[
            {'inds_cd':'002','ind_netprps':'999','orgn_netprps':'999','frgnr_netprps':'999'},
            {'inds_cd':'001','ind_netprps':'-25','orgn_netprps':'10','frgnr_netprps':'15'}]))
        with patch.object(self.client,'index_bars',return_value=[{'stck_bsop_date':today.strftime('%Y%m%d')}]):
            result=self.client.market_flow('0001')
        self.assertEqual(result['net'],{'individual':-25,'institution':10,'foreign':15})
        self.assertEqual((result['unit'],result['date_basis']),('억원','요청 기준일'))
        self.assertEqual(post.call_args.kwargs['json']['base_dt'],today.strftime('%Y%m%d'))

    @patch('predash.kiwoom.requests.post')
    def test_fills_are_read_by_explicit_order_date(self, post):
        self.ready()
        post.return_value=response(ok(acnt_ord_cntr_prps_dtl=[]))
        result=self.client.fills(7)
        self.assertEqual(result['rows'],[])
        calls=post.call_args_list
        self.assertTrue(calls)
        dates=[]
        for call in calls:
            self.assertEqual(call.kwargs['headers']['api-id'],'kt00007')
            self.assertEqual(call.kwargs['json']['qry_tp'],'4')
            self.assertEqual(call.kwargs['json']['dmst_stex_tp'],'KRX')
            dates.append(call.kwargs['json']['ord_dt'])
        self.assertEqual(len(dates),len(set(dates)))


class KiwoomExecutionNormalization(unittest.TestCase):
    def test_official_order_type_is_not_trade_direction(self):
        fills=normalize_kiwoom([fill_row()])
        self.assertEqual((fills[0]['side'],fills[0]['quantity'],fills[0]['price'],fills[0]['amount']),('buy',2,4900,9800))
        self.assertEqual(fills[0]['at'],datetime(2026,9,30,13,5,43))

    def test_zero_padded_unfilled_order_ignored(self):
        self.assertEqual(normalize_kiwoom([fill_row(cntr_qty='0000000000')]),[])

    def test_corrections_and_conflicting_partial_fills_are_not_double_counted(self):
        with self.assertRaises(TradeDataError):normalize_kiwoom([fill_row(mdfy_cncl='정정')])
        with self.assertRaises(TradeDataError):normalize_kiwoom([fill_row(),fill_row(cntr_qty='3')])

    def test_identical_order_deduplicated(self):
        self.assertEqual(len(normalize_kiwoom([fill_row(),fill_row()])),1)

    def test_bad_codes_are_explicitly_excluded(self):
        skipped=[]
        self.assertEqual(normalize_kiwoom([fill_row(stk_cd='invalid')],skipped=skipped),[])
        self.assertEqual(len(skipped),1)


if __name__ == '__main__':unittest.main()
