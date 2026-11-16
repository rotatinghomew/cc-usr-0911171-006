import unittest
from opportunity_planner.route_planning.itinerary import ItineraryPlanner
class ItineraryTests(unittest.TestCase):
 def setUp(self):self.p=ItineraryPlanner();self.matrix={'home':{'school':20,'job':10},'job':{'school':25},'school':{'job':25}}
 def row(self,id,start,end,location,**extra):return {'id':id,'start':start,'end':end,'location':location,**extra}
 def test_nonconflicting_plan(self):
  rows=[self.row('job','2026-11-16T09:00:00+00:00','2026-11-16T10:00:00+00:00','job'),self.row('class','2026-11-16T11:00:00+00:00','2026-11-16T12:00:00+00:00','school')];self.assertEqual(self.p.plan(rows,{'travel_matrix':self.matrix})['chosen'],['job','class'])
 def test_travel_conflict(self):
  rows=[self.row('job','2026-11-16T09:00:00+00:00','2026-11-16T10:00:00+00:00','job'),self.row('class','2026-11-16T10:10:00+00:00','2026-11-16T11:00:00+00:00','school')];self.assertEqual(self.p.plan(rows,{'travel_matrix':self.matrix})['rejected'][0]['id'],'class')
 def test_prerequisite(self):self.assertIn('prerequisite_missing',self.p.plan([self.row('advanced','2026-11-16T09:00:00+00:00','2026-11-16T10:00:00+00:00','school',prerequisites=['basic'])],{'travel_matrix':self.matrix})['rejected'][0]['reasons'])
 def test_capacity(self):self.assertIn('capacity_exhausted',self.p.plan([self.row('course','2026-11-16T09:00:00+00:00','2026-11-16T10:00:00+00:00','school',capacity=1,booked=1)],{})['rejected'][0]['reasons'])
