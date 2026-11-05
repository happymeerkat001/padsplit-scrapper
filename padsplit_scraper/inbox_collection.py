"""Bounded, read-only PadSplit GraphQL page adapters.

These functions do not authenticate or schedule themselves. The caller supplies
an already-authorized read transport. They never return attachment locations,
source credentials, or provider diagnostic text. Cursors and records are
sensitive and belong only in encrypted private state.
"""
from __future__ import annotations
import time
import datetime as dt
import hashlib
import json
import hmac
import os
from pathlib import Path
import re
import stat
import tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from padsplit_scraper.attachment_metadata import safe_attachment_filename

class SourceError(RuntimeError):
    """Generic collection failure, deliberately excluding source responses."""

def _bounded_string(value,limit,*,optional=False):
    if value is None and optional:return None
    if not isinstance(value,str) or not value or len(value)>limit or any(ord(c)<32 for c in value):raise SourceError('Invalid source value')
    return value

def _get(post,query,variables,attempts):
    if type(attempts) is not int or not 1<=attempts<=5:raise ValueError('Invalid retry bound')
    for attempt in range(attempts):
        try: result=post(query,variables)
        except (TimeoutError,ConnectionError,OSError):
            if attempt+1<attempts:
                time.sleep(min(.25*(2**attempt),1.0));continue
            raise SourceError('Source request unavailable after bounded retries') from None
        except Exception:raise SourceError('Source request failed') from None
        if not isinstance(result,dict) or result.get('errors'):raise SourceError('Source response rejected')
        return result
    raise SourceError('Source unavailable')

def _page(value,after):
    if not isinstance(value,dict) or not isinstance(value.get('edges'),list) or not isinstance(value.get('pageInfo'),dict):raise SourceError('Invalid source page')
    info=value['pageInfo'];more=info.get('hasNextPage')
    if type(more) is not bool:raise SourceError('Invalid source page')
    cursor=info.get('endCursor')
    if more:
        _bounded_string(cursor,4000)
        if cursor==after:raise SourceError('Source cursor did not advance')
    result=[];seen=set()
    for edge in value['edges']:
        if not isinstance(edge,dict) or not isinstance(edge.get('node'),dict):raise SourceError('Invalid source page')
        node=edge['node'];ident=_bounded_string(node.get('id'),2000)
        if ident in seen:raise SourceError('Duplicate source identity')
        seen.add(ident);result.append(node)
    return result,cursor if more else None,not more

def _attachment(value):
    if not isinstance(value,dict):raise SourceError('Invalid attachment metadata')
    result={}
    for key,limit in (('id',2000),('filename',500),('mediaType',200)):
        if value.get(key) is not None:
            text=_bounded_string(value[key],limit)
            if key=='filename':
                if not safe_attachment_filename(text):continue
            if key=='mediaType' and not re.fullmatch(r'[A-Za-z0-9.+_-]+(?:/[A-Za-z0-9.+_-]+)?',text):continue
            result[key]=text
    deleted=value.get('deleted')
    if type(deleted) is not bool:raise SourceError('Invalid attachment metadata')
    result['deleted']=deleted
    return result

def _message(value):
    if not isinstance(value,dict):raise SourceError('Invalid message')
    ident=_bounded_string(value.get('id'),2000);deleted=value.get('deleted')
    if type(deleted) is not bool:raise SourceError('Invalid message')
    text=value.get('text')
    if text is not None and (not isinstance(text,str) or len(text)>100000):raise SourceError('Invalid message')
    out={'id':ident,'text':'' if deleted else text,'deleted':deleted}
    for key,limit in (('created',100),('messageType',200)):
        if value.get(key) is not None:out[key]=_bounded_string(value[key],limit)
    sender=value.get('sender')
    if sender is not None:
        if not isinstance(sender,dict):raise SourceError('Invalid sender')
        out['sender']={k:_bounded_string(sender[k],500) for k in ('displayName','firstName','lastName') if sender.get(k)}
    attachments=value.get('attachments')
    if attachments is not None:
        if not isinstance(attachments,list) or len(attachments)>100:raise SourceError('Invalid attachments')
        out['attachments']=[_attachment(item) for item in attachments]
    return out

