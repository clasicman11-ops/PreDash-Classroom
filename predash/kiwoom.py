"""Session-only, read-only Kiwoom REST adapter; see docs/KIWOOM.md.

Endpoint/field contracts are from Kiwoom-Securities/Kiwoom-REST-API,
revision 953e5dbff123f437ab4d11a78a95191a685eb51f (2026-10-02).
No order TR is accepted, including when using the mock server.
"""
from datetime import date, datetime, timedelta
from math import isfinite
import re
import time
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo('Asia/Seoul')
CLIENT_SCHEMA = 2
READ_APIS = {
    'au10001': '/oauth2/token',
    'kt00018': '/api/dostk/acnt',
    'kt00001': '/api/dostk/acnt',
    'kt00007': '/api/dostk/acnt',
    'ka10059': '/api/dostk/stkinfo',
    'ka10051': '/api/dostk/sect',
    'ka10081': '/api/dostk/chart',
    'ka20006': '/api/dostk/chart',
}
INDEX_CODES = {'0001': '001', '1001': '101'}
KNOWN_ERROR_CODES = frozenset({
    1501, 1504, 1505, 1511, 1512, 1513, 1514, 1515, 1516, 1517, 1687,
    1700, 1701, 1702, 1901, 1902, 1903,
    8001, 8002, 8003, 8005, 8006, 8009, 8010, 8011, 8012, 8015, 8016,
    8020, 8030, 8031, 8040, 8050, 8103, 8104,
})


def response_codes(value, message=''):
    """Read only numeric error codes; never expose provider message contents.

    Kiwoom can wrap a specific error in return_code=3 and return_msg=[8010:...].
    Unknown message numbers are not treated as codes or shown to the user.
    """
    raw = str(value).strip()
    top = str(int(raw)) if not isinstance(value, bool) and re.fullmatch(r'-?\d{1,6}', raw) else '미확인'
    specific = top
    if top != '0' and top not in {str(code) for code in KNOWN_ERROR_CODES}:
        for match in re.finditer(r'\[(\d{3,5}):|CODE=(\d{3,5})(?!\d)', str(message or '')):
            code = int(match.group(1) or match.group(2))
            if code in KNOWN_ERROR_CODES:
                specific = str(code)
                break
    return top, specific


class BrokerError(RuntimeError):
    """Sanitized error that can safely be displayed in the app."""


def number(value, label='숫자'):
    try:
        result = float(str(value).strip().replace(',', ''))
        if not isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise BrokerError(f'키움 {label} 응답을 확인하지 못했습니다.') from None


def stock_code(value):
    code = str(value).strip().upper()
    if re.fullmatch(r'A[0-9A-Z]{6}', code):
        code = code[1:]
    if not re.fullmatch(r'(?:[0-9A-Z]{6}|[JQ][0-9]{6})', code):
        raise BrokerError('키움 종목코드를 확인하지 못했습니다.')
    return code


def parse_day(value):
    try:
        return datetime.strptime(str(value), '%Y%m%d').date()
    except (ValueError, TypeError):
        raise BrokerError('키움 자료의 기준일을 확인하지 못했습니다.') from None


def closed_day():
    """Do not label the current intraday candle as a completed daily close."""
    now = datetime.now(KST)
    return now.date() if now.hour >= 18 else now.date() - timedelta(days=1)


def validate_day(value):
    if type(value) is not date or value > datetime.now(KST).date():
        raise BrokerError('조회 기준일을 확인하세요.')
    return value


