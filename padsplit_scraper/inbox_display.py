"""Allowlisted private inbox display fields; never carry provider identifiers or URLs."""
import re

_KEYS={'first_name','last_name','room_number','street','city','state'}
_URL=re.compile(r'(?:https?://|www\.|[?&](?:token|key|secret|signature)=)', re.I)


def validate_display(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-_KEYS:raise ValueError('Invalid conversation display')
    result={}
    for key,item in value.items():
        if item is not None:
            if key=='room_number' and type(item) is int:item=str(item)
            if not isinstance(item,str) or len(item)>500 or any(ord(c)<32 for c in item) or _URL.search(item):
                raise ValueError('Invalid conversation display field')
        result[key]=item
    return result


def source_display(chat):
    if 'source_display' in chat:return validate_display(chat['source_display'])
    paths={'first_name':('occupancy','user','firstName'), 'last_name':('occupancy','user','lastName'),
           'room_number':('occupancy','room','roomNumber'), 'street':('property','address','street1'),
           'city':('property','address','city','name'), 'state':('property','address','city','state','name')}
    display={}
    for dest,path in paths.items():
        current=chat
        for key in path:
            if current is None:break
            if not isinstance(current,dict):raise ValueError('Invalid conversation display source')
            if key not in current:break
            current=current[key]
        else:display[dest]=current
    return validate_display(display) if display else None