def _check_last_message_consistency(summary, detail):
    """Reject changed shared fields without equating partial attachment lists.

    The chat-list query supplies less attachment metadata than the message
    query. Detail may enrich unknown summary fields, but may not discard
    observed scalar/sender fields or all observed attachment metadata.
    """
    for key in ('id','deleted','text','created','messageType'):
        if summary.get(key) is not None and (detail.get(key) is None or summary[key]!=detail[key]):
            raise SourceError('Conflicting last-message observation')
    first,second=summary.get('sender'),detail.get('sender')
    if isinstance(first,dict):
        if any(value is not None and (not isinstance(second,dict) or second.get(key)!=value)
               for key,value in first.items()):
            raise SourceError('Conflicting last-message observation')
    summary_attachments=summary.get('attachments')
    if summary_attachments is not None:
        from collections import Counter
        detail_attachments=detail.get('attachments')
        if detail_attachments is None or len(summary_attachments)!=len(detail_attachments):
            raise SourceError('Conflicting last-message observation')
        # No attachment IDs are present in the summary. Compare aggregate
        # observed states, without claiming an individual identity match.
        for deleted in (False,True):
            left=[a for a in summary_attachments if a['deleted'] is deleted]
            right=[a for a in detail_attachments if a['deleted'] is deleted]
            known=Counter(a['mediaType'] for a in left if a.get('mediaType') is not None)
            available=Counter(a['mediaType'] for a in right if a.get('mediaType') is not None)
            if len(left)!=len(right) or any(count>available[kind] for kind,count in known.items()):
                raise SourceError('Conflicting last-message observation')

def graphql_transport(session):
    """Use an already-authenticated session for only the two known read queries.

    No login, mutation, redirect, or action hook is performed. Authentication
    failure stops immediately and callers receive a redacted error.
    """
    import requests
    from padsplit_scraper.scraper import CHAT_LIST_QUERY, MESSAGE_LIST_QUERY, GRAPHQL_URL, BASE_URL, DEFAULT_TIMEOUT
    def post(query,variables):
        if query not in (CHAT_LIST_QUERY,MESSAGE_LIST_QUERY):raise SourceError('Unsupported source request')
        try:
            response=session.post(GRAPHQL_URL,headers={'Content-Type':'application/json','Accept':'application/json','Referer':BASE_URL+'/host/communication'},
                                  json={'query':query,'variables':variables},timeout=DEFAULT_TIMEOUT,allow_redirects=False)
        except requests.exceptions.RequestException:raise ConnectionError('Source unavailable') from None
        if response.status_code==429 or 500<=response.status_code<=599:raise ConnectionError('Source unavailable')
        if response.status_code!=200:raise SourceError('Source request denied')
        try:return response.json()
        except (ValueError,TypeError):raise SourceError('Invalid source response') from None
    return post

def _inputs(after,limit):
    if after is not None:_bounded_string(after,4000)
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid page limit')

def chat_page(post,after=None,limit=50,*,attempts=3):
    from padsplit_scraper.scraper import CHAT_LIST_QUERY
    _inputs(after,limit)
    variables={'first':limit,'after':after,'searchMember':'','searchProperty':'','moveIn':False,'moveOut':False,'active':False,'archived':False}
    result=_get(post,CHAT_LIST_QUERY,variables,attempts)
    try:nodes,next_cursor,exhausted=_page(result['data']['messenger']['chats'],after)
    except (KeyError,TypeError):raise SourceError('Invalid source page') from None
    chats=[]
    for node in nodes:
        chat={'id':node['id']}
        from padsplit_scraper.inbox_display import source_display
        display=source_display(node)
        if display is not None:chat['source_display']=display
        member=node.get('member')
        if member is not None and not isinstance(member,dict):raise SourceError('Invalid unread state')
        unread=member.get('isUnread') if member is not None else None
        if unread is not None and type(unread) is not bool:raise SourceError('Invalid unread state')
        chat['unread']=unread
        if node.get('title') is not None:chat['title']=_bounded_string(node['title'],1000)
        if node.get('lastMessage') is not None:chat['lastMessage']=_message(node['lastMessage'])
        chats.append(chat)
    return {'items':chats,'next_cursor':next_cursor,'exhausted':exhausted,'coverage':'source chat filters; full coverage unverified'}

