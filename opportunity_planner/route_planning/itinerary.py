from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Mapping,Sequence
@dataclass(frozen=True)
class Activity:
 id:str;kind:str;start:dt.datetime;end:dt.datetime;location:str;mandatory:bool=False
class ItineraryPlanner:
 def activity(self,row):
  start=dt.datetime.fromisoformat(str(row['start']));end=dt.datetime.fromisoformat(str(row['end']))
  if end<=start:raise ValueError('invalid_interval')
  return Activity(str(row['id']),str(row.get('kind','event')),start,end,str(row.get('location','unknown')),bool(row.get('mandatory',False)))
 def travel(self,left,right,matrix):return int(matrix.get(left,{}).get(right,999))
 def conflict(self,left,right,matrix,rest_minutes):return right.start < left.end+dt.timedelta(minutes=self.travel(left.location,right.location,matrix)+rest_minutes)
 def prerequisites(self,row,completed):return sorted(set(row.get('prerequisites',()))-set(completed))
 def feasible(self,row,chosen,context):
  activity=self.activity(row);reasons=[]
  if self.prerequisites(row,context.get('completed_courses',())):reasons.append('prerequisite_missing')
  for other in chosen:
   if self.conflict(other,activity,context.get('travel_matrix',{}),int(context.get('minimum_rest_minutes',0))):reasons.append('schedule_conflict');break
  if row.get('capacity') is not None and int(row.get('booked',0))>=int(row['capacity']):reasons.append('capacity_exhausted')
  return activity,tuple(reasons)
 def plan(self,rows,context):
  scored=sorted(rows,key=lambda row:(not bool(row.get('mandatory')), -int(row.get('priority',0)), str(row.get('start')),str(row.get('id'))));chosen=[];rejected=[]
  for row in scored:
   activity,reasons=self.feasible(row,chosen,context)
   if reasons:rejected.append({'id':activity.id,'reasons':list(reasons)})
   else:chosen.append(activity)
  return {'chosen':[a.id for a in sorted(chosen,key=lambda a:a.start)],'rejected':rejected,'travel_minutes':sum(self.travel(a.location,b.location,context.get('travel_matrix',{})) for a,b in zip(sorted(chosen,key=lambda a:a.start),sorted(chosen,key=lambda a:a.start)[1:]))}
 def reschedule_after_cancellation(self,rows,cancelled_id,context):return self.plan([row for row in rows if str(row['id'])!=cancelled_id],context)
