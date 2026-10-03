"""Normalize confirmed fills and match closed trades without inventing opening holdings."""
from collections import defaultdict, deque
from datetime import datetime
from math import isfinite
import re

class TradeDataError(ValueError):
    pass

def _positive(value, label):
    try:
        v=float(str(value).replace(',',''))
        if not isfinite(v) or v<=0: raise ValueError
        return v
    except (TypeError,ValueError):
        raise TradeDataError(f'{label} 값이 올바르지 않습니다.') from None

def normalize_kis(rows, skipped=None):
    """Only actual, non-cancelled executions; one KIS row per order aggregate."""
    fills=[];seen={}
    for index,row in enumerate(rows):
        if str(row.get('cncl_yn','N')).upper() in ('Y','1'): continue
        raw_qty=row.get('tot_ccld_qty')
        if raw_qty in (None,'','0','0.0'): continue
        side={'01':'sell','02':'buy'}.get(str(row.get('sll_buy_dvsn_cd','')))
        if not side: raise TradeDataError('매수·매도 구분을 확인할 수 없습니다.')
        code=str(row.get('pdno','')).strip().upper()
        # Domestic short codes can contain letters (e.g. preferred shares).
        # A is a provider prefix only for a seven-character code.
        if re.fullmatch(r'A[0-9A-Z]{6}',code):code=code[1:]
        day=str(row.get('ord_dt','')).strip()
        clock=str(row.get('ord_tmd','000000')).strip().zfill(6)
        if not re.fullmatch(r'(?:[0-9A-Z]{6}|Q[0-9]{6})',code):
            if skipped is not None:
                skipped.append({'row':index+1,'reason':'종목코드 미확인 · 표시 제외'})
                continue
            raise TradeDataError('종목코드 형식이 올바르지 않습니다.')
        try: at=datetime.strptime(day+clock,'%Y%m%d%H%M%S')
        except ValueError: raise TradeDataError('체결 일시를 확인할 수 없습니다.') from None
        qty=_positive(raw_qty,'체결수량')
        price=_positive(row.get('avg_prvs'),'체결 평균가')
        raw_amount=row.get('tot_ccld_amt')
        amount=_positive(raw_amount,'체결금액') if raw_amount not in (None,'') else qty*price
        branch=str(row.get('ord_gno_brno','')).strip();order=str(row.get('odno','')).strip()
        if not order: raise TradeDataError('주문번호가 없어 체결 중복을 판별할 수 없습니다.')
        unique=(day,branch,order,code,side)
        if unique in seen:
            if seen[unique] != (qty,price,amount):
                raise TradeDataError('같은 주문번호에 다른 체결 합계가 있어 중복 계산을 중단했습니다.')
            continue
        seen[unique]=(qty,price,amount)
        fills.append({'at':at,'code':code,'name':str(row.get('prdt_name') or code),'side':side,
                      'quantity':qty,'price':price,'amount':amount,'source':'KIS 체결'})
    return sorted(fills,key=lambda x:(x['at'],x['code'],0 if x['side']=='buy' else 1))

def normalize_kiwoom(rows, skipped=None):
    """kt00007 executions; order timestamps, not execution timestamps.

    Conflicting partial executions/corrections are never silently collapsed.
    """
    fills=[];seen={}
    for index,row in enumerate(rows):
        try:
            qty=float(str(row.get('cntr_qty')).replace(',',''))
            if not isfinite(qty) or qty<0:raise ValueError
        except (TypeError,ValueError):
            raise TradeDataError('키움 체결수량을 확인할 수 없습니다.') from None
        if qty==0:continue
        side_text=str(row.get('io_tp_nm',''))
        is_buy,is_sell='매수' in side_text,'매도' in side_text
        if is_buy==is_sell:raise TradeDataError('키움 매수·매도 구분을 확인할 수 없습니다.')
        correction=str(row.get('mdfy_cncl',''))+' '+side_text
        if '정정' in correction or '취소' in correction:
            raise TradeDataError('정정·취소 주문의 체결 합계는 중복 확인이 필요해 분석을 보류합니다.')
        side='buy' if is_buy else 'sell'
        code=str(row.get('stk_cd','')).strip().upper()
        if re.fullmatch(r'A[0-9A-Z]{6}',code):code=code[1:]
        if not re.fullmatch(r'(?:[0-9A-Z]{6}|[JQ][0-9]{6})',code):
            if skipped is not None:
                skipped.append({'row':index+1,'reason':'종목코드 미확인 · 표시 제외'})
                continue
            raise TradeDataError('키움 종목코드 형식이 올바르지 않습니다.')
        day=str(row.get('_query_date',''))
        clock=str(row.get('ord_tm','')).replace(':','')
        try:at=datetime.strptime(day+clock,'%Y%m%d%H%M%S')
        except ValueError:raise TradeDataError('키움 주문일시를 확인할 수 없습니다.') from None
        price=_positive(row.get('cntr_uv'),'키움 체결단가')
        order=str(row.get('ord_no','')).strip()
        if not order:raise TradeDataError('키움 주문번호가 없어 체결 중복을 확인할 수 없습니다.')
        unique=(day,order,code,side)
        amounts=(qty,price,qty*price)
        if unique in seen:
            if seen[unique]!=amounts:
                raise TradeDataError('같은 키움 주문에 다른 체결 수량·가격이 있어 합계 계산을 보류합니다.')
            continue
        seen[unique]=amounts
        fills.append({'at':at,'code':code,'name':str(row.get('stk_nm') or code),'side':side,
                      'quantity':qty,'price':price,'amount':qty*price,
                      'source':'키움증권 kt00007 · 주문시간 기준'})
    return sorted(fills,key=lambda x:(x['at'],x['code'],0 if x['side']=='buy' else 1))