def message_page(post,chat_id,after=None,limit=50,*,attempts=3):
    from padsplit_scraper.scraper import MESSAGE_LIST_QUERY
    _inputs(after,limit);_bounded_string(chat_id,2000)
    result=_get(post,MESSAGE_LIST_QUERY,{'chatId':chat_id,'first':limit,'after':after},attempts)
    try:nodes,next_cursor,exhausted=_page(result['data']['messenger']['chat']['messages'],after)
    except (KeyError,TypeError):raise SourceError('Invalid source page') from None
    return {'items':[_message(item) for item in nodes],'next_cursor':next_cursor,'exhausted':exhausted,'coverage':'filtered message types; full history unverified'}

_MAGIC=b'PADINBOX1\n'
def _private_path(path):
    target=Path(path)
    if not target.is_absolute() or target.is_symlink():raise SourceError('Private checkpoint path required')
    parent=target.parent.resolve(strict=True);root=Path(__file__).resolve().parents[1];meta=parent.stat()
    if parent==root or root in parent.parents or meta.st_uid!=os.geteuid() or meta.st_mode&0o077 or not stat.S_ISDIR(meta.st_mode):
        raise SourceError('Private checkpoint directory required')
    return parent/target.name

def _key(key):
    if not isinstance(key,bytes) or len(key)!=32:raise SourceError('Invalid checkpoint key')
    return key

def read_checkpoint(path,key):
    target=_private_path(path);key=_key(key)
    try:
        fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0));meta=os.fstat(fd)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or meta.st_mode&0o077 or meta.st_size>24000000:raise SourceError('Invalid checkpoint file')
            data=stream.read()
        if not data.startswith(_MAGIC):raise SourceError('Invalid checkpoint file')
        blob=data[len(_MAGIC):];state=json.loads(AESGCM(key).decrypt(blob[:12],blob[12:],_MAGIC))
    except Exception:raise SourceError('Checkpoint could not be authenticated') from None
    if not isinstance(state,dict) or state.get('version')!=1 or state.get('phase') not in ('chats','messages','complete') or not isinstance(state.get('chats'),list):raise SourceError('Invalid checkpoint state')
    return state

