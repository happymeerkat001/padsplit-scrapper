"""Explicitly mapped, read-only thermostat snapshot staging projection.

No source service is contacted, no device is controlled, and no location name
is used to infer a property scope. Unknown source fields stop the projection.
"""
import argparse, datetime as dt, hashlib, json, math, os, re, stat, sys
from pathlib import Path
from psycopg.types.json import Jsonb
from backend.storage.migrate import staging_connect

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_DEVICE={'id','name','temp','heat_setpoint','cool_setpoint','humidity','outdoor_temp','mode','equipment_status'}
_BINDING={'source_location_id','target_property_id','expected_property_name'}

def _source_id(value):
    if type(value) not in (str,int) or not str(value).strip() or len(str(value))>200 or any(ord(c)<32 for c in str(value)):
        raise ValueError('Invalid source identity')
    return json.dumps(value,ensure_ascii=True,separators=(',',':'))

def _timestamp(value):
    if not isinstance(value,str) or len(value)>100:raise ValueError('Invalid observation time')
    try: parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('Invalid observation time') from None
    if parsed.tzinfo is None:raise ValueError('Timezone required')
    return parsed

def projection_rows(payload,bindings):
    if not isinstance(payload,dict) or set(payload)!={'scraped_at','locations'} or not isinstance(payload['locations'],list) or not isinstance(bindings,list):raise ValueError('Invalid climate snapshot')
    observed=_timestamp(payload['scraped_at']); mappings={};seen_locations=set();seen_devices=set();rows=[]
    fingerprint=hashlib.sha256(json.dumps(payload,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    for item in bindings:
        if not isinstance(item,dict) or set(item)!=_BINDING:raise ValueError('Invalid reviewed mapping')
        source=_source_id(item['source_location_id']);prop=item['target_property_id'];name=item['expected_property_name']
        if source in mappings or not isinstance(prop,str) or not _ID.fullmatch(prop) or not isinstance(name,str) or not name.strip() or len(name)>1000:raise ValueError('Invalid reviewed mapping')
        mappings[source]=item
    for location in payload['locations']:
        if not isinstance(location,dict) or set(location)!={'id','name','devices'} or not isinstance(location['devices'],list):raise ValueError('Invalid location')
        source=_source_id(location['id']);name=location['name']
        if source in seen_locations or source not in mappings or not isinstance(name,str) or len(name)>1000:raise ValueError('Location needs reviewed scope')
        seen_locations.add(source);binding=mappings[source]
        for device in location['devices']:
            if not isinstance(device,dict) or set(device)!=_DEVICE:raise ValueError('Unsupported device fields')
            identity=_source_id(device['id']);pair=json.dumps([source,identity],separators=(',',':'))
            if pair in seen_devices:raise ValueError('Duplicate device')
            seen_devices.add(pair)
            if device['name'] is not None and (not isinstance(device['name'],str) or len(device['name'])>500):raise ValueError('Invalid device name')
            for key in ('temp','heat_setpoint','cool_setpoint','humidity','outdoor_temp'):
                value=device[key]
                if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):raise ValueError('Invalid reading')
            for key in ('mode','equipment_status'):
                value=device[key]
                if value is not None and (type(value) not in (str,int) or len(str(value))>200):raise ValueError('Invalid device state')
            device_key='climate_'+hashlib.sha256(pair.encode()).hexdigest()
            observation='climate_observation_'+hashlib.sha256((pair+'\0'+observed.astimezone(dt.timezone.utc).isoformat()).encode()).hexdigest()
            data={key:value for key,value in device.items() if key!='id'};data['location_name']=name
            rows.append({'id':observation,'device_key':device_key,'property_id':binding['target_property_id'],'expected_name':binding['expected_property_name'],'observed_at':observed,'data':data,'source_fingerprint':fingerprint})
    if seen_locations!=set(mappings):raise ValueError('Unused reviewed mapping')
    return rows

def project(conn,payload,bindings,*,apply=False):
    rows=projection_rows(payload,bindings);conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE properties IN SHARE MODE')
        conn.execute('LOCK TABLE climate_observations IN SHARE ROW EXCLUSIVE MODE')
        for row in rows:
            prop=conn.execute('SELECT name FROM properties WHERE id=%s',(row['property_id'],)).fetchone()
            if not prop or prop[0]!=row['expected_name']:raise ValueError('Reviewed property mismatch')
            scopes=conn.execute('SELECT DISTINCT property_id FROM climate_observations WHERE device_key=%s',(row['device_key'],)).fetchall()
            if any(scope[0]!=row['property_id'] for scope in scopes):raise ValueError('Device scope conflict')
            existing=conn.execute('SELECT id,device_key,property_id,observed_at,data,source_fingerprint FROM climate_observations WHERE id=%s OR (device_key=%s AND observed_at=%s) FOR UPDATE',(row['id'],row['device_key'],row['observed_at'])).fetchall()
            expected=(row['id'],row['device_key'],row['property_id'],row['observed_at'],row['data'],row['source_fingerprint'])
            if existing and existing!=[expected]:raise ValueError('Observation conflict')
            if not existing and apply:conn.execute('INSERT INTO climate_observations(id,device_key,property_id,observed_at,data,source_fingerprint) VALUES (%s,%s,%s,%s,%s,%s)',(row['id'],row['device_key'],row['property_id'],row['observed_at'],Jsonb(row['data']),row['source_fingerprint']))
    return len(rows)

def _pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate JSON field')
        out[key]=value
    return out

def private_json(path):
    source=Path(path)
    if not source.is_absolute() or source.is_symlink():raise ValueError('Private source required')
    resolved=source.resolve(strict=True);root=Path(__file__).resolve().parents[2];parent=resolved.parent.stat()
    if resolved.is_relative_to(root) or parent.st_uid!=os.geteuid() or parent.st_mode&0o077 or not stat.S_ISDIR(parent.st_mode):raise ValueError('Private directory required')
    fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>20_000_000:raise ValueError('Private file required')
        with os.fdopen(fd,'r',encoding='utf8') as stream:
            fd=-1;return json.load(stream,object_pairs_hook=_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
    finally:
        if fd!=-1:os.close(fd)

def main(argv=None):
    parser=argparse.ArgumentParser(description='Project reviewed private thermostat observations into staging')
    parser.add_argument('--input',required=True);parser.add_argument('--bindings',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    try:
        source=private_json(args.input);mapping=private_json(args.bindings)
        if not isinstance(mapping,dict) or set(mapping)!={'version','bindings'} or type(mapping['version']) is not int or mapping['version']!=1:raise ValueError('Invalid mapping')
        with staging_connect() as conn: count=project(conn,source,mapping['bindings'],apply=args.apply)
        print(f'{"Staging projection" if args.apply else "Projection dry run"}: {count} climate readings validated.')
    except Exception as exc:
        print(f'Climate projection stopped ({type(exc).__name__}); reconcile private inputs.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
