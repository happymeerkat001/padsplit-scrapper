"""Project a complete encrypted private snapshot into an isolated staging DB.

The collector fetches at most recent context for some chats. Absence from the
snapshot is never interpreted as deletion. No property is inferred from an
address or room label; each chat requires an operator-reviewed scope binding.
This tool is a staging rehearsal, not an ongoing production sync.
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path
from psycopg.types.json import Jsonb
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from padsplit_scraper.inbox_display import source_display
from backend.storage.migrate import canonical, key_from_env, read_backup, encrypted_backup, source_parts, staging_connect, verify_records

_IDENT=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_RUN=re.compile(r'^[a-f0-9]{32}$')


def _timestamp(value):
    if not isinstance(value,str) or len(value)>100: raise ValueError('Invalid source timestamp')
    try: stamp=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: raise ValueError('Invalid source timestamp') from None
    if stamp.tzinfo is None: raise ValueError('Timestamp requires timezone')
    return stamp


def _map(rec):
    source_parts(rec)
    value=rec['data'].get('value')
    if not isinstance(value,dict):raise ValueError('Invalid source document')
    return value


def _unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON field')
        result[key]=value
    return result


def snapshot_from_records(records):
    index={}
    for rec in records:
        source_parts(rec); path=rec['path']
        if path in index:raise ValueError('Duplicate source document')
        index[path]=rec
    pointer=index.get('private_dashboard/current')
    if not pointer:raise ValueError('Private snapshot missing')
    fields=_map(pointer); run=fields.get('run_id'); count=fields.get('chunk_count'); stamp=fields.get('scraped_at')
    if type(fields.get('schema_version')) is not int or fields['schema_version']!=1 or not isinstance(run,str) or not _RUN.fullmatch(run) or type(count) is not int or not 1<=count<=100:raise ValueError('Invalid snapshot metadata')
    _timestamp(stamp)
    pieces=[]
    for n in range(count):
        record=index.get(f'private_dashboard_runs/{run}/chunks/{n}')
        if not record:raise ValueError('Incomplete private snapshot')
        part=_map(record);text=part.get('data')
        if type(part.get('index')) is not int or part['index']!=n or not isinstance(text,str) or len(text)>400000 or not text.isascii():raise ValueError('Invalid snapshot chunk')
        pieces.append(text)
    try: payload=json.loads(''.join(pieces),object_pairs_hook=_unique_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
    except (ValueError,TypeError,RecursionError):raise ValueError('Invalid snapshot JSON') from None
    if not isinstance(payload,dict) or payload.get('scraped_at')!=stamp or not isinstance(payload.get('messages'),list) or not isinstance(payload.get('tasks'),dict) or any(not isinstance(v,list) for v in payload['tasks'].values()):raise ValueError('Invalid snapshot payload')
    return payload


def _source_id(value):
    if not isinstance(value,str) or not 1<=len(value)<=2000 or any(ord(ch)<32 for ch in value):raise ValueError('Invalid source identity')
    return value


def _digest(kind,*parts):
    value=json.dumps([kind,*parts],ensure_ascii=True,separators=(',',':')).encode()
    return kind+'_'+hashlib.sha256(value).hexdigest()


def _sender(message):
    sender=message.get('sender')
    if sender is None:return None
    if not isinstance(sender,dict):raise ValueError('Invalid sender')
    name=sender.get('displayName')
    if name is None or name=='':
        pieces=[sender.get('firstName'),sender.get('lastName')]
        if any(v is not None and not isinstance(v,str) for v in pieces):raise ValueError('Invalid sender name')
        name=' '.join(v for v in pieces if v) or None
    if name is not None and (not isinstance(name,str) or len(name)>500):raise ValueError('Invalid sender name')
    return name

def _attachments(message):
    items=message.get('attachments')
    if items is None:return None
    if not isinstance(items,list) or len(items)>100:raise ValueError('Invalid attachment metadata')
    result=[]
    for item in items:
        if not isinstance(item,dict):raise ValueError('Invalid attachment metadata')
        entry={};identity=item.get('id')
        if identity is not None:entry['id']=_digest('attachment',_source_id(identity))
        for field,limit in (('mediaType',200),('filename',500)):
            value=item.get(field)
            if value is not None:
                if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 for c in value):raise ValueError('Invalid attachment metadata')
                if field=='filename' and any(c in value for c in '/\\:?#'):continue
                if field=='mediaType' and not re.fullmatch(r'[A-Za-z0-9.+_-]+(?:/[A-Za-z0-9.+_-]+)?',value):continue
                entry[field]=value
        deleted=item.get('deleted',False)
        if type(deleted) is not bool:raise ValueError('Invalid attachment metadata')
        entry['deleted']=deleted
        result.append(entry)
    return result


def projection_rows(payload,bindings):
    if not isinstance(payload,dict) or not isinstance(payload.get('messages'),list) or not isinstance(bindings,dict):raise ValueError('Invalid inbox input')
    observed=_timestamp(payload.get('scraped_at')); conversations=[]; messages=[]; seen=set()
    for chat in payload['messages']:
        if not isinstance(chat,dict):raise ValueError('Invalid conversation')
        chat_id=_source_id(chat.get('id'))
        if chat_id in seen:raise ValueError('Duplicate conversation')
        seen.add(chat_id)
        if chat_id not in bindings:raise ValueError('Conversation requires reviewed property scope')
        prop=bindings[chat_id]
        if prop is not None and (not isinstance(prop,str) or not _IDENT.fullmatch(prop)):raise ValueError('Invalid property scope')
        title=chat.get('title')
        if title is not None and (not isinstance(title,str) or len(title)>1000):raise ValueError('Invalid conversation title')
        conv_id=_digest('chat',chat_id); path='padsplit_chat/'+conv_id
        member=chat.get('member')
        if member is not None and not isinstance(member,dict):raise ValueError('Invalid unread state')
        direct=chat.get('unread'); nested=member.get('isUnread') if member is not None else None
        if any(value is not None and type(value) is not bool for value in (direct,nested)) or (direct is not None and nested is not None and direct!=nested):raise ValueError('Invalid unread state')
        unread=direct if direct is not None else nested
        conversations.append(dict(id=conv_id,property_id=prop,title=title,observed=observed,path=path,unread=unread,display=source_display(chat)))
        recent=chat.get('recent_messages',[])
        if not isinstance(recent,list):raise ValueError('Invalid recent messages')
        last=chat.get('lastMessage')
        if last is not None and not isinstance(last,dict):raise ValueError('Invalid last message')
        unique={}
        for msg in recent+([last] if last else []):
            if not isinstance(msg,dict):raise ValueError('Invalid message')
            msg_id=_source_id(msg.get('id'));deleted=msg.get('deleted',False)
            if type(deleted) is not bool:raise ValueError('Invalid deletion flag')
            body=msg.get('text')
            if body is None:body=''
            if not isinstance(body,str) or len(body)>100000:raise ValueError('Invalid message text')
            timestamp=_timestamp(msg['created']) if msg.get('created') is not None else None
            row=dict(id=_digest('message',chat_id,msg_id),conversation_id=conv_id,body='' if deleted else body,
                     sent_at=timestamp,sender=_sender(msg),deleted=deleted,attachments=_attachments(msg),path='padsplit_message/'+_digest('message',chat_id,msg_id))
            prior=unique.get(msg_id)
            if prior is not None and prior!=row:raise ValueError('Conflicting copies of a message')
            unique[msg_id]=row
        messages.extend(unique.values())
    if set(bindings)!=seen:raise ValueError('Unused or missing conversation scope')
    return conversations,messages


def load_bindings(path):
    source=Path(path)
    if source.is_symlink():raise ValueError('Mapping file must not be a symlink')
    resolved=source.resolve(strict=True);repo=Path(__file__).resolve().parents[2]
    parent=resolved.parent.stat()
    if resolved.is_relative_to(repo) or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or parent.st_mode & 0o077:
        raise ValueError('Mapping directory must be private and outside the repository')
    fd=os.open(resolved,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    try:
        meta=os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or meta.st_mode & 0o077 or meta.st_size>1000000:raise ValueError('Mapping file must be private and bounded')
        with os.fdopen(fd,'r',encoding='utf8') as stream:
            fd=-1; data=json.load(stream,object_pairs_hook=_unique_pairs)
    finally:
        if fd!=-1:os.close(fd)
    if not isinstance(data,dict) or set(data)!={'version','bindings'} or type(data['version']) is not int or data['version']!=1 or not isinstance(data['bindings'],list) or len(data['bindings'])>10000:
        raise ValueError('Invalid mapping document')
    result={}
    for item in data['bindings']:
        if not isinstance(item,dict) or set(item)!={'chat_id','property_id'}:raise ValueError('Invalid binding')
        chat=_source_id(item['chat_id']);prop=item['property_id']
        if chat in result or (prop is not None and (not isinstance(prop,str) or not _IDENT.fullmatch(prop))):raise ValueError('Invalid or duplicate binding')
        result[chat]=prop
    return result


def project(conn, records, key, bindings, *, apply=False):
    snapshot=snapshot_from_records(records)
    conversations,messages=projection_rows(snapshot,bindings)
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        conn.execute('LOCK TABLE properties IN SHARE MODE')
        conn.execute('LOCK TABLE conversations IN SHARE ROW EXCLUSIVE MODE')
        conn.execute('LOCK TABLE messages IN SHARE ROW EXCLUSIVE MODE')
        verify_records(conn,records,key)
        for row in conversations:
            if row['property_id'] is not None and not conn.execute('SELECT 1 FROM properties WHERE id=%s',(row['property_id'],)).fetchone():raise ValueError('Mapped property missing')
            existing=conn.execute('SELECT id,property_id,source_title,source_observed_at,updated_at,source_path,source_unread,source_display FROM conversations WHERE id=%s OR source_path=%s FOR UPDATE',(row['id'],row['path'])).fetchall()
            expected=(row['id'],row['property_id'],row['title'],row['observed'],row['observed'],row['path'],row['unread'],row['display'])
            if existing and existing!=[expected]:raise ValueError('Conversation conflict requires reconciliation')
            if not existing and apply:
                conn.execute('INSERT INTO conversations(id,property_id,source_title,source_observed_at,updated_at,source_path,source_unread,source_display) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(*expected[:-1],Jsonb(expected[-1]) if expected[-1] is not None else None))
        for row in messages:
            existing=conn.execute('SELECT id,conversation_id,body,sent_at,sender_label,source_deleted,source_path,source_attachments FROM messages WHERE id=%s OR source_path=%s FOR UPDATE',(row['id'],row['path'])).fetchall()
            expected=(row['id'],row['conversation_id'],row['body'],row['sent_at'],row['sender'],row['deleted'],row['path'],row['attachments'])
            if existing and existing!=[expected]:raise ValueError('Message conflict requires reconciliation')
            if not existing and apply:
                conn.execute('INSERT INTO messages(id,conversation_id,body,sent_at,sender_label,source_deleted,source_path,source_attachments) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(*expected[:-1],Jsonb(expected[-1]) if expected[-1] is not None else None))
    return len(conversations),len(messages)


def _observation_stamp(payload):
    raw=payload.get('scraped_at')
    if isinstance(raw,str) and re.search(r'\.\d{7,}(?:Z|[+-]\d\d:\d\d)$',raw):
        raise ValueError('Observation precision exceeds PostgreSQL timestamp precision')
    return _timestamp(raw)


def _run_id(records):
    pointer=next((r for r in records if r['path']=='private_dashboard/current'),None)
    if pointer is None:raise ValueError('Private snapshot missing')
    return _map(pointer)['run_id']

def validate_provider_capture(capture):
    """Validate the complete marked encrypted-collector record set."""
    payload=snapshot_from_records(capture);index={r['path']:r for r in capture}
    if len(index)!=len(capture) or 'inbox_collection/source' not in index:raise ValueError('Missing collection provenance')
    marker=_map(index['inbox_collection/source'])
    if marker!={'kind':'provider_graphql_collection_v1','coverage':'filtered_non_atomic','observed_at':payload['scraped_at']}:raise ValueError('Invalid collection provenance')
    pointer=_map(index['private_dashboard/current']);run=pointer['run_id'];count=pointer['chunk_count']
    expected={'inbox_collection/source','private_dashboard/current'}|{f'private_dashboard_runs/{run}/chunks/{i}' for i in range(count)}
    if set(index)!=expected:raise ValueError('Invalid provider capture records')
    return payload


def combine_initial_capture(source_path,capture_path,output_path,key):
    """Combine source and an explicitly marked provider capture for staging.

    The original source backup remains authoritative and unchanged. This is
    allowed only when it has no private snapshot pointer; a capture never
    silently replaces an existing source document.
    """
    from padsplit_scraper.inbox_collection import _private_path
    output=_private_path(output_path)
    if output in (Path(source_path).resolve(),Path(capture_path).resolve()):raise ValueError('Output must differ from inputs')
    baseline=read_backup(source_path,key);capture=read_backup(capture_path,key)
    base_paths=[r.get('path') for r in baseline];capture_paths=[r.get('path') for r in capture]
    if len(set(base_paths))!=len(base_paths) or 'private_dashboard/current' in base_paths:raise ValueError('Existing source snapshot requires explicit reconciliation')
    validate_provider_capture(capture);expected=set(capture_paths)
    if set(base_paths)&expected:raise ValueError('Source capture conflicts with baseline')
    encrypted_backup([*baseline,*capture],output,key)
    return len(baseline),len(capture)


def _snapshot_aad(run_id,observed):
    return canonical(['padsplit-inbox-catchup',run_id,observed.astimezone(dt.timezone.utc).isoformat()])


def _snapshot_fingerprint(key,plain):
    return hmac.new(key,b'padsplit-inbox-catchup\0'+plain,hashlib.sha256).digest()


def catch_up(conn, baseline_path, candidate_path, key, bindings, *, apply=False):
    """Apply one authenticated later export to the local staged inbox.

    The authenticated baseline must still match all staged encrypted source
    records. The complete candidate and its reviewed mapping are encrypted in
    the same transaction as changes. Missing conversations/messages are not
    deleted. Any changed existing message or property assignment stops all work.
    """
    baseline=read_backup(baseline_path,key);candidate=read_backup(candidate_path,key)
    return catch_up_records(conn,baseline,candidate,key,bindings,apply=apply)


def catch_up_records(conn, baseline, candidate, key, bindings, *, apply=False):
    """Catch up verified in-memory records using the existing atomic checks."""
    if not isinstance(key,bytes) or len(key)!=32:raise ValueError('Invalid migration key')
    before=snapshot_from_records(baseline);after=snapshot_from_records(candidate)
    base_time=_observation_stamp(before);observed=_observation_stamp(after)
    base_run=_run_id(baseline);run_id=_run_id(candidate)
    if run_id==base_run or observed<=base_time:raise ValueError('Candidate must be a later distinct snapshot')
    conversations,messages=projection_rows(after,bindings)
    # Validate the initial projection without inferring its original property
    # mappings. Identity and immutable message content must still exist.
    baseline_ids=[_source_id(chat.get('id')) for chat in before['messages'] if isinstance(chat,dict)]
    baseline_conversations,baseline_messages=projection_rows(before,{ident:None for ident in baseline_ids})
    plain=canonical({'records':sorted(candidate,key=lambda x:x['path']),'bindings':bindings})
    if len(plain)>64_000_000:raise ValueError('Candidate backup too large for staging catch-up')
    fingerprint=_snapshot_fingerprint(key,plain)
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        conn.execute('LOCK TABLE properties IN SHARE MODE')
        conn.execute('LOCK TABLE conversations IN SHARE ROW EXCLUSIVE MODE')
        conn.execute('LOCK TABLE messages IN SHARE ROW EXCLUSIVE MODE')
        conn.execute('LOCK TABLE inbox_snapshot_runs IN SHARE ROW EXCLUSIVE MODE')
        verify_records(conn,baseline,key)
        for row in baseline_conversations:
            existing=conn.execute('SELECT id,source_path,source_observed_at FROM conversations WHERE id=%s',(row['id'],)).fetchone()
            if not existing or existing[0]!=row['id'] or existing[1]!=row['path'] or existing[2] is None or existing[2]<base_time:
                raise ValueError('Baseline conversation projection missing or changed')
        for row in baseline_messages:
            existing=conn.execute('SELECT id,conversation_id,body,sent_at,sender_label,source_deleted,source_path,source_attachments FROM messages WHERE id=%s',(row['id'],)).fetchone()
            expected=(row['id'],row['conversation_id'],row['body'],row['sent_at'],row['sender'],row['deleted'],row['path'],row['attachments'])
            if existing!=expected:raise ValueError('Baseline message projection missing or changed')
        latest=base_time;matching=None
        for stored_id,stored_time,cipher,nonce,digest,version in conn.execute('SELECT run_id,observed_at,ciphertext,nonce,fingerprint,key_version FROM inbox_snapshot_runs ORDER BY observed_at').fetchall():
            if version!='v1' or stored_time<=latest:raise ValueError('Invalid staged observation history')
            try: stored_plain=AESGCM(key).decrypt(bytes(nonce),bytes(cipher),_snapshot_aad(stored_id,stored_time))
            except Exception:raise ValueError('Staged observation authentication failed') from None
            if not hmac.compare_digest(bytes(digest),_snapshot_fingerprint(key,stored_plain)):
                raise ValueError('Staged observation fingerprint mismatch')
            latest=stored_time
            if stored_id==run_id:matching=(stored_time,bytes(digest))
        if observed<latest or (observed==latest and matching!=(observed,fingerprint)) or (matching is not None and matching!=(observed,fingerprint)):
            raise ValueError('Observation is stale or conflicts with a previous run')
        max_observed=conn.execute("SELECT max(source_observed_at) FROM conversations WHERE source_path LIKE 'padsplit_chat/%'").fetchone()[0]
        if max_observed is not None and (observed<max_observed or (observed==max_observed and matching is None)):
            raise ValueError('Observation predates or conflicts with staged conversations')
        for row in conversations:
            if row['property_id'] is not None and not conn.execute('SELECT 1 FROM properties WHERE id=%s',(row['property_id'],)).fetchone():raise ValueError('Mapped property missing')
            existing=conn.execute('SELECT id,property_id,source_title,source_observed_at,updated_at,source_path,source_unread,source_display FROM conversations WHERE id=%s OR source_path=%s FOR UPDATE',(row['id'],row['path'])).fetchall()
            if existing:
                if len(existing)!=1 or existing[0][0]!=row['id'] or existing[0][1]!=row['property_id'] or existing[0][5]!=row['path'] or existing[0][3] is None or existing[0][3]>observed:
                    raise ValueError('Conversation mapping or observation conflict')
                if matching is not None and (existing[0][2],existing[0][3],existing[0][4],existing[0][6],existing[0][7])!=(row['title'],observed,observed,row['unread'],row['display']):
                    raise ValueError('Repeated observation differs from staged conversation')
                if apply and matching is None:
                    conn.execute('UPDATE conversations SET source_title=%s,source_observed_at=%s,updated_at=%s,source_unread=%s,source_display=%s WHERE id=%s',(row['title'],observed,observed,row['unread'],Jsonb(row['display']) if row['display'] is not None else None,row['id']))
            elif matching is not None:raise ValueError('Repeated observation is missing a staged conversation')
            elif apply:conn.execute('INSERT INTO conversations(id,property_id,source_title,source_observed_at,updated_at,source_path,source_unread,source_display) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],row['property_id'],row['title'],observed,observed,row['path'],row['unread'],Jsonb(row['display']) if row['display'] is not None else None))
        for row in messages:
            existing=conn.execute('SELECT id,conversation_id,body,sent_at,sender_label,source_deleted,source_path,source_attachments FROM messages WHERE id=%s OR source_path=%s FOR UPDATE',(row['id'],row['path'])).fetchall()
            expected=(row['id'],row['conversation_id'],row['body'],row['sent_at'],row['sender'],row['deleted'],row['path'],row['attachments'])
            if existing and existing!=[expected]:raise ValueError('Existing message changed; reconciliation required')
            if not existing and matching is not None:raise ValueError('Repeated observation is missing a staged message')
            if not existing and apply:conn.execute('INSERT INTO messages(id,conversation_id,body,sent_at,sender_label,source_deleted,source_path,source_attachments) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(*expected[:-1],Jsonb(expected[-1]) if expected[-1] is not None else None))
        if apply and matching is None:
            nonce=os.urandom(12);cipher=AESGCM(key).encrypt(nonce,plain,_snapshot_aad(run_id,observed))
            conn.execute('INSERT INTO inbox_snapshot_runs(run_id,observed_at,ciphertext,nonce,fingerprint,key_version) VALUES (%s,%s,%s,%s,%s,%s)',(run_id,observed,cipher,nonce,fingerprint,'v1'))
    return len(conversations),len(messages)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Project private snapshot inbox into isolated staging')
    parser.add_argument('--input',required=True);parser.add_argument('--mapping',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--baseline',help='Original authenticated export for later snapshot rehearsal')
    args=parser.parse_args(argv)
    try:
        key=key_from_env();bindings=load_bindings(args.mapping)
        with staging_connect() as conn:
            if args.baseline:counts=catch_up(conn,args.baseline,args.input,key,bindings,apply=args.apply)
            else:counts=project(conn,read_backup(args.input,key),key,bindings,apply=args.apply)
        print(f'{"Staging projection" if args.apply else "Projection dry run"}: {counts[0]} conversations, {counts[1]} observed messages validated.')
    except Exception as exc:
        print(f'Inbox projection stopped ({type(exc).__name__}); source and reviewed scope require reconciliation.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
