"""Offline tests; fixtures are synthetic and never written to production data/."""
from datetime import datetime, timedelta
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from src.subway_collect.config import Config, DAYS, KST, ROOT, load_keys
from src.subway_collect.derive import save_arrival_samples, save_positions, match_delays, schedule_at
from src.subway_collect.http import Client, SourceError
from src.subway_collect.providers import arrival_response, position_response, public_response, seoul_open_response, public_url
from src.subway_collect.runner import Runner
from src.subway_collect.storage import Store, dumps

TUESDAY = datetime(2026, 11, 10, 18, 0, tzinfo=KST)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        values = json.loads((ROOT / 'config/collector.json').read_text(encoding='utf8'))
        self.config = Config(self.root, values)
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def row(self, seconds=0, status='0', **extra):
        return dict(trainNo='2001', statnId='1002000222', statnNm='강남', updnLine='1',
                    trainSttus=status, recptnDt=(TUESDAY+timedelta(seconds=seconds)).strftime('%Y-%m-%d %H:%M:%S'), **extra)

    def save(self, seconds, status='0'):
        row = self.row(seconds, status)
        return save_positions(self.store, self.config, [row], TUESDAY+timedelta(seconds=seconds+1), '2호선')


class WindowsTests(Fixture):
    def test_boundaries_all_days_and_next_day(self):
        for day in range(7):
            d = TUESDAY+timedelta(days=day)
            for hour, minute, expected in [(6,29,False),(6,30,True),(9,29,True),(9,30,False),(17,30,True),(20,29,True),(20,30,False)]:
                self.assertEqual(self.config.active(d.replace(hour=hour,minute=minute)), expected)
        late = TUESDAY.replace(hour=21)
        self.assertEqual(self.config.next_start(late), TUESDAY.replace(day=11,hour=6,minute=30))

    def test_invalid_and_overlapping_windows(self):
        self.config.values['windows'] = [['06:30','09:30'],['09:00','10:00']]
        with self.assertRaises(ValueError):
            self.config.validate()

    def test_keys_reload_without_printing_secret(self):
        (self.root / '.env').write_text('SEOUL_SUBWAY_API_KEY="abc123"\n', encoding='utf8')
        self.assertEqual(load_keys(self.root)['SEOUL_SUBWAY_API_KEY'], 'abc123')
        (self.root / '.env').write_text('SEOUL_SUBWAY_API_KEY=new\n', encoding='utf8')
        self.assertEqual(load_keys(self.root)['SEOUL_SUBWAY_API_KEY'], 'new')


class ParserTests(unittest.TestCase):
    def test_position_pagination_total_and_empty(self):
        rows,total = position_response({'errorMessage':{'code':'INFO-000','total':1200},'realtimePositionList':[{'trainNo':'1','statnId':'2'}]})
        self.assertEqual((len(rows),total),(1,1200))
        self.assertEqual(position_response({'code':'INFO-200'}),([],0))

    def test_api_errors_are_not_empty_success(self):
        for parser, doc in [(position_response,{'errorMessage':{'code':'ERROR-337'}}),
                            (public_response,{'header':{'resultCode':'30'}})]:
            with self.assertRaises(SourceError):
                parser(doc)

    def test_public_singleton_and_empty(self):
        self.assertEqual(public_response({'response':{'header':{'resultCode':'00'},'body':{'totalCount':1,'items':{'item':{'a':1}}}}}),([{'a':1}],1))
        self.assertEqual(public_response({'header':{'resultCode':0},'body':{'totalCount':0,'items':''}}),([],0))

    def test_encoded_service_key_is_encoded_once(self):
        url = public_url('schedule','getTrainSch','abc%2Bdef%3D',{})
        self.assertIn('abc%2Bdef%3D',url)
        self.assertNotIn('%252B',url)



