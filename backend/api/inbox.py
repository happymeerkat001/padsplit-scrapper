"""Read-only inbox over migrated records. Ingestion and outbound delivery are not implemented."""
import json,re
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from starlette.concurrency import run_in_threadpool
from backend.api.repository import Repository,Forbidden,NotFound
from padsplit_scraper.inbox_display import validate_display
from padsplit_scraper.attachment_metadata import safe_attachment_filename

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')

class InvalidCursor(Exception): pass

def conversations(repository,uid,limit,offset,q='',unread=False):
    with repository.connect() as conn:
        actor=Repository._identity(conn,uid,lock=True)
        if not actor:raise Forbidden()
        # Limit costly substring scans without interpolating user input or wildcard syntax.
        conn.execute("SET LOCAL statement_timeout = '2000ms'")
        pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        rows=conn.execute('''SELECT c.id,c.property_id,p.name AS property_name,c.updated_at,c.source_title,c.source_observed_at,c.source_unread,c.source_display,
                CASE WHEN last_message.source_deleted THEN NULL ELSE left(last_message.body,300) END AS last_message_preview,
                last_message.sent_at AS last_message_at
            FROM conversations c LEFT JOIN properties p ON p.id=c.property_id
            LEFT JOIN LATERAL (SELECT m.body,m.sent_at,m.source_deleted FROM messages m WHERE m.conversation_id=c.id
                ORDER BY m.sent_at DESC NULLS LAST,m.id DESC LIMIT 1) AS last_message ON true
            WHERE (%s='owner' OR EXISTS (SELECT 1 FROM property_access a
                WHERE a.firebase_uid=%s AND a.property_id=c.property_id))
            AND (NOT %s OR c.source_unread IS TRUE)
            AND (%s='' OR c.source_title ILIKE %s OR p.name ILIKE %s OR concat_ws(' ',c.source_display->>'first_name',c.source_display->>'last_name',c.source_display->>'room_number',c.source_display->>'street',c.source_display->>'city',c.source_display->>'state') ILIKE %s OR EXISTS
                (SELECT 1 FROM messages m WHERE m.conversation_id=c.id
                 AND m.source_deleted IS NOT TRUE AND m.body ILIKE %s))
            ORDER BY c.updated_at DESC,c.id LIMIT %s OFFSET %s''',
            (actor['role'],uid,unread,q,pattern,pattern,pattern,pattern,limit,offset)).fetchall()
        for row in rows:row['source_display']=validate_display(row['source_display'])
        return rows

def messages(repository,uid,conversation_id,limit,offset,order='asc',before=None):
    orders={'asc':'sent_at ASC NULLS LAST,id ASC','desc':'sent_at DESC NULLS LAST,id DESC'}
    if order not in orders:raise ValueError('Invalid message order')
    with repository.connect() as conn:
        actor=Repository._identity(conn,uid,lock=True)
        if not actor:raise Forbidden()
        conversation=conn.execute('SELECT property_id FROM conversations WHERE id=%s FOR SHARE',(conversation_id,)).fetchone()
        if not conversation:raise NotFound()
        if actor['role']!='owner' and conversation['property_id'] not in actor['properties']:raise Forbidden()
        condition='';params=[conversation_id]
        if before is not None:
            anchor=conn.execute('SELECT sent_at FROM messages WHERE id=%s AND conversation_id=%s',(before,conversation_id)).fetchone()
            if not anchor:raise InvalidCursor()
            stamp=anchor['sent_at']
            if stamp is None:
                condition=' AND sent_at IS NULL AND id<%s';params.append(before)
            else:
                condition=' AND (sent_at<%s OR (sent_at=%s AND id<%s) OR sent_at IS NULL)';params.extend((stamp,stamp,before))
        params.extend((limit,offset))
        rows=conn.execute('''SELECT id,CASE WHEN source_deleted THEN '' ELSE body END AS body,sent_at,sender_label,source_deleted,source_attachments FROM messages WHERE conversation_id=%s
            '''+condition+''' ORDER BY '''+orders[order]+''' LIMIT %s OFFSET %s''',params).fetchall()
        result=[]
        for row in rows:
            raw=row.pop('source_attachments')
            if row['source_deleted'] or raw is None: attachments=None
            else:
                if not isinstance(raw,list) or len(raw)>100:raise ValueError('Invalid attachment metadata')
                attachments=[]
                for item in raw:
                    if not isinstance(item,dict) or type(item.get('deleted')) is not bool:raise ValueError('Invalid attachment metadata')
                    clean={'deleted':item['deleted']}
                    if isinstance(item.get('id'),str) and re.fullmatch(r'attachment_[a-f0-9]{64}',item['id']):clean['id']=item['id']
                    name=item.get('filename')
                    if safe_attachment_filename(name):clean['filename']=name
                    mime=item.get('mediaType')
                    if isinstance(mime,str) and len(mime)<=200 and re.fullmatch(r'[A-Za-z0-9.+_-]+(?:/[A-Za-z0-9.+_-]+)?',mime):clean['mediaType']=mime
                    attachments.append(clean)
            result.append({**row,'attachments':attachments})
        return result

def make_inbox_router(*,actor_dependency,repository_provider,run):
    router=APIRouter()
    @router.get('/v1/inbox/conversations')
    def list_conversations(limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
        return {'items':run(lambda:conversations(repository_provider(),identity['uid'],limit,offset)),'limit':limit,'offset':offset,'ingestion':'not_configured'}
    @router.post('/v1/inbox/search')
    async def search_conversations(request:Request,identity=Depends(actor_dependency)):
        # Search text can contain private names or messages, so never put it in URLs.
        raw=b''
        async for chunk in request.stream():
            raw+=chunk
            if len(raw)>2048:raise HTTPException(422,'Invalid request')
        try: body=json.loads(raw)
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid request')
        if (not isinstance(body,dict) or set(body)!={'q','unread','limit','offset'} or
            not isinstance(body['q'],str) or len(body['q'])>100 or type(body['unread']) is not bool or
            type(body['limit']) is not int or not 1<=body['limit']<=100 or
            type(body['offset']) is not int or not 0<=body['offset']<=10000):raise HTTPException(422,'Invalid request')
        limit,offset=body['limit'],body['offset']
        items=await run_in_threadpool(run,lambda:conversations(repository_provider(),identity['uid'],limit,offset,body['q'].strip(),body['unread']))
        return {'items':items,'limit':limit,'offset':offset,'ingestion':'not_configured'}
    @router.get('/v1/inbox/conversations/{conversation_id}/messages')
    def list_messages(conversation_id:str,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),order:Literal['asc','desc']='asc',before:str|None=Query(default=None,pattern=_ID.pattern),identity=Depends(actor_dependency)):
        if not _ID.fullmatch(conversation_id):raise HTTPException(422,'Invalid request')
        if before is not None and (order!='desc' or offset!=0 or before in ('.','..')):raise HTTPException(422,'Invalid page cursor')
        def read():
            try:return messages(repository_provider(),identity['uid'],conversation_id,limit,offset,order,before)
            except InvalidCursor:return None
        items=run(read)
        if items is None:raise HTTPException(422,'Invalid page cursor')
        return {'items':items,'limit':limit,'offset':offset,'next_before':items[-1]['id'] if order=='desc' and len(items)==limit else None}
    return router