def _save_checkpoint(path,key,state):
    target=_private_path(path);key=_key(key)
    plain=json.dumps(state,ensure_ascii=True,separators=(',',':')).encode()
    if len(plain)>16000000:raise SourceError('Checkpoint exceeds safe size')
    nonce=os.urandom(12);cipher=_MAGIC+nonce+AESGCM(key).encrypt(nonce,plain,_MAGIC)
    fd,name=tempfile.mkstemp(prefix='.inbox-',dir=target.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as stream:fd=-1;stream.write(cipher);stream.flush();os.fsync(stream.fileno())
        os.replace(name,target)
        directory_fd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        if fd!=-1:os.close(fd)
        if os.path.exists(name):os.unlink(name)

def advance_checkpoint(post,path,key,*,account_tag,max_pages=1,observed_at=None):
    """Fetch bounded pages and atomically persist encrypted resume state.

    One host-local file lock prevents overlap. Collection is non-atomic and
    provider message-type filters prevent a full-history claim. The caller
    controls authorization and scheduling; this never publishes or sends.
    """
    from padsplit_scraper.process_lock import acquire_lock
    target=_private_path(path);_key(key)
    if not isinstance(account_tag,str) or not re.fullmatch(r'[a-f0-9]{64}',account_tag):raise SourceError('Account binding required')
    if type(max_pages) is not int or not 1<=max_pages<=100:raise ValueError('Invalid page bound')
    lock_dir=target.parent/('inbox-lock-'+hashlib.sha256(target.name.encode()).hexdigest()[:16])
    with acquire_lock('inbox-collector',directory=lock_dir,timeout=0):
        if target.exists():
            state=read_checkpoint(target,key)
            if not isinstance(state.get('account_tag'),str) or not hmac.compare_digest(state['account_tag'],account_tag):raise SourceError('Checkpoint account does not match')
        else:
            if not isinstance(observed_at,str):raise SourceError('Observation time required')
            try:stamp=dt.datetime.fromisoformat(observed_at.replace('Z','+00:00'))
            except ValueError:raise SourceError('Invalid observation time') from None
            if stamp.tzinfo is None:raise SourceError('Invalid observation time')
            state={'version':1,'account_tag':account_tag,'phase':'chats','observed_at':observed_at,'chats':[],'cursor':None,'chat_cursors':[],'chat_index':0,'message_cursor':None,'message_cursors':[]}
        for _ in range(max_pages):
            if state['phase']=='complete':break
            if state['phase']=='chats':
                page=chat_page(post,state['cursor']);seen={item['id'] for item in state['chats']}
                for item in page['items']:
                    if item['id'] in seen:raise SourceError('Repeated chat across source pages')
                    seen.add(item['id']);state['chats'].append(item)
                if len(state['chats'])>10000:raise SourceError('Chat collection exceeds safe size')
                next_cursor=page['next_cursor']
                if next_cursor is not None and next_cursor in state['chat_cursors']:raise SourceError('Source cursor cycle')
                if next_cursor is not None:state['chat_cursors'].append(next_cursor)
                state['cursor']=next_cursor
                if page['exhausted']:state['phase']='messages' if state['chats'] else 'complete'
            else:
                chat=state['chats'][state['chat_index']];page=message_page(post,chat['id'],state['message_cursor']);items=chat.setdefault('recent_messages',[])
                seen={item['id']:item for item in items}
                for item in page['items']:
                    last=chat.get('lastMessage')
                    if isinstance(last,dict) and last.get('id')==item['id']:
                        _check_last_message_consistency(last,item)
                    prior=seen.get(item['id'])
                    if prior is not None and prior!=item:raise SourceError('Conflicting message across source pages')
                    if prior is None:items.append(item);seen[item['id']]=item
                if len(items)>10000:raise SourceError('Message collection exceeds safe size')
                next_cursor=page['next_cursor']
                if next_cursor is not None and next_cursor in state['message_cursors']:raise SourceError('Source cursor cycle')
                if next_cursor is not None:state['message_cursors'].append(next_cursor)
                state['message_cursor']=next_cursor
                if page['exhausted']:
                    last=chat.get('lastMessage')
                    if isinstance(last,dict) and last.get('id') in seen:
                        detail=seen[last['id']]
                        _check_last_message_consistency(last,detail)
                        chat['lastMessage']=detail
                    state['chat_index']+=1;state['message_cursor']=None;state['message_cursors']=[]
                    if state['chat_index']==len(state['chats']):state['phase']='complete'
            _save_checkpoint(target,key,state)
        return {'complete':state['phase']=='complete','chats':len(state['chats']),'coverage':'filtered message types; non-atomic; complete history unverified'}

def export_candidate(checkpoint,destination,key):
    """Create an encrypted staging candidate, explicitly marked as collected.

    This is not a Firestore export and must never replace the source backup.
    Only a finished checkpoint is eligible. Existing files are never overwritten.
    """
    from backend.storage.migrate import encode_value,encrypted_backup
    state=read_checkpoint(checkpoint,key);target=_private_path(destination)
    if state['phase']!='complete':raise SourceError('Collection has not finished')
    payload={'scraped_at':state['observed_at'],'messages':state['chats'],'tasks':{}}
    text=json.dumps(payload,ensure_ascii=True,separators=(',',':'))
    pieces=[text[n:n+400000] for n in range(0,len(text),400000)]
    if not pieces or len(pieces)>100:raise SourceError('Collection exceeds safe snapshot size')
    run=hashlib.sha256(json.dumps(state,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
    def record(path,fields):return {'path':path,'update_time':None,'data':encode_value(fields)}
    records=[record('inbox_collection/source',{'kind':'provider_graphql_collection_v1','coverage':'filtered_non_atomic','observed_at':state['observed_at']}),
             record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':len(pieces),'scraped_at':state['observed_at']})]
    records.extend(record('private_dashboard_runs/'+run+'/chunks/'+str(i),{'index':i,'data':part}) for i,part in enumerate(pieces))
    encrypted_backup(records,target,_key(key))
    return {'documents':len(records),'coverage':'filtered message types; non-atomic; complete history unverified'}