class DerivationTests(Fixture):
    def test_dedup_restart_and_arrival_interval(self):
        self.save(0,'0')
        self.store.close()
        self.store = Store(self.root)
        self.assertEqual(self.save(0,'0'),0)
        self.save(30,'1')
        self.save(60,'1')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM arrivals').fetchone()[0],1)
        row = self.store.db.execute('SELECT * FROM arrivals').fetchone()
        self.assertEqual(row['uncertainty_seconds'],30)
        self.assertEqual(row['quality'],'bounded_observation')

    def test_first_seen_arrival_not_used_as_precise_label(self):
        self.save(0,'1')
        match_delays(self.store,'2026-11-10')
        row = self.store.db.execute('SELECT * FROM delays').fetchone()
        self.assertEqual(row['match_status'],'unbounded_arrival')
        self.assertIsNone(row['delay_seconds'])

    def test_stale_and_out_of_order_do_not_create_false_arrivals(self):
        self.save(60,'0')
        save_positions(self.store,self.config,[self.row(30,'1')],TUESDAY+timedelta(seconds=61),'2호선')
        save_positions(self.store,self.config,[self.row(90,'1')],TUESDAY+timedelta(seconds=300),'2호선')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM arrivals').fetchone()[0],0)

    def test_matched_delay_keeps_sampling_interval_and_negative_values(self):
        self.save(0,'0')
        self.save(30,'1')
        with self.store.db:
            self.store.insert('timetable',dict(event_id='tt',fetched_date='2026-11-10',line='2호선',
                train_no='2001',station_name='강남',direction='1',day_type='평일',arrival_time='18:00:40',payload_json='{}'))
        match_delays(self.store,'2026-11-10')
        row = self.store.db.execute('SELECT * FROM delays').fetchone()
        self.assertEqual(row['delay_seconds'],-10)
        self.assertEqual(row['lower_delay_seconds'],-40)
        self.assertEqual(row['match_status'],'matched_observation_interval')

    def test_ambiguous_schedule_not_guessed(self):
        self.save(0,'0'); self.save(30,'1')
        with self.store.db:
            for eid in ('one','two'):
                self.store.insert('timetable',dict(event_id=eid,fetched_date='2026-11-10',line='2호선',
                    train_no='2001',station_name='강남',direction='1',arrival_time='18:00:00',payload_json='{}'))
        match_delays(self.store,'2026-11-10')
        self.assertEqual(self.store.db.execute('SELECT match_status FROM delays').fetchone()[0],'ambiguous_timetable')

    def test_over_midnight_timetable(self):
        self.assertEqual(schedule_at('2026-11-10','25:10:00'),datetime(2026,11,11,1,10,tzinfo=KST))
        self.assertIsNone(schedule_at('2026-11-10','99:88:00'))


class StatisticsTests(Fixture):
    def test_export_partition_and_no_duplicates(self):
        self.save(0)
        folder = self.store.export('2026-11-10')
        self.store.export('2026-11-10')
        self.assertIn('dow=TUE',str(folder))
        self.assertEqual(len((folder/'positions.csv').read_text(encoding='utf-8-sig').splitlines()),2)


class FakeResponse:
    status = 200
    def __init__(self, doc): self.body = json.dumps(doc).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body


