import unittest
from predash.watchlist import (MAX_WATCH,clean_codes,export_backup,restore_backup,backup_names,
                              backup_groups,backup_sectors,filter_codes)

class WatchlistTests(unittest.TestCase):
    def test_public_codes_bounded_deduplicated_and_portable(self):
        raw=['005930','000660','005930','abc123','12345678']+[f'{i:06d}' for i in range(30)]
        codes=clean_codes(raw)
        self.assertEqual(codes[:2],['005930','000660'])
        self.assertEqual(len(codes),MAX_WATCH)
        self.assertEqual(restore_backup(export_backup(codes)),codes)
    def test_invalid_backup_rejected(self):
        with self.assertRaises(ValueError):restore_backup('{"version":2,"codes":[]}')
    def test_named_backup_preserves_names_and_legacy_codes(self):
        payload=export_backup(['005930'],{'005930':'삼성전자'})
        self.assertEqual(restore_backup(payload),['005930'])
        self.assertEqual(backup_names(payload),{'005930':'삼성전자'})
        self.assertEqual(backup_names('{"version":1,"codes":["005930"]}'),{})
    def test_backup_names_exclude_unlisted_and_invalid_labels(self):
        payload=export_backup(['005930','000660','035420'],
            {'005930':' 삼성전자 ','000660':123,'035420':'035420','123456':'다른 종목'})
        self.assertEqual(backup_names(payload),{'005930':'삼성전자'})
    def test_twenty_codes_and_classification_round_trip_legacy_backup(self):
        codes=[f'{i:06d}' for i in range(20)]
        payload=export_backup(codes,{codes[19]:'테스트 종목'},
                              {codes[19]:'보유종목'},{codes[19]:' 반도체 '})
        self.assertEqual(len(restore_backup(payload)),20)
        self.assertEqual(backup_groups(payload),{codes[19]:'보유종목'})
        self.assertEqual(backup_sectors(payload),{codes[19]:'반도체'})
        legacy='{"version":1,"codes":["005930"],"names":{"005930":"삼성전자"}}'
        self.assertEqual(backup_groups(legacy),{})
        self.assertEqual(filter_codes(restore_backup(legacy),group='매수 관찰'),['005930'])
    def test_filters_and_metadata_reject_unlisted_invalid_values(self):
        codes=['005930','000660','035420']
        payload=export_backup(codes,groups={'005930':'보유종목','000660':'섹터 관심','035420':['bad'],'123456':'보유종목'},
                              sectors={'005930':'반도체','000660':'x'*100,'035420':123})
        self.assertEqual(backup_groups(payload),{'005930':'보유종목','000660':'섹터 관심'})
        self.assertEqual(len(backup_sectors(payload)['000660']),40)
        self.assertEqual(filter_codes(codes,backup_groups(payload),backup_sectors(payload),'보유종목','반도체'),['005930'])
        self.assertEqual(filter_codes(codes,backup_groups(payload),backup_sectors(payload),'매수 관찰',''),['035420'])

if __name__=='__main__':unittest.main()

