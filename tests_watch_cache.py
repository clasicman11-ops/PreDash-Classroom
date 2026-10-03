"""Verify selective calls, expiry, failure retries and credential/session isolation."""
import unittest
from unittest.mock import Mock

from predash.watch_cache import refresh_selected, query_context, WATCH_TTL_SECONDS


def result(code, errors=None):
    return {'code':code,'lamp':{'close':1},'errors':errors or {},'fetched':'synthetic-timestamp'}


class WatchCacheTests(unittest.TestCase):
    def refresh(self,selected,results=None,cache=None,fetch=None,context='A',day='2026-10-03',now=1000,force=False):
        return refresh_selected(selected,results or {},cache or {},fetch or Mock(side_effect=result),context,day,
                                force=force,clock=lambda:now)

    def test_only_selected_called_existing_results_kept_and_recent_calls_reused(self):
        fetch=Mock(side_effect=result)
        results,cache,stats=self.refresh(['005930','000660'],{'035420':result('035420')},fetch=fetch)
        self.assertEqual([call.args[0] for call in fetch.call_args_list],['005930','000660'])
        self.assertIn('035420',results)
        next_fetch=Mock(side_effect=AssertionError('Recent selection should not call API'))
        updated,_,stats=self.refresh(['005930'],results,cache,next_fetch,now=1010)
        self.assertEqual(stats,{'reused':1,'fetched':0})
        self.assertEqual(updated,results)
        updated['005930']['lamp']['close']=9
        self.assertEqual(cache['entries']['005930']['result']['lamp']['close'],1)

    def test_force_expiry_and_configuration_or_trading_cutoff_changes_requery(self):
        results,cache,_=self.refresh(['005930','000660'])
        for kwargs in ({'force':True},{'now':1000+WATCH_TTL_SECONDS},
                       {'context':'B'},{'day':'2026-10-03 / 18시 이후'}):
            fetch=Mock(side_effect=result)
            refreshed,_,stats=self.refresh(['005930'],results,cache,fetch,**kwargs)
            fetch.assert_called_once_with('005930')
            self.assertEqual(stats['reused'],0)
            if 'context' in kwargs or 'day' in kwargs:self.assertNotIn('000660',refreshed)

    def test_failed_forced_refresh_removes_success_cache_and_retries(self):
        results,cache,_=self.refresh(['005930'])
        failure=Mock(return_value=result('005930',{'price':'synthetic error'}))
        failed,cache,_=self.refresh(['005930'],results,cache,failure,force=True)
        self.assertNotIn('005930',cache['entries'])
        self.assertEqual(failed['005930']['errors'],{'price':'synthetic error'})
        retry=Mock(side_effect=result)
        self.refresh(['005930'],failed,cache,retry,now=1001)
        retry.assert_called_once_with('005930')

    def test_new_session_and_source_keys_cannot_reuse_previous_session(self):
        _,cache,_=self.refresh(['005930'])
        fresh=Mock(side_effect=result)
        self.refresh(['005930'],fetch=fresh)
        fresh.assert_called_once_with('005930')
        self.assertEqual(cache['context'],'A')
        real=query_context({'mode':'real','key':'dummy-real','secret':'dummy-secret'})
        demo=query_context({'mode':'demo','key':'dummy-real','secret':'dummy-secret'})
        changed=query_context({'mode':'real','key':'dummy-other','secret':'dummy-secret'})
        self.assertEqual(len({real,demo,changed}),3)
        self.assertNotIn('dummy',real)


if __name__=='__main__':unittest.main()