class ReliabilityTests(Fixture):
    def test_http_error_body_archived_without_credentials(self):
        def fail(*args,**kwargs):
            raise HTTPError('https://example.test/secretABC',400,'bad',{},io.BytesIO(b'{"code":9000,"echo":"secretABC"}'))
        client=Client(self.config,self.store,lambda _:True,clock=lambda:TUESDAY,opener=fail)
        client.keys={'DATA_GO_KR_API_KEY':'secretABC'}
        self.assertIsNone(client.fetch('data_go_kr','timetable','1','https://example.test',public_response,attempts=1))
        row=self.store.db.execute('SELECT * FROM requests').fetchone()
        self.assertEqual(row['http_status'],400)
        self.assertEqual(row['outcome'],'failed')
        with gzip.open(self.root/row['raw_path'],'rt',encoding='utf8') as f: raw=f.read()
        self.assertNotIn('secretABC',raw)
        self.assertIn('9000',raw)

    def test_position_pagination_does_not_drop_boundary_row(self):
        self.config.values['lines'] = ['2호선']  # 실제 설정의 노선 수와 무관하게 페이지 경계만 검증
        runner = Runner(self.config,self.store,clock=lambda:TUESDAY)
        # Use synthetic pages and a frozen clock, no real network or credentials.
        from unittest.mock import patch
        urls=[]
        def fetch(*args,**kwargs):
            urls.append(args[3])
            return [self.row(train_marker=len(urls))],1001
        runner.client.fetch=fetch
        with patch('src.subway_collect.runner.load_keys',return_value={'SEOUL_SUBWAY_API_KEY':'test'}):
            runner.positions()
        self.assertEqual(len(urls),2)
        self.assertIn('/1/1000/',urls[0])
        self.assertIn('/1001/2000/',urls[1])

    def test_duplicate_runner_lock_is_rejected(self):
        from src.subway_collect.cli import lock_file
        with lock_file(self.store.data):
            with self.assertRaises(RuntimeError):
                with lock_file(self.store.data):
                    self.fail('Second runner acquired lock')

    def test_retry_budget_survives_restart_and_resets_next_day(self):
        self.config.values['daily_budgets']['seoul_subway'] = 2
        calls = []
        def fail(*args,**kwargs):
            calls.append(1); raise URLError('not logged secret')
        client = Client(self.config,self.store,lambda _:True,clock=lambda:TUESDAY,opener=fail,sleep=lambda _:None)
        client.fetch('seoul_subway','positions','2','http://example.test',position_response)
        self.assertEqual(len(calls),2)
        self.store.close(); self.store = Store(self.root)
        self.assertIsNone(self.store.begin_request('seoul_subway','positions','2',TUESDAY,2))
        self.assertIsNotNone(self.store.begin_request('seoul_subway','positions','2',TUESDAY+timedelta(days=1),2))

    def test_outside_window_and_stop_prevent_requests(self):
        client = Client(self.config,self.store,lambda _:False,clock=lambda:TUESDAY,
                        opener=lambda *a,**k: self.fail('network called'))
        self.assertIsNone(client.fetch('data_go_kr','timetable','1','http://example.test',public_response))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM requests').fetchone()[0],0)

    def test_response_secret_redacted_from_disk(self):
        doc = {'header':{'resultCode':'00'},'body':{'totalCount':0},'echo':'superSecret123'}
        client = Client(self.config,self.store,lambda _:True,clock=lambda:TUESDAY,
                        opener=lambda *a,**k: FakeResponse(doc))
        client.keys = {'DATA_GO_KR_API_KEY':'superSecret123'}
        self.assertIsNotNone(client.fetch('data_go_kr','timetable','1','http://example.test',public_response))
        path = next((self.root/'data/raw').rglob('*.gz'))
        with gzip.open(path,'rt',encoding='utf8') as f: raw = f.read()
        self.assertNotIn('superSecret123',raw)

    def test_auth_failure_does_not_disable_other_government_dataset(self):
        calls = []
        def fake(*a,**k):
            calls.append(1)
            return FakeResponse({'header':{'resultCode':'30'}} if len(calls)==1 else {'header':{'resultCode':'00'},'body':{'totalCount':0}})
        client = Client(self.config,self.store,lambda _:True,clock=lambda:TUESDAY,opener=fake)
        client.fetch('data_go_kr','notices','1','http://example.test',public_response)
        self.assertIsNotNone(client.fetch('data_go_kr','timetable','1','http://example.test',public_response))
        self.assertEqual(len(calls),2)

    def test_timetable_uses_official_required_parameters(self):
        self.config.values['lines'] = ['2호선']
        runner = Runner(self.config,self.store,clock=lambda:TUESDAY)
        runner.keys={'DATA_GO_KR_API_KEY':'test'}
        urls=[]
        def fetch(*args,**kwargs):
            urls.append(args[3]); return [],0
        runner.client.fetch=fetch
        self.assertTrue(runner.timetable_step())
        from urllib.parse import urlparse, parse_qs
        query=parse_qs(urlparse(urls[0]).query)
        self.assertEqual(query['tmprTmtblYn'],['N'])
        self.assertEqual(query['upbdnbSe'],['내선'])
        self.assertEqual(query['wkndSe'],['평일'])



