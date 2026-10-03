"""Bounded query reuse within one session; never a shared account-data cache."""
from copy import deepcopy
from hashlib import sha256
import json
from time import monotonic

from predash.watchlist import clean_codes

WATCH_TTL_SECONDS=15*60


def query_context(settings):
    """Fingerprint configuration changes without storing raw credentials here."""
    return sha256(json.dumps(settings,sort_keys=True).encode('utf-8')).hexdigest()


def refresh_selected(selected, results, cache, fetch, context, date_key, *,
                     force=False, clock=monotonic, on_progress=None):
    selected=clean_codes(selected)
    same_context=cache.get('context')==context and cache.get('date_key')==date_key
    entries=deepcopy(cache.get('entries',{})) if same_context else {}
    merged=deepcopy(results) if same_context or not cache else {}
    stats={'reused':0,'fetched':0}
    for index,code in enumerate(selected,1):
        saved=entries.get(code,{})
        age=clock()-saved.get('saved_at',float('-inf'))
        reused=not force and 0<=age<WATCH_TTL_SECONDS and isinstance(saved.get('result'),dict)
        if reused:
            result=deepcopy(saved['result'])
            stats['reused']+=1
        else:
            result=fetch(code)
            stats['fetched']+=1
            entries.pop(code,None)
            # Retry incomplete/error responses next time, including provider outages.
            if not result.get('errors') and any(result.get(k) for k in ('lamp','metrics','flow')):
                entries[code]={'saved_at':clock(),'result':deepcopy(result)}
        merged[code]=result
        if on_progress:on_progress(index,len(selected),code,reused)
    # The caller prunes removed tickers; always enforce the same bounded list here.
    allowed=clean_codes(merged)
    merged={code:merged[code] for code in allowed}
    entries={code:entry for code,entry in entries.items() if code in allowed}
    return merged,{'context':context,'date_key':date_key,'entries':entries},stats
