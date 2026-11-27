"""Small artificial timetable fixtures test the engine; never served by the app."""
from datetime import datetime,timedelta
import unittest

from timetable_data import KST,STATIONS,build_runs
from planner import recommend
from prediction import apply_live
from transfers import TransferPolicy

IDS={s['name']:s['id'] for s in STATIONS.values()}
DAY=datetime(2026,11,17,tzinfo=KST)


def at(value):
    h,m=map(int,value.split(':'))
    return DAY.replace(hour=h,minute=m).timestamp()


def run(line,number,stops,direction='1',express=False):
    return {'id':line+'-'+number,'line':line,'number':number,'direction':direction,
        'destination':stops[-1][0],'origin_name':stops[0][0],'source':'https://example.test/fixture',
        'express':express,'service_date':'2026-11-17',
        'stops':[{'station':IDS[name],'arrival':at(value),'departure':at(value),
            'can_board':i<len(stops)-1,'can_alight':i>0} for i,(name,value) in enumerate(stops)]}


class PlannerTests(unittest.TestCase):
    def test_stream_delay_changes_only_verified_service_occurrence(self):
        current=DAY.replace(hour=7,minute=25)
        runs=[run('GTX-A','X1079',[('대곡','07:24'),('서울역','07:36')])]
        delay={'line':'GTX-A','train_no':'X1079','station_name':'대곡','direction':'1',
            'destination':'서울역','observed_at':current.isoformat(),
            'scheduled_at':DAY.replace(hour=7,minute=24).isoformat(),
            'match_status':'matched_observation_interval'}
        feed={'available':True,'source':'kafka_redis','delays':[delay]}
        predicted,stats=apply_live(runs,feed,current)
        self.assertEqual(stats['used_anchors'],1)
        self.assertEqual(predicted[0]['stops'][-1]['arrival'],at('07:37'))
        delay['scheduled_at']=DAY.replace(hour=6,minute=24).isoformat()
        _,stats=apply_live(runs,feed,current)
        self.assertEqual(stats['used_anchors'],0)

    def setUp(self):
        self.now=DAY.replace(hour=7,minute=15)
        self.payload={'origin':IDS['대곡'],'destination':IDS['역삼'],'deadline':DAY.replace(hour=8,minute=40).isoformat(),
            'transfer_extra_minutes':0,'boarding_seconds':0,'safety_minutes':3,'max_transfers':3,
            'transfer_overrides':{f"{IDS['서울역']}:GTX-A:4":180,f"{IDS['충무로']}:4:3":120,f"{IDS['사당']}:4:2":120,f"{IDS['교대']}:3:2":120}}
        self.runs=[run('GTX-A','X1079',[('대곡','07:24'),('서울역','07:36')]),
            run('4','4001',[('서울역','07:42'),('사당','07:58')]),
            run('4','4003',[('서울역','07:48'),('사당','08:04')]),
            run('2','2101',[('사당','08:03'),('역삼','08:20')]),
            run('2','2103',[('사당','08:10'),('역삼','08:27')]),
            run('3','3001',[('대곡','07:22'),('교대','08:05')]),
            run('2','2201',[('교대','08:09'),('역삼','08:15')])]

    def result(self,feed=None,**changes):
        return recommend({**self.payload,**changes},self.now,self.runs,feed or {'available':False})

    def assert_boardable(self,trip):
        self.assertGreaterEqual(trip['board_ts'],self.now.timestamp())
        for t in trip['transfer_details']:
            self.assertGreaterEqual(t['wait_seconds'],0)
        for leg in trip['legs']:
            self.assertGreater(leg['arrive_ts'],leg['board_ts'])

    def test_routes_are_ranked_by_actual_arrival_not_station_count(self):
        r=self.result()
        self.assertEqual(r['earliest']['route_key'],'3|2')
        self.assertEqual(r['earliest']['arrive_ts'],at('08:15'))
        self.assertIn('GTX-A|4|2',[p['route_key'] for p in r['routes']['fastest']])
        for p in r['routes']['fastest']+r['routes']['latest']: self.assert_boardable(p)

    def test_real_time_delay_changes_which_transfer_train_is_catchable(self):
        baseline=self.result()
        feed={'available':True,'arrivals':[{'subwayId':'1032','statnNm':'서울역','btrainNo':'1079','updnLine':'하행','bstatnNm':'서울역','barvlDt':1500,'recptnDt':self.now.isoformat()}]}
        r=self.result(feed)
        before=next(p for p in baseline['routes']['fastest'] if p['route_key']=='GTX-A|4|2')
        after=next(p for p in r['routes']['fastest'] if p['route_key']=='GTX-A|4|2')
        self.assertEqual(before['legs'][1]['train_no'],'4001')
        self.assertEqual(after['legs'][1]['train_no'],'4003')
        self.assertEqual(after['arrive_ts'],at('08:27'))
        self.assertGreater(after['arrive_ts'],before['arrive_ts'])
        self.assertGreater(r['live']['used_anchors'],0)

    def test_latest_departure_is_actual_train_and_meets_deadline(self):
        r=self.result()
        self.assertEqual(r['latest']['board_ts'],at('07:24'))
        self.assertLessEqual(r['latest']['arrive_ts'],at('08:40'))
        self.assert_boardable(r['latest'])

    def test_sending_one_train_uses_next_real_departure(self):
        self.runs=[run('GTX-A','X1',[('대곡','07:24'),('서울역','07:36')]),run('GTX-A','X3',[('대곡','07:44'),('서울역','07:56')])]
        r=self.result(destination=IDS['서울역'],skip_trains=1,max_transfers=0)
        self.assertEqual(r['earliest']['board_ts'],at('07:44'))
        self.assertEqual(r['earliest']['legs'][0]['train_no'],'X3')

    def test_express_does_not_board_at_a_skipped_station(self):
        self.runs=[run('4','E1',[('서울역','07:30'),('사당','07:50')],express=True)]
        r=self.result(origin=IDS['명동'],destination=IDS['사당'])
        self.assertIsNone(r['earliest'])
        self.assertEqual(r['status'],'no_data')

    def test_unreachable_deadline_still_shows_earliest_alternative(self):
        r=self.result(deadline=DAY.replace(hour=8,minute=0).isoformat())
        self.assertEqual(r['status'],'unreachable')
        self.assertIsNone(r['latest'])
        self.assertIsNotNone(r['earliest'])

    def test_no_timetable_never_fabricates_departures(self):
        self.runs=[]
        r=self.result();self.assertEqual(r['status'],'no_data');self.assertIsNone(r['earliest'])

    def test_disallow_transfers_and_invalid_input(self):
        self.assertIsNone(self.result(allow_transfer=False)['earliest'])
        for changes in ({'origin':'missing'},{'destination':IDS['대곡']},{'skip_trains':3},{'max_transfers':True},{'allow_transfer':'yes'},{'deadline':'invalid'},{'transfer_overrides':{'bad':5}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.result(**changes)


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.now=DAY.replace(hour=7,minute=15)
        self.run=run('4','4001',[('서울역','07:16'),('사당','07:32')])
        self.row={'subwayId':'1004','statnNm':'서울역','btrainNo':'4001','updnLine':'하행','bstatnNm':'사당','barvlDt':90,'recptnDt':DAY.replace(hour=7,minute=14,second=30).isoformat()}

    def predict(self,row=None,runs=None):
        return apply_live(runs or [self.run],{'available':True,'arrivals':[row or self.row]},self.now)

    def test_eta_is_relative_to_source_generation_not_request_time(self):
        runs,stats=self.predict()
        self.assertEqual(runs[0]['stops'][0]['arrival'],at('07:16'))
        self.assertEqual(runs[0]['stops'][1]['delay_seconds'],0)
        self.assertEqual(stats['used_anchors'],1)

    def test_stale_unknown_zero_and_ambiguous_values_do_not_update(self):
        for row in ({**self.row,'recptnDt':DAY.replace(hour=7).isoformat()},
                    {**self.row,'barvlDt':0},{**self.row,'bstatnNm':'오이도'},{**self.row,'btrainNo':'9999'}):
            with self.subTest(row=row): self.assertEqual(self.predict(row)[1]['used_anchors'],0)
        duplicate={**self.run,'id':'another-occurrence'}
        self.assertEqual(self.predict(runs=[self.run,duplicate])[1]['ambiguous'],1)

    def test_delay_is_not_added_again_at_every_station(self):
        row={**self.row,'barvlDt':210}
        runs,stats=self.predict(row)
        self.assertEqual([s['delay_seconds'] for s in runs[0]['stops']],[120,120])

    def test_live_gtx_departure_projects_from_observed_status(self):
        raw=run('GTX-A','X1079',[('대곡','07:14'),('서울역','07:26')])
        feed={'available':True,'arrivals':[],'positions':[{'line':'GTX-A','trainNo':'1079','trainSttus':'2','updnLine':'1','statnTnm':'서울역','statnNm':'대곡','recptnDt':self.now.isoformat()}]}
        result,stats=apply_live([raw],feed,self.now)
        self.assertEqual(result[0]['stops'][1]['arrival'],at('07:27'))
        self.assertEqual(stats['used_anchors'],1)


class TransferAndImportTests(unittest.TestCase):
    def test_direct_reverse_and_user_override_are_distinguished(self):
        p=TransferPolicy(120,{},30)
        direct=p.get(IDS['서울역'],'4','GTX-A')
        reverse=p.get(IDS['서울역'],'GTX-A','4')
        self.assertEqual(direct['walk_seconds'],220)
        self.assertFalse(direct['inferred']);self.assertTrue(reverse['inferred'])
        custom=TransferPolicy(0,{f"{IDS['서울역']}:GTX-A:4":600},30)
        self.assertEqual(custom.get(IDS['서울역'],'GTX-A','4')['total_seconds'],630)
        self.assertEqual(custom.get(IDS['서울역'],'4','GTX-A')['walk_seconds'],220)

    def test_expired_schedule_is_excluded_and_24h_is_next_day(self):
        base={'line':'4','number':'1','direction':'1','branch':None,'origin':'서울역','destination':'사당','express':'N','valid_from':'2026-01-01','valid_until':None,'temporary':'N','day_type':'평일','source':'test'}
        rows=[{**base,'station':'서울역','arrival':None,'departure':'24:10:00'}, {**base,'station':'사당','arrival':'24:30:00','departure':None},
              {**base,'number':'expired','station':'서울역','arrival':None,'departure':'07:10:00','valid_until':'2026-11-16'},
              {**base,'number':'expired','station':'사당','arrival':'07:30:00','departure':None,'valid_until':'2026-11-16'}]
        runs,_=build_runs(rows,DAY.date())
        self.assertEqual(len(runs),1)
        self.assertEqual(datetime.fromtimestamp(runs[0]['stops'][0]['departure'],KST).date(),DAY.date()+timedelta(days=1))
        self.assertFalse(runs[0]['stops'][-1]['can_board'])

    def test_real_loop_does_not_split_at_repeated_non_origin_station(self):
        names=['신내','응암','역촌','불광','독바위','연신내','구산','응암','새절']
        rows=[]
        for i,name in enumerate(names):
            rows.append({'line':'6','number':'6101','direction':'0','branch':None,'origin':'신내','destination':'새절','express':'N','valid_from':None,'valid_until':None,'temporary':'N','day_type':'평일','source':'test','station':name,'arrival':f'07:{i*3:02}:00','departure':f'07:{i*3:02}:20' if i<len(names)-1 else None})
        runs,_=build_runs(rows,DAY.date())
        self.assertEqual(len(runs),1)
        self.assertEqual([s['station'] for s in runs[0]['stops']].count(IDS['응암']),2)


if __name__=='__main__': unittest.main()