class NewSourceTests(Fixture):
    def test_line_scope_rejects_out_of_service_lines(self):
        self.config.values['lines'] = ['2호선', '9호선']
        with self.assertRaises(ValueError):
            self.config.validate()

    def test_positions_polls_only_requested_line(self):
        from unittest.mock import patch
        runner = Runner(self.config,self.store,clock=lambda:TUESDAY)
        urls=[]
        runner.client.fetch=lambda *a,**k: (urls.append(a[3]), ([],0))[1]
        with patch('src.subway_collect.runner.load_keys',return_value={'SEOUL_SUBWAY_API_KEY':'test','SEOUL_OPEN_API_KEY':'','DATA_GO_KR_API_KEY':''}):
            runner.positions(['3호선'])
        self.assertEqual(len(urls),1)
        self.assertIn('3%ED%98%B8%EC%84%A0',urls[0])

    def test_arrival_samples_keep_only_service_lines(self):
        rows=[dict(subwayId='1002',statnId='1002000222',statnNm='강남',btrainNo='2001',updnLine='내선',arvlCd='1',recptnDt='2026-11-10 18:00:05'),
              dict(subwayId='1009',statnId='1009000925',statnNm='신논현',btrainNo='9001',updnLine='상행',arvlCd='1',recptnDt='2026-11-10 18:00:05')]
        self.assertEqual(save_arrival_samples(self.store,self.config,rows,TUESDAY),1)
        self.assertEqual(save_arrival_samples(self.store,self.config,rows,TUESDAY),0)
        row=self.store.db.execute('SELECT * FROM arrival_samples').fetchone()
        self.assertEqual((row['line'],row['train_no'],row['arrival_code']),('2호선','2001','1'))

    def test_arrival_and_open_api_errors_are_not_empty_success(self):
        with self.assertRaises(SourceError):
            arrival_response({'errorMessage':{'code':'ERROR-337'}})
        self.assertEqual(arrival_response({'errorMessage':{'code':'INFO-200'}}),([],0))
        parse=seoul_open_response('CardSubwayTime')
        with self.assertRaises(SourceError):
            parse({'RESULT':{'CODE':'INFO-100'}})
        self.assertEqual(parse({'CardSubwayTime':{'list_total_count':1,'RESULT':{'CODE':'INFO-000'},'row':[{'a':1}]}}),([{'a':1}],1))

    def test_boarding_months_wait_for_fifth_day_release(self):
        self.config.values['boarding_volume_months']=2
        runner=Runner(self.config,self.store,clock=lambda:TUESDAY)
        self.assertEqual(runner.boarding_months(datetime(2026,11,4,tzinfo=KST)),['202609','202608'])
        self.assertEqual(runner.boarding_months(datetime(2026,11,5,tzinfo=KST)),['202610','202609'])
        self.assertEqual(runner.boarding_months(datetime(2026,1,10,tzinfo=KST)),['202512','202511'])

    def test_boarding_volume_pages_then_stops(self):
        self.config.values['boarding_volume_months']=1
        runner=Runner(self.config,self.store,clock=lambda:TUESDAY)
        runner.keys={'SEOUL_OPEN_API_KEY':'test'}
        urls=[]
        runner.client.fetch=lambda *a,**k: (urls.append(a[3]), ([{'n':len(urls)}],1500))[1]
        self.assertTrue(runner.boarding_volume_step())
        self.assertTrue(runner.boarding_volume_step())
        self.assertFalse(runner.boarding_volume_step())
        self.assertIn('/CardSubwayTime/1/1000/202610',urls[0])
        self.assertIn('/CardSubwayTime/1001/2000/202610',urls[1])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM boarding_volume').fetchone()[0],2)

    def test_unpublished_boarding_month_is_retried_next_day(self):
        self.config.values['boarding_volume_months']=1
        day=[TUESDAY]
        runner=Runner(self.config,self.store,clock=lambda:day[0])
        runner.keys={'SEOUL_OPEN_API_KEY':'test'}
        calls=[]
        runner.client.fetch=lambda *a,**k: (calls.append(1), ([],0))[1]
        self.assertTrue(runner.boarding_volume_step())
        self.assertFalse(runner.boarding_volume_step())
        day[0]=TUESDAY+timedelta(days=1)
        self.assertTrue(runner.boarding_volume_step())
        self.assertEqual(len(calls),2)

    def test_reference_csv_cp949_loaded_once_with_original_columns(self):
        from src.subway_collect.reference import load_reference
        path=self.root/'transfer.csv'
        path.write_bytes('환승역명,환승노선,환승거리,환승소요시간\n시청,1호선→2호선,250,3:40\n'.encode('cp949'))
        first=load_reference(self.store,'transfer_times',path)
        self.assertEqual((first['status'],first['encoding'],first['rows']),('loaded','cp949',1))
        self.assertEqual(load_reference(self.store,'transfer_times',path)['status'],'already_loaded')
        row=json.loads(self.store.db.execute('SELECT payload_json FROM reference_rows').fetchone()[0])
        self.assertEqual(row['환승소요시간'],'3:40')
        with self.assertRaises(ValueError):
            load_reference(self.store,'weather',path)

    def test_timetable_direction_uses_position_api_codes(self):
        self.config.values['lines'] = ['2호선']
        runner = Runner(self.config,self.store,clock=lambda:TUESDAY)
        runner.keys = {'DATA_GO_KR_API_KEY':'test'}
        rows = [dict(trainno='2001',stnNm='강남',upbdnbSe='외선',trainArvlTm='18:00:40'),
                dict(trainno='2002',stnNm='강남',upbdnbSe='내선',trainArvlTm='18:01:40')]
        runner.client.fetch = lambda *a,**k: (rows,2)
        runner.timetable_step()
        got = dict(self.store.db.execute('SELECT train_no,direction FROM timetable').fetchall())
        self.assertEqual(got, {'2001':'1','2002':'0'})  # 외선=1, 내선=0 (위치 API updnLine과 같은 코드)

    def test_old_reversed_timetable_directions_are_repaired(self):
        with self.store.db:
            self.store.insert('timetable',dict(event_id='old',fetched_date='2026-11-10',line='2호선',train_no='2001',
                station_name='강남',direction='0',arrival_time='18:00:40',payload_json=dumps({'upbdnbSe':'외선'})))
        self.store.close(); self.store = Store(self.root)
        self.assertEqual(self.store.db.execute('SELECT direction FROM timetable').fetchone()[0],'1')

    def test_direction_only_mismatch_is_reported_not_guessed(self):
        self.save(0,'0'); self.save(30,'1')  # 위치 방향 '1'
        with self.store.db:
            self.store.insert('timetable',dict(event_id='tt',fetched_date='2026-11-10',line='2호선',train_no='2001',
                station_name='강남',direction='0',arrival_time='18:00:40',payload_json='{}'))
        match_delays(self.store,'2026-11-10')
        row = self.store.db.execute('SELECT match_status,delay_seconds FROM delays').fetchone()
        self.assertEqual((row[0],row[1]),('direction_mismatch',None))

    def station_case(self, line, observed_name, timetable_name, train='5001'):
        with self.store.db:
            self.store.insert('timetable',dict(event_id=f'tt-{line}-{timetable_name}',fetched_date='2026-11-10',line=line,train_no=train,
                station_name=timetable_name,direction='1',arrival_time='18:00:40',payload_json='{}'))
        for sec, status in ((0,'0'),(30,'1')):
            row = dict(trainNo=train, statnId='x', statnNm=observed_name, updnLine='1', trainSttus=status,
                       recptnDt=(TUESDAY+timedelta(seconds=sec)).strftime('%Y-%m-%d %H:%M:%S'))
            save_positions(self.store, self.config, [row], TUESDAY+timedelta(seconds=sec+1), line)
        match_delays(self.store, '2026-11-10')
        return self.store.db.execute('SELECT match_status, station_name FROM delays WHERE line=?', (line,)).fetchone()

    def test_station_note_in_parentheses_is_matched(self):
        self.assertEqual(tuple(self.station_case('5호선','천호(풍납토성)','천호')), ('matched_observation_interval','천호'))

    def test_line_specific_alias_only_when_timetable_has_it(self):
        self.assertEqual(tuple(self.station_case('7호선','총신대입구(이수)','이수','7001')), ('matched_observation_interval','이수'))

    def test_alias_matches_timetable_name_with_its_own_note(self):
        self.assertEqual(tuple(self.station_case('7호선','뚝섬유원지','자양(뚝섬한강공원)','7002')), ('matched_observation_interval','자양(뚝섬한강공원)'))

    def test_unknown_station_name_stays_unmatched(self):
        self.assertEqual(self.station_case('6호선','없는역','다른역','6001')[0], 'unmatched_timetable')

    def trip_case(self, line, observed_train, rows, terminal):
        """rows = [(train_no, station, arrival_time, destination, linked_to)]. 관측: 강남, 18:00:30 도착, 종착역 terminal."""
        with self.store.db:
            for i, (tr, st, t, dest, nxt) in enumerate(rows):
                self.store.insert('timetable', dict(event_id=f'trip-{line}-{i}', fetched_date='2026-11-10', line=line, train_no=tr,
                    station_name=st, direction='1', arrival_time=t, payload_json=dumps({'arvlStnNm': dest, 'lnkgTrainno': nxt})))
        for sec, status in ((0,'0'),(30,'1')):
            row = dict(trainNo=observed_train, statnId='x', statnNm='강남', updnLine='1', trainSttus=status, statnTnm=terminal,
                       recptnDt=(TUESDAY+timedelta(seconds=sec)).strftime('%Y-%m-%d %H:%M:%S'))
            save_positions(self.store, self.config, [row], TUESDAY+timedelta(seconds=sec+1), line)
        match_delays(self.store, '2026-11-10')
        return tuple(self.store.db.execute('SELECT match_status, match_method, delay_seconds FROM delays WHERE line=?', (line,)).fetchone())

    def test_exact_number_with_same_destination(self):
        self.assertEqual(self.trip_case('3호선','3259',[('3259','강남','18:00:40','대화',None)],'대화'), ('matched_observation_interval','exact',-10))

    def test_exact_number_but_other_destination_is_not_trusted(self):
        self.assertEqual(self.trip_case('3호선','3259',[('3259','강남','18:00:40','오금',None)],'대화')[:2], ('destination_mismatch',None))

    def test_linked_trainno_after_turnaround_needs_same_destination(self):
        rows = [('3257','대화','17:20:00','대화','3259'), ('3259','강남','18:00:40','오금',None)]
        self.assertEqual(self.trip_case('3호선','3257',rows,'오금'), ('matched_observation_interval','linked_trainno',-10))

    def test_linked_trainno_with_other_destination_stays_unmatched(self):
        rows = [('3257','대화','17:20:00','대화','3259'), ('3259','강남','18:00:40','오금',None)]
        self.assertEqual(self.trip_case('3호선','3257',rows,'수서')[0], 'unmatched_timetable')

    def test_korail_k_numbers(self):
        self.assertEqual(self.trip_case('4호선','4626',[('K4626','강남','18:00:40','오이도',None)],'오이도')[:2], ('matched_observation_interval','korail_k_number'))
        self.assertEqual(self.trip_case('1호선','0473',[('K473','강남','18:00:40','인천',None)],'인천')[:2], ('matched_observation_interval','korail_k_number'))

    def test_line2_first_digit_with_terminal_alias(self):
        self.assertEqual(self.trip_case('2호선','3308',[('2308','강남','18:00:40','성수',None)],'성수종착'), ('matched_observation_interval','line2_first_digit',-10))

    def test_loop_terminal_name_matches(self):
        self.assertEqual(self.trip_case('6호선','6217',[('6217','강남','18:00:40','응암',None)],'응암순환(상선)')[:2], ('matched_observation_interval','exact'))

    def test_inferred_match_an_hour_off_is_not_used_as_delay(self):
        self.assertEqual(self.trip_case('1호선','1118',[('K1118','강남','17:01:30','용산',None)],'용산'), ('unverified_large_delay',None,None))

    def test_exact_match_keeps_large_real_delay(self):
        self.assertEqual(self.trip_case('5호선','5118',[('5118','강남','17:30:30','방화',None)],'방화'), ('matched_observation_interval','exact',1800))

    def test_k_number_without_known_destination_is_not_used(self):
        self.assertEqual(self.trip_case('3호선','3255',[('3255K','강남','18:00:40','대화',None)],None)[0], 'unmatched_timetable')

    def test_periodic_export_skips_positions(self):
        self.save(0)
        folder=self.store.export('2026-11-10',include_positions=False)
        self.assertFalse((folder/'positions.csv').exists())
        self.assertTrue((folder/'arrival_samples.csv').exists())


if __name__ == '__main__':
    unittest.main()