class Kiwoom:
    def __init__(self, mode=None, *, settings=None):
        settings = settings or {}
        self.mode = mode or settings.get('mode', 'demo')
        if self.mode not in ('real', 'demo') or settings.get('mode', self.mode) != self.mode:
            raise BrokerError('키움 실전·모의 환경이 일치하지 않습니다.')
        self.key = str(settings.get('key', '')).strip()
        self.secret = str(settings.get('secret', '')).strip()
        if not self.key or not self.secret:
            raise BrokerError('데이터 연결에서 해당 환경의 키움 App Key와 App Secret을 입력하세요.')
        self.base = 'https://api.kiwoom.com' if self.mode == 'real' else 'https://mockapi.kiwoom.com'
        self.token = None
        self.expires = 0
        self._last_request = 0.0

    @staticmethod
    def result_error(code, message='', *, api_id=None, mode=None):
        top, code = response_codes(code, message)
        if code in ('1700', '1701', '1702'):
            detail = '호출 한도 초과 · 잠시 후 다시 조회하세요.'
        elif code in ('8010', '8040', '8050', '8103'):
            detail = '앱 서버 IP와 키움 허용 IP 등록을 확인하세요. PC IP와 앱 서버 IP는 다릅니다.'
        elif code in ('8030', '8031'):
            detail = '실전·모의 구분과 해당 환경에서 발급한 키가 일치하는지 확인하세요.'
        elif code == '8104':
            detail = '모의투자에서 지원하지 않는 조회입니다. 실전 키로 자동 전환하지 않습니다.'
        elif code in ('8001', '8002', '8011', '8012', '8020'):
            detail = '해당 환경의 REST API App Key·App Secret과 사용 승인을 확인하세요. Windows OpenAPI+ 키와는 다릅니다.'
        elif code in ('8003', '8005', '8006', '8009', '8015', '8016'):
            detail = '접근토큰이 유효하지 않습니다. 키움 연결을 해제한 뒤 해당 환경의 키로 다시 연결하세요.'
        elif code == '3':
            detail = '인증에 실패했습니다. 세부 코드가 없어 원인을 확정할 수 없습니다. REST API 키·실전/모의 구분·앱 서버 허용 IP를 확인하세요.'
        elif code in ('1501', '1504', '1505', '1511', '1512', '1513', '1514', '1515', '1516', '1517', '1687'):
            detail = '조회 요청의 필수값 또는 형식이 거부됐습니다. 표시된 조회 단계와 코드를 확인하세요.'
        elif code in ('1901', '1902', '1903'):
            detail = '종목코드 또는 거래소 구분을 확인하세요.'
        else:
            detail = '요청 권한·조회기간·키움 서비스 상태를 확인하세요.'
        label = top + (f' / 세부 {code}' if code != top else '')
        stages = {'au10001': '토큰 발급', 'kt00018': '잔고 조회', 'kt00001': '예수금 조회',
                  'kt00007': '체결 조회', 'ka10059': '종목 수급', 'ka10051': '시장 수급',
                  'ka10081': '종목 차트', 'ka20006': '지수 차트'}
        context = ' · '.join(part for part in (
            {'real': '실전', 'demo': '모의투자'}.get(mode), stages.get(api_id)) if part)
        return BrokerError(f'키움 응답 {label}' + (f' · {context}' if context else '') + f' · {detail}')

    def _post(self, api_id, body, *, cont_yn='N', next_key=''):
        if api_id not in READ_APIS:
            raise BrokerError('허용되지 않은 요청입니다. 이 앱은 조회 전용입니다.')
        headers = {'Content-Type': 'application/json;charset=UTF-8', 'api-id': api_id}
        if api_id != 'au10001':
            headers.update({'authorization': 'Bearer ' + str(self.token),
                            'cont-yn': cont_yn, 'next-key': next_key})
        for attempt in range(3):
            delay = 0.6 - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            try:
                response = requests.post(self.base + READ_APIS[api_id], headers=headers,
                                         json=body, timeout=(5, 25), allow_redirects=False)
                if response.status_code == 429:
                    if attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    raise BrokerError('키움 호출 한도 초과 · 잠시 후 다시 조회하세요.')
                if response.status_code != 200:
                    raise BrokerError(f'키움 연결 실패 (HTTP {response.status_code}) · 투자 환경·허용 IP·서비스 상태를 확인하세요.')
                data = response.json()
            except (requests.RequestException, ValueError):
                raise BrokerError('키움 서버에 연결하지 못했습니다. 네트워크와 서버 IP 등록을 확인하세요.') from None
            if not isinstance(data, dict) or 'return_code' not in data:
                raise BrokerError('키움 응답 형식을 확인하지 못했습니다.')
            top, code = response_codes(data['return_code'], data.get('return_msg'))
            if code in ('1700', '1701', '1702') and attempt < 2:
                time.sleep(1 + attempt)
                continue
            if top != '0':
                # Do not display return_msg: a provider message can echo credentials.
                raise self.result_error(data['return_code'], data.get('return_msg'),
                                        api_id=api_id, mode=self.mode)
            return response, data
        raise BrokerError('키움 조회를 완료하지 못했습니다.')

    def authorize(self):
        if self.token and time.time() < self.expires:
            return
        self.token = None
        _, data = self._post('au10001', {'grant_type': 'client_credentials',
                                        'appkey': self.key, 'secretkey': self.secret})
        try:
            expires = datetime.strptime(str(data['expires_dt']), '%Y%m%d%H%M%S').replace(tzinfo=KST).timestamp() - 120
            token = data['token']
            if not isinstance(token, str) or not token or expires <= time.time():
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise BrokerError('키움 토큰 또는 만료일을 확인하지 못했습니다.') from None
        self.token, self.expires = token, expires

    def _pages(self, api_id, body, rows_key):
        cont, cursor, seen = 'N', '', set()
        for _ in range(100):
            self.authorize()
            response, data = self._post(api_id, body, cont_yn=cont, next_key=cursor)
            rows = data.get(rows_key)
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise BrokerError(f'키움 {api_id} 목록 응답을 확인하지 못했습니다.')
            next_cont = str(response.headers.get('cont-yn', 'N')).upper()
            next_cursor = str(response.headers.get('next-key', '')).strip()
            if next_cont not in ('Y', 'N', ''):
                raise BrokerError('키움 연속조회 상태를 확인하지 못했습니다.')
            if next_cont == 'Y' and (not next_cursor or next_cursor in seen):
                raise BrokerError('키움 연속조회가 반복되거나 중단되었습니다. 일부 결과는 표시하지 않습니다.')
            yield data, rows
            if next_cont != 'Y':
                return
            seen.add(next_cursor)
            cont, cursor = 'Y', next_cursor
        raise BrokerError('키움 조회 페이지 한도를 초과했습니다. 일부 결과는 표시하지 않습니다.')

    def balance(self):
        positions = {}
        for _, rows in self._pages('kt00018', {'qry_tp': '1', 'dmst_stex_tp': 'KRX'}, 'acnt_evlt_remn_indv_tot'):
            for row in rows:
                quantity = number(row.get('rmnd_qty'), '보유수량')
                if quantity < 0:
                    raise BrokerError('음수 잔고는 이 화면에서 지원하지 않습니다.')
                if quantity == 0:
                    continue
                code = stock_code(row.get('stk_cd'))
                price = abs(number(row.get('cur_prc'), '현재가'))
                cost = number(row.get('pur_amt'), '매입금액')
                value = number(row.get('evlt_amt'), '평가금액')
                pnl = number(row.get('evltv_prft'), '평가손익')
                if min(price, cost, value) < 0:
                    raise BrokerError('키움 잔고 금액을 확인하지 못했습니다.')
                item = positions.setdefault(code, {'code': code, 'name': str(row.get('stk_nm') or code),
                    'quantity': 0, 'cost': 0, 'value': 0, 'pnl': 0, 'price': price})
                if item['price'] != price:
                    raise BrokerError('동일 종목 잔고의 현재가가 달라 집계를 보류합니다.')
                item['quantity'] += quantity
                item['cost'] += cost
                item['value'] += value
                item['pnl'] += pnl
        items = list(positions.values())
        total = sum(item['value'] for item in items)
        for item in items:
            item['average_cost'] = item.pop('cost') / item['quantity']
            item['weight'] = item['value'] / total * 100 if total else 0
        cash, cash_error = None, ''
        try:
            self.authorize()
            _, deposit = self._post('kt00001', {'qry_tp': '2'})
            cash = number(deposit.get('entr'), '예수금')
        except BrokerError as exc:
            cash_error = str(exc)
        return {'positions': items, 'value': total, 'pnl': sum(p['pnl'] for p in items),
                'cash': cash, 'cash_error': cash_error, 'mode': self.mode,
                'source': '키움증권 · KRX 평가 기준', 'fetched': datetime.now(KST).isoformat()}

    def fills(self, days=30):
        if type(days) is not int or not 1 <= days <= 90:
            raise BrokerError('체결 조회기간은 1~90일 사이로 선택하세요.')
        end = datetime.now(KST).date()
        start = end - timedelta(days=days-1)
        rows = []
        for offset in range(days):
            day = end - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            body = {'ord_dt': day.strftime('%Y%m%d'), 'qry_tp': '4', 'stk_bond_tp': '1',
                    'sell_tp': '0', 'stk_cd': '', 'fr_ord_no': '',
                    'dmst_stex_tp': 'KRX' if self.mode == 'demo' else '%'}
            for _, records in self._pages('kt00007', body, 'acnt_ord_cntr_prps_dtl'):
                rows.extend({**r, '_query_date': body['ord_dt']} for r in records)
        return {'rows': rows, 'from': start.isoformat(), 'to': end.isoformat(),
                'fetched': datetime.now(KST).isoformat(), 'mode': self.mode}

    def _history(self, api_id, body, rows_key, start, end):
        points = {}
        for _, rows in self._pages(api_id, body, rows_key):
            dates = []
            for row in rows:
                day = parse_day(row.get('dt'))
                if day > end:
                    raise BrokerError('요청 기준일 이후 자료가 섞여 조회를 보류합니다.')
                dates.append(day)
                if day < start:
                    continue
                if day in points and points[day] != row:
                    raise BrokerError('같은 날짜의 키움 응답이 서로 다릅니다.')
                points[day] = row
            if dates != sorted(dates, reverse=True):
                raise BrokerError('키움 일별 자료의 날짜 순서를 확인하지 못했습니다.')
            if dates and min(dates) <= start:
                break
        return [points[day] for day in sorted(points, reverse=True)]

    def investor_flow(self, code):
        code = stock_code(code)
        end = closed_day()
        rows = self._history('ka10059', {'dt': end.strftime('%Y%m%d'), 'stk_cd': code,
            'amt_qty_tp': '2', 'trde_tp': '0', 'unit_tp': '1'}, 'stk_invsr_orgn', end-timedelta(days=60), end)
        normalized = []
        for row in rows:
            values = [number(row.get(k), '순매수 수량') for k in ('ind_invsr','frgnr_invsr','orgn')]
            if any(v != int(v) for v in values):
                raise BrokerError('순매수 수량의 단위를 확인하지 못했습니다.')
            normalized.append(dict(zip(('stck_bsop_date','prsn_ntby_qty','frgn_ntby_qty','orgn_ntby_qty'),
                                       [row['dt'], *map(int, values)])))
        from predash.flow import summarize_flow
        result = summarize_flow(normalized, datetime.now(KST).date())
        if result:
            result.update(source='키움증권 ka10059 · KRX', unit='주', mode=self.mode)
        return result

    def daily_bars(self, code, end_day):
        end = validate_day(end_day) - timedelta(days=1)
        rows = self._history('ka10081', {'stk_cd': stock_code(code), 'base_dt': end.strftime('%Y%m%d'),
            'upd_stkpc_tp': '0'}, 'stk_dt_pole_chart_qry', end-timedelta(days=49), end)
        return [{'stck_bsop_date': r['dt'], 'stck_hgpr': abs(number(r.get('high_pric'), '고가')),
                 'stck_lwpr': abs(number(r.get('low_pric'), '저가'))} for r in rows]

    def index_bars(self, code, as_of=None):
        if code not in INDEX_CODES:
            raise BrokerError('지원하지 않는 시장입니다.')
        end = validate_day(as_of) if as_of is not None else closed_day()
        rows = self._history('ka20006', {'inds_cd': INDEX_CODES[code], 'base_dt': end.strftime('%Y%m%d')},
                             'inds_dt_pole_qry', end-timedelta(days=70), end)
        # Official spec: integer value is the index multiplied by 100.
        return [{'stck_bsop_date': r['dt'], 'bstp_nmix_prpr': abs(number(r.get('cur_prc'), '지수')) / 100}
                for r in rows]

    def market_flow(self, code, as_of=None):
        if code not in INDEX_CODES:
            raise BrokerError('지원하지 않는 시장입니다.')
        bars = self.index_bars(code, as_of)
        if not bars:
            raise BrokerError('시장 수급의 조회 기준 거래일을 확인하지 못했습니다.')
        day = max(parse_day(r['stck_bsop_date']) for r in bars)
        if (datetime.now(KST).date()-day).days > 5:
            raise BrokerError('시장 수급 조회 기준일이 오래됐습니다.')
        body = {'mrkt_tp': '0' if code == '0001' else '1', 'amt_qty_tp': '0',
                'base_dt': day.strftime('%Y%m%d'), 'stex_tp': '1'}
        matches = []
        for _, rows in self._pages('ka10051', body, 'inds_netprps'):
            matches.extend(r for r in rows if str(r.get('inds_cd')) == INDEX_CODES[code])
        if len(matches) != 1:
            raise BrokerError('시장 전체의 순매수 행을 확인하지 못했습니다.')
        row = matches[0]
        net = {name: number(row.get(field), '시장 순매수 대금') for name, field in (
            ('individual','ind_netprps'),('institution','orgn_netprps'),('foreign','frgnr_netprps'))}
        # ka10051 has no response date; explicitly retain the requested date basis.
        return {'date': day.isoformat(), 'date_basis': '요청 기준일', 'net': net, 'unit': '억원',
                'source': '키움증권 ka10051 · KRX', 'mode': self.mode}
