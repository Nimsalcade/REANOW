"""Offline tests only. All airport/flight rows and HTTP responses below are synthetic."""
import argparse
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import flight_data as f


class Response(io.BytesIO):
    def __init__(self,body,content_type='text/csv',length=None):
        super().__init__(body)
        self.status=200
        self.headers={'Content-Type':content_type,'Content-Length':str(len(body) if length is None else length)}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.out=Path(self.temp.name)
        self.job=dict(id='synthetic',source_id='test_only',url='https://example.test/data.csv',
                      format='csv',filename='data.csv',data_period='synthetic')

    def tearDown(self):
        self.temp.cleanup()

    def test_success_saves_hash_and_bytes(self):
        body=b'name,value\nsynthetic,3\n'
        result=f.download(self.job,self.out,1000,'OfflineTest',lambda *a,**k:Response(body))
        self.assertEqual(result['sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual((self.out/result['path']).read_bytes(),body)
        self.assertEqual(result['bytes'],len(body))

    def test_html_error_is_not_saved_as_csv(self):
        with self.assertRaises(ValueError):
            f.download(self.job,self.out,1000,'OfflineTest',lambda *a,**k:Response(b'<html>error</html>','text/html'))
        self.assertEqual(list((self.out/'raw').iterdir()),[])
        self.assertEqual(list((self.out/'tmp').iterdir()),[])

    def test_declared_size_limit(self):
        with self.assertRaises(ValueError):
            f.download(self.job,self.out,4,'OfflineTest',lambda *a,**k:Response(b'a,b\n1,2\n'))
        self.assertEqual(list((self.out/'raw').iterdir()),[])

    def test_stream_limit_without_content_length(self):
        def open_response(*a,**k):
            r=Response(b'a,b\n1,2\n')
            del r.headers['Content-Length']
            return r
        with self.assertRaises(ValueError):
            f.download(self.job,self.out,4,'OfflineTest',open_response)
        self.assertEqual(list((self.out/'tmp').iterdir()),[])

    def test_partial_transfer_is_rejected(self):
        with self.assertRaises(ValueError):
            f.download(self.job,self.out,1000,'OfflineTest',lambda *a,**k:Response(b'a,b\n',length=500))
        self.assertEqual(list((self.out/'raw').iterdir()),[])

    def test_denial_not_retried(self):
        calls=[]
        def denied(*a,**k):
            calls.append(1)
            raise urllib.error.HTTPError(self.job['url'],403,'denied',{},None)
        with self.assertRaises(urllib.error.HTTPError):
            f.download(self.job,self.out,1000,'OfflineTest',denied)
        self.assertEqual(len(calls),1)

    def test_failure_preserves_current_index(self):
        out=self.out/'data'
        out.mkdir()
        original={'synthetic':{'path':'previous_good.csv','sha256':'test-only'}}
        f.atomic_json(out/'current.json',original)
        jobs=self.out/'jobs.json'
        job=dict(self.job,group='reference')
        jobs.write_text(json.dumps({'jobs':[job]}))
        args=argparse.Namespace(jobs=jobs,group=['reference'],id=[],out=out,max_total_mb=1,
                                max_file_mb=1,user_agent='OfflineTest')
        with patch.object(f,'download',side_effect=ValueError('synthetic error')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(f.fetch(args),1)
        self.assertEqual(f.read_json(out/'current.json'),original)
        self.assertEqual(json.loads((out/'downloads.jsonl').read_text())['status'],'failed')

    def test_non_https_rejected(self):
        with self.assertRaises(ValueError):
            f.download(dict(self.job,url='http://example.test/'),self.out,1000,'OfflineTest')

    def test_long_retry_after_is_respected_without_early_retry(self):
        def limited(*a,**k):
            raise urllib.error.HTTPError(self.job['url'],429,'limited',{'Retry-After':'120'},None)
        with patch.object(f.time,'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                f.download(self.job,self.out,1000,'OfflineTest',limited)
            sleep.assert_not_called()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.db=self.root/'test.sqlite'

    def tearDown(self):
        self.temp.cleanup()

    def write_csv(self,name,columns,rows):
        path=self.root/name
        with path.open('w',newline='',encoding='utf-8') as out:
            w=csv.writer(out)
            w.writerow(columns)
            w.writerows(rows)
        return path

    def quiet(self,call,args):
        with contextlib.redirect_stdout(io.StringIO()):
            return call(args)

    def make_flights(self,delay=15,bad_time=False,multi_month=False):
        cols=['FlightDate','Reporting_Airline','DOT_ID_Reporting_Airline',
              'Flight_Number_Reporting_Airline','OriginAirportID','Origin','DestAirportID',
              'Dest','CRSDepTime','CRSArrTime','CRSElapsedTime','Cancelled','Diverted','ArrDelay','ArrDel15']
        rows=[['2026-08-01','ZZ',99999,123,1,'AAA',2,'BBB',900,1100,120,0,0,delay,1 if delay>=15 else 0],
              ['2026-08-01','ZZ',99999,124,1,'AAA',2,'BBB',960 if bad_time else 2400,200,120,1,0,'','']]
        if multi_month:
            rows[1][0]='2026-07-01'
        return self.write_csv('synthetic_flights.csv',cols,rows)

    def test_month_replacement_and_revised_file(self):
        file=self.make_flights()
        args=argparse.Namespace(file=file,db=str(self.db))
        self.quiet(f.import_ontime,args)
        self.quiet(f.import_ontime,args)
        self.make_flights(delay=-3)
        self.quiet(f.import_ontime,args)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM flight_history').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT MIN(arrival_delay_minutes) FROM flight_history').fetchone()[0],-3)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM future_flights').fetchone()[0],0)

    def test_invalid_revision_rolls_back_good_month(self):
        file=self.make_flights()
        args=argparse.Namespace(file=file,db=str(self.db))
        self.quiet(f.import_ontime,args)
        self.make_flights(bad_time=True)
        with self.assertRaises(ValueError):
            self.quiet(f.import_ontime,args)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM flight_history').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT MAX(arrival_delay_minutes) FROM flight_history').fetchone()[0],15)

    def test_multi_month_import_rejected(self):
        file=self.make_flights(multi_month=True)
        with self.assertRaises(ValueError):
            self.quiet(f.import_ontime,argparse.Namespace(file=file,db=str(self.db)))

    def test_denominators_and_unknown_delay(self):
        file=self.make_flights()
        self.quiet(f.import_ontime,argparse.Namespace(file=file,db=str(self.db)))
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            f.route_stats(argparse.Namespace(db=str(self.db),origin='AAA',destination='BBB'))
        r=json.loads(out.getvalue())['rows'][0]
        self.assertEqual(r['cancellation_rate'],0.5)
        self.assertEqual(r['arrival_delay_15_rate'],1.0)
        self.assertEqual(r['delay_observations'],1)

    def test_airport_country_and_closed_filters(self):
        cols=['id','ident','name','iso_country','latitude_deg','longitude_deg','type','iata_code','icao_code','scheduled_service']
        data=[[1,'SYN1','Synthetic U.S.','US',40,-90,'small_airport','AAA','', 'yes'],
              [2,'SYN2','Synthetic territory','PR',18,-66,'small_airport','BBB','', 'yes'],
              [3,'SYN3','Synthetic Canada','CA',45,-80,'small_airport','CCC','', 'yes'],
              [4,'SYN4','Synthetic closed','US',41,-90,'closed_airport','DDD','', 'no']]
        path=self.write_csv('synthetic_airports.csv',cols,data)
        args=argparse.Namespace(airports=path,timezones=None,states_only=False,db=str(self.db))
        self.quiet(f.import_airports,args)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT country FROM airports ORDER BY country').fetchall(),[('PR',),('US',)])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM airports WHERE timezone IS NULL').fetchone()[0],2)
        args.states_only=True
        self.quiet(f.import_airports,args)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM airports').fetchone()[0],1)

    def test_numeric_and_time_checks(self):
        self.assertEqual(f.hhmm('2400'),2400)
        self.assertEqual(f.hhmm('59'),59)
        self.assertIsNone(f.number(''))
        for n in ['1260','-1','2500','12.5']:
            with self.assertRaises(ValueError):f.hhmm(n)
        with self.assertRaises(ValueError):f.number('NaN')


if __name__=='__main__':
    unittest.main()