def closed_trades(fills):
    """FIFO before fees/taxes. Sells lacking imported purchase inventory stay unmatched."""
    inventory=defaultdict(deque);closed=[];unmatched=[]
    for f in fills:
        code=f['code']
        if f['side']=='buy':
            inventory[code].append({'at':f['at'],'remaining':f['quantity'],'price':f['price']})
            continue
        left=f['quantity']
        while left>1e-9 and inventory[code]:
            lot=inventory[code][0];qty=min(left,lot['remaining'])
            closed.append({'code':code,'name':f['name'],'buy_at':lot['at'],'sell_at':f['at'],
                           'quantity':qty,'buy_price':lot['price'],'sell_price':f['price'],
                           'gross_pnl':(f['price']-lot['price'])*qty,'fees_included':False})
            left-=qty;lot['remaining']-=qty
            if lot['remaining']<1e-9: inventory[code].popleft()
        if left>1e-9: unmatched.append({'code':code,'at':f['at'],'quantity':left,'reason':'조회 기간 이전 매수 또는 이관 가능'})
    return {'closed':closed,'unmatched_sells':unmatched,
            'open_lots':sum(len(q) for q in inventory.values()),'basis':'FIFO · 수수료/세금 미반영'}

def purchase_range(fill, raw_bars, window=20):
    """Position of execution price within the preceding complete sessions' original high/low."""
    if fill['side']!='buy': raise TradeDataError('매수 체결만 가격 위치를 계산합니다.')
    cutoff=fill['at'].date()
    bars=[];seen=set()
    for row in raw_bars:
        try:
            day=datetime.strptime(str(row['stck_bsop_date']), '%Y%m%d').date()
            high=_positive(row['stck_hgpr'],'당시 고가')
            low=_positive(row['stck_lwpr'],'당시 저가')
        except (KeyError,TradeDataError,ValueError):
            raise TradeDataError('당시 시세 응답을 검증하지 못했습니다.') from None
        if day>=cutoff: raise TradeDataError('매수 당일 또는 이후 시세가 섞여 판정을 보류합니다.')
        if day in seen or high<low: raise TradeDataError('당시 시세에 중복 날짜 또는 잘못된 범위가 있습니다.')
        seen.add(day);bars.append((day,low,high))
    bars.sort(reverse=True)
    if len(bars)<window: return None
    selected=bars[:window]
    low=min(bar[1] for bar in selected);high=max(bar[2] for bar in selected)
    if high<=low: return None
    price=_positive(fill['price'],'매수가')
    # Extreme discontinuities may signal corporate actions; historical execution
    # prices cannot be compared safely across them without action records.
    if max(bar[2] for bar in selected)/min(bar[1] for bar in selected)>3:
        return None
    position=(price-low)/(high-low)*100
    return {'code':fill['code'],'name':fill['name'],'at':fill['at'],
            'price':price,'range_low':low,'range_high':high,'position_pct':position,
            'upper_20':position>=80,'sessions':window,
            'from':selected[-1][0].isoformat(),'to':selected[0][0].isoformat()}

def daily_activity(fills, day, incomplete=False):
    """Gross FIFO estimate only when sold inventory is evidenced in imported fills."""
    todays=[f for f in fills if f['at'].date()==day]
    buy=sum(f.get('amount',f['quantity']*f['price']) for f in todays if f['side']=='buy')
    sell=sum(f.get('amount',f['quantity']*f['price']) for f in todays if f['side']=='sell')
    matched=closed_trades(fills)
    sold_codes={f['code'] for f in todays if f['side']=='sell'}
    uncertain={f['code'] for f in matched['unmatched_sells']}
    # An order timestamp does not establish execution order across opposite orders.
    timestamps=defaultdict(set)
    for f in fills:timestamps[(f['code'],f['at'])].add(f['side'])
    uncertain.update(code for (code,_),sides in timestamps.items() if len(sides)>1)
    valid=not incomplete and not (sold_codes & uncertain)
    closed=[t for t in matched['closed'] if t['sell_at'].date()==day]
    pnl=sum(t['gross_pnl'] for t in closed) if valid else None
    amount=buy+sell if not incomplete else None
    return {'date':day.isoformat(),'buy':buy if not incomplete else None,
            'sell':sell if not incomplete else None,'amount':amount,'pnl':pnl,
            'return_pct':pnl/amount*100 if pnl is not None and amount else None,
            'count':len(todays),'incomplete':incomplete,
            'reason':'일부 체결 제외' if incomplete else '매수 원가 또는 체결 순서 미확인' if not valid else '',
            'basis':'거래금액: 조회된 체결금액 또는 체결단가×수량 · 손익: 주문시간 순 FIFO 추정 · 수수료·세금 미반영'}

