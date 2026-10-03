"""Portable, bounded watch codes for a bookmark or an exported backup."""
import json
import re

WATCHLIST_SCHEMA=2
MAX_WATCH=20
WATCH_GROUPS=('보유종목','매수 관찰','섹터 관심')
DEFAULT_GROUP='매수 관찰'

def clean_codes(values):
    result=[]
    for value in values:
        code=str(value).strip()
        if re.fullmatch(r'[0-9]{6}',code) and code not in result:
            result.append(code)
        if len(result)==MAX_WATCH:break
    return result

def restore_backup(payload):
    try:
        decoded=json.loads(payload)
        if not isinstance(decoded,dict) or decoded.get('version')!=1 or not isinstance(decoded.get('codes'),list):
            raise ValueError
        return clean_codes(decoded['codes'])
    except (ValueError,TypeError,UnicodeDecodeError):
        raise ValueError('관심종목 백업 파일 형식을 확인하세요.') from None

def clean_names(names,codes):
    if not isinstance(names,dict):return {}
    return {code:str(names[code]).strip()[:80] for code in clean_codes(codes)
            if code in names and isinstance(names[code],str) and names[code].strip() and names[code].strip()!=code}

def backup_names(payload):
    codes=restore_backup(payload)
    decoded=json.loads(payload)
    return clean_names(decoded.get('names',{}),codes)

def clean_groups(groups,codes):
    if not isinstance(groups,dict):return {}
    return {code:groups[code] for code in clean_codes(codes)
            if code in groups and isinstance(groups[code],str) and groups[code] in WATCH_GROUPS}

def clean_sectors(sectors,codes):
    if not isinstance(sectors,dict):return {}
    return {code:sectors[code].strip()[:40] for code in clean_codes(codes)
            if code in sectors and isinstance(sectors[code],str) and sectors[code].strip()}

def backup_groups(payload):
    codes=restore_backup(payload)
    return clean_groups(json.loads(payload).get('groups',{}),codes)

def backup_sectors(payload):
    codes=restore_backup(payload)
    return clean_sectors(json.loads(payload).get('sectors',{}),codes)

def filter_codes(codes,groups=None,sectors=None,group=None,sector=None):
    codes=clean_codes(codes)
    groups=clean_groups(groups,codes)
    sectors=clean_sectors(sectors,codes)
    return [code for code in codes
            if (group is None or groups.get(code,DEFAULT_GROUP)==group)
            and (sector is None or sectors.get(code,'')==sector)]

def export_backup(codes,names=None,groups=None,sectors=None):
    codes=clean_codes(codes)
    payload={'version':1,'codes':codes}
    if names:payload['names']=clean_names(names,codes)
    if groups:payload['groups']=clean_groups(groups,codes)
    if sectors:payload['sectors']=clean_sectors(sectors,codes)
    return json.dumps(payload,ensure_ascii=False,indent=2)

