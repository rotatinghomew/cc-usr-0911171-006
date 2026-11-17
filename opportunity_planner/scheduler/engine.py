from __future__ import annotations
from ..route_planning.itinerary import ItineraryPlanner
from ..route_planning.change_impact import ChangeImpactAnalyzer
import datetime as dt
import decimal
import math
import statistics
import ast
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

class DomainError(RuntimeError):
    pass

@dataclass(frozen=True)
class AnalysisResult:
    key: str
    accepted: bool
    value: decimal.Decimal
    reasons: tuple[str,...]
    facts: Mapping[str,Any]=field(default_factory=dict)

class ScheduleEngineBase:
    algorithm='scheduling'
    def normalize(self,rows):
        result=[];seen=set()
        for position,row in enumerate(rows):
            item=dict(row);key=str(item.get('id') or position)
            if key in seen:continue
            seen.add(key);item['_key']=key;item['_position']=position;result.append(item)
        return result
    def rank(self,subjects,context):return sorted([self.evaluate(item,context) for item in self.normalize(subjects)],key=lambda result:(not result.accepted,-result.value,result.key))
    def allocate(self,subjects,context):
        remaining=decimal.Decimal(str(context.get('capacity',100)))-decimal.Decimal(str(context.get('reserved',0)));accepted=[];rejected=[]
        by_key={str(item.get('id')):item for item in self.normalize(subjects)}
        for result in self.rank(subjects,context):
            demand=decimal.Decimal(str(by_key[result.key].get('demand',1)))
            if result.accepted and demand<=remaining:accepted.append(result);remaining-=demand
            else:rejected.append(AnalysisResult(result.key,False,result.value,result.reasons+('allocation_exhausted',),result.facts))
        return accepted,rejected
    def summarize(self,results):
        values=[float(result.value) for result in results];reasons=Counter(reason for result in results for reason in result.reasons)
        return {'count':len(results),'accepted':sum(result.accepted for result in results),'rejected':sum(not result.accepted for result in results),'mean':statistics.fmean(values) if values else 0.0,'median':statistics.median(values) if values else 0.0,'reasons':dict(sorted(reasons.items()))}
    def compare(self,left,right):
        a={item.key:item for item in left};b={item.key:item for item in right};changes=[]
        for key in sorted(set(a)|set(b)):
            old=a.get(key);new=b.get(key)
            if old is None:changes.append({'key':key,'change':'added','accepted':new.accepted})
            elif new is None:changes.append({'key':key,'change':'removed','accepted':old.accepted})
            elif old.accepted!=new.accepted or old.value!=new.value:changes.append({'key':key,'change':'changed','from':str(old.value),'to':str(new.value),'accepted_from':old.accepted,'accepted_to':new.accepted})
        return {'changes':changes,'change_count':len(changes)}

class ScheduleEngine(ScheduleEngineBase):
    def __init__(self):
        self.itinerary = ItineraryPlanner()
        self.change_impact_analyzer = ChangeImpactAnalyzer(self.itinerary)
    def evaluate(self,item,context):
        if item.get('activities'):
            plan=self.itinerary.plan(item['activities'],context)
            facts=dict(plan)
            if item.get('schedule_changes'):
                facts['change_impact']=self.change_impact_analyzer.analyze(
                    item['activities'],item['schedule_changes'],context)
            accepted=not plan['rejected']
            return AnalysisResult(
                str(item.get('id','unknown')),accepted,
                decimal.Decimal(len(plan['chosen'])),
                tuple(reason for row in plan['rejected'] for reason in row['reasons']),facts)
        key=str(item.get('id','unknown'));start=dt.datetime.fromisoformat(str(item.get('start','2026-11-16T09:00:00+00:00')));end=dt.datetime.fromisoformat(str(item.get('end','2026-11-16T10:00:00+00:00')));reasons=[]
        if end<=start:reasons.append('invalid_interval')
        travel=int(item.get('travel_minutes',0));rest=int(item.get('minimum_rest_minutes',0))
        for existing in context.get('bookings',[]):
            left=dt.datetime.fromisoformat(existing['start']);right=dt.datetime.fromisoformat(existing['end'])
            if start<right+dt.timedelta(minutes=travel+rest) and left<end+dt.timedelta(minutes=travel+rest):reasons.append('schedule_conflict');break
        prerequisites=set(item.get('prerequisites',[]));completed=set(context.get('completed_courses',[]))
        if prerequisites-completed:reasons.append('prerequisite_missing')
        value=decimal.Decimal(str((end-start).total_seconds()/3600)) if end>start else decimal.Decimal('0');return AnalysisResult(key,not reasons,value,tuple(reasons),{'duration_hours':str(value),'travel_minutes':travel,'rest_minutes':rest})
