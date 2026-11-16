import decimal
import unittest
from opportunity_planner.scheduler.engine import ScheduleEngine

class DomainTests(unittest.TestCase):
    def setUp(self): self.engine=ScheduleEngine()

    def booking(self,**changes):
        value={'id':'booking-1','start':'2026-11-16T09:00:00+00:00','end':'2026-11-16T10:00:00+00:00','travel_minutes':15,'minimum_rest_minutes':15,'prerequisites':['basic']};value.update(changes);return value
    def context(self,**changes):
        value={'bookings':[],'completed_courses':['basic']};value.update(changes);return value
    def test_feasible(self):self.assertTrue(self.engine.evaluate(self.booking(),self.context()).accepted)
    def test_invalid_interval(self):self.assertIn('invalid_interval',self.engine.evaluate(self.booking(end='2026-11-16T08:00:00+00:00'),self.context()).reasons)
    def test_conflict_with_buffer(self):self.assertIn('schedule_conflict',self.engine.evaluate(self.booking(),self.context(bookings=[{'start':'2026-11-16T10:15:00+00:00','end':'2026-11-16T11:00:00+00:00'}])).reasons)
    def test_missing_prerequisite(self):self.assertIn('prerequisite_missing',self.engine.evaluate(self.booking(),self.context(completed_courses=[])).reasons)
    def test_duration(self):self.assertEqual(self.engine.evaluate(self.booking(),self.context()).value,decimal.Decimal('1.0'))
    def test_rank_feasible_first(self):self.assertTrue(self.engine.rank([self.booking(id='bad',prerequisites=['x']),self.booking(id='good')],self.context())[0].accepted)

