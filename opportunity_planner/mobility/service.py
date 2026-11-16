from __future__ import annotations
import datetime as dt
import decimal
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

class MobilityServiceError(RuntimeError):
    pass

@dataclass(frozen=True)
class MobilityServiceOutcome:
    shift_id: str
    status: str
    reasons: tuple[str,...]
    measures: Mapping[str,Any]

class MobilityService:
    source_kind='shift'
    consumer_kind='interview'
    def validate(self, value: Mapping[str,Any]) -> dict[str,Any]:
        item=dict(value)
        identifier=str(item.get('shift_id') or item.get('id') or '').strip()
        if len(identifier)<2: raise MobilityServiceError('shift_id is required')
        item['shift_id']=identifier
        quantity=int(item.get('quantity',0))
        if quantity<0: raise MobilityServiceError('quantity cannot be negative')
        item['quantity']=quantity
        amount=decimal.Decimal(str(item.get('amount','0'))).quantize(decimal.Decimal('0.01'))
        if amount<0: raise MobilityServiceError('amount cannot be negative')
        item['amount']=amount
        return item

    def decide(self, value: Mapping[str,Any], related: Sequence[Mapping[str,Any]], now: dt.datetime) -> MobilityServiceOutcome:
        item=self.validate(value); identifier=item['shift_id']; reasons=[]
        active=[row for row in related if row.get('status') in {'active','received','validated'}]
        owned=[row for row in active if row.get('owner_id')==item.get('owner_id')]
        total_quantity=sum(int(row.get('quantity',0)) for row in owned)
        total_amount=sum((decimal.Decimal(str(row.get('amount','0'))) for row in owned),decimal.Decimal('0'))
        if item.get('effective_to') and str(item['effective_to'])<str(item.get('effective_from','')): reasons.append('invalid_effective_range')
        if item['quantity']+total_quantity>int(item.get('quantity_limit',1000000)): reasons.append('quantity_limit_exceeded')
        if item['amount']+total_amount>decimal.Decimal(str(item.get('amount_limit','1000000000'))): reasons.append('amount_limit_exceeded')
        status='accepted' if not reasons else 'rejected'
        measures={'related_active':len(active),'owner_active':len(owned),'owner_quantity':total_quantity,'owner_amount':str(total_amount),'evaluated_at':now.isoformat(),'consumer_kind':self.consumer_kind}
        return MobilityServiceOutcome(identifier,status,tuple(reasons),measures)

    def group(self, values: Iterable[Mapping[str,Any]], key: str='owner_id') -> dict[str,list[dict[str,Any]]]:
        result=defaultdict(list)
        for value in values:
            item=self.validate(value); result[str(item.get(key,''))].append(item)
        return dict(result)

    def checksum(self, values: Sequence[Mapping[str,Any]]) -> str:
        normalized=[]
        for value in values:
            item=self.validate(value); normalized.append('|'.join([item['shift_id'],str(item.get('owner_id','')),str(item['quantity']),str(item['amount'])]))
        return hashlib.sha256('\n'.join(sorted(normalized)).encode()).hexdigest()
