"""Transactional PostgreSQL access for the initial API surface."""
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Forbidden(Exception): pass
class NotFound(Exception): pass
class Conflict(Exception): pass


# Minimum schema contract used by the current API routes. These names contain
# no tenant data and can be checked in PostgreSQL's public system catalogs
# without granting the API access to the migration ledger or widening table
# privileges. This is not a migration checksum or data-completeness check.
_READY_COLUMNS = {
    'schema_migrations': ('version','checksum'),
    'app_users': ('firebase_uid','active','auth_lock_guard'),
    'user_roles': ('firebase_uid','role','team_access_version','auth_lock_guard'),
    'property_access': ('firebase_uid','property_id','auth_lock_guard'),
    'properties': ('id','name'),
    'rooms': ('id','property_id','label'),
    'occupancy_observations': ('id','room_id','observed_at','source_payload','source_generation'),
    'tasks': ('id','property_id','title','status','priority','assignee_uid','version','created_at','updated_at','source_path'),
    'task_discussions': ('id','task_id','author_uid','body','created_at','source_role','source_status','source_created_nanos','source_present_fields','deleted_at'),
    'conversations': ('id','property_id','updated_at','source_title','source_observed_at','source_unread','source_display','auth_lock_guard'),
    'messages': ('id','conversation_id','body','sent_at','sender_label','source_deleted','source_attachments'),
    'vendors': ('id','name','data','version'),
    'templates': ('id','name','body','version'),
    'notes': ('id','text','version'),
    'access_codes': ('property_id','ciphertext','nonce','key_version','version','updated_at'),
    'access_code_schemas': ('property_id','ciphertext','nonce','key_version','version'),
    'code_history': ('id','property_id','ciphertext','nonce','key_version','source_version','created_at'),
    'statistics': ('id','data','observed_at'),
    'report_access': ('firebase_uid','auth_lock_guard'),
    'padsplit_task_observations': ('id','task_key','source_id','property_id','bucket','data','observed_at','source_generation'),
    'reviewed_room_task_links': ('task_observation_id','occupancy_observation_id','property_id','source_generation'),
    'reviewed_provider_discussion_links': ('task_observation_id','internal_task_id','property_id','source_generation','expected_task_source_path'),
    'climate_observations': ('id','device_key','property_id','observed_at','source_fingerprint','data'),
    'idempotency_keys': ('actor_uid','key','request_hash','response_json'),
    'audit_events': ('actor_uid','action','target_type','target_id','redacted_metadata'),
}


class Repository:
    def __init__(self, dsn):
        if not dsn: raise ValueError('DATABASE_URL is required')
        self._dsn = dsn

    def connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5)

    def ready(self):
        try:
            with self.connect() as conn:
                tables,columns=zip(*((table,column) for table,fields in _READY_COLUMNS.items() for column in fields))
                row=conn.execute('''SELECT count(*) AS missing
                    FROM unnest(%s::text[], %s::text[]) AS required(relation_name,column_name)
                    WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                        WHERE a.attrelid=to_regclass(quote_ident(required.relation_name))
                          AND a.attname=required.column_name AND a.attnum>0 AND NOT a.attisdropped)''',
                    (list(tables),list(columns))).fetchone()
                return row['missing']==0
        except Exception:
            return False

    @staticmethod
    def _identity(conn, uid, *, lock=False):
        suffix = ' FOR SHARE OF u, r' if lock else ''
        row = conn.execute('SELECT u.firebase_uid AS uid, r.role FROM app_users u JOIN user_roles r ON r.firebase_uid=u.firebase_uid WHERE u.firebase_uid=%s AND u.active = true' + suffix, (uid,)).fetchone()
        if not row: return None
        props=conn.execute('SELECT property_id FROM property_access WHERE firebase_uid=%s' + (' FOR SHARE' if lock else ''), (uid,)).fetchall()
        return {'uid':row['uid'],'role':row['role'],'properties':[p['property_id'] for p in props]}

    def identity(self, uid):
        with self.connect() as conn: return self._identity(conn, uid)

    def properties(self, uid):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor: raise Forbidden()
            if actor['role']=='owner':
                return conn.execute('SELECT id, name FROM properties ORDER BY name, id').fetchall()
            return conn.execute('SELECT p.id,p.name FROM properties p JOIN property_access pa ON pa.property_id=p.id WHERE pa.firebase_uid=%s ORDER BY p.name,p.id',(uid,)).fetchall()

    def shared_records(self, uid, kind, limit, offset):
        # Existing records have no property boundary; only the owner may see them.
        statements = {'vendors':'SELECT id,name,data,version FROM vendors ORDER BY id LIMIT %s OFFSET %s',
                      'templates':'SELECT id,name,body,version FROM templates ORDER BY id LIMIT %s OFFSET %s'}
        if kind not in statements: raise ValueError('Invalid record type')
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role']!='owner': raise Forbidden()
            return conn.execute(statements[kind],(limit,offset)).fetchall()

    def tasks_list(self, uid, property_id, limit, offset):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor: raise Forbidden()
            if property_id and actor['role']!='owner' and property_id not in actor['properties']: raise Forbidden()
            return conn.execute('''SELECT t.id,t.property_id,t.title,t.status,t.priority,t.assignee_uid,t.version,t.created_at,t.updated_at
                FROM tasks t WHERE (%s = 'owner' OR EXISTS
                (SELECT 1 FROM property_access p WHERE p.firebase_uid = %s AND p.property_id = t.property_id))
                AND (%s::text IS NULL OR t.property_id = %s)
                ORDER BY t.updated_at DESC NULLS LAST,t.id LIMIT %s OFFSET %s''',
                (actor['role'],uid,property_id,property_id,limit,offset)).fetchall()

    def task_update(self, uid, task_id, title, status, expected_version):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role'] not in ('owner','manager','operator'): raise Forbidden()
            task=conn.execute('SELECT property_id, version FROM tasks WHERE id=%s FOR UPDATE',(task_id,)).fetchone()
            if not task: raise NotFound()
            if actor['role']!='owner' and task['property_id'] not in actor['properties']: raise Forbidden()
            if task['version']!=expected_version: raise Conflict()
            updated=conn.execute('''UPDATE tasks SET title=COALESCE(%s,title),status=%s,version=version+1,updated_at=now()
                WHERE id=%s AND version=%s RETURNING id,property_id,title,status,priority,assignee_uid,version,created_at,updated_at''',
                (title,status,task_id,expected_version)).fetchone()
            if not updated: raise Conflict()
            conn.execute('''INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata)
                VALUES (%s,'task.update','task',%s,%s)''', (uid,task_id,Jsonb({'fields':(['title','status'] if title is not None else ['status'])})))
            return updated

    def task_get(self, uid, task_id):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor:raise Forbidden()
            self._task_for_actor(conn,actor,task_id,lock=True)
            return conn.execute('SELECT id,property_id,title,status,priority,assignee_uid,version,created_at,updated_at FROM tasks WHERE id=%s',(task_id,)).fetchone()

    @staticmethod
    def _task_for_actor(conn, actor, task_id, *, lock=False):
        task=conn.execute('SELECT property_id,version FROM tasks WHERE id=%s' + (' FOR UPDATE' if lock else ''),(task_id,)).fetchone()
        if not task: raise NotFound()
        if actor['role']!='owner' and task['property_id'] not in actor['properties']: raise Forbidden()
        return task

    @staticmethod
    def _reserve(conn, uid, key, operation, payload):
        import hashlib, json
        fingerprint=hashlib.sha256(json.dumps([operation,payload],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        inserted=conn.execute('''INSERT INTO idempotency_keys(actor_uid,key,request_hash,response_json)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING key''',(uid,key,fingerprint,Jsonb({}))).fetchone()
        if inserted:return None
        previous=conn.execute('SELECT request_hash,response_json FROM idempotency_keys WHERE actor_uid=%s AND key=%s FOR UPDATE',(uid,key)).fetchone()
        if not previous or previous['request_hash']!=fingerprint: raise Conflict()
        return previous['response_json']

    @staticmethod
    def _finish(conn, uid, key, response):
        conn.execute('UPDATE idempotency_keys SET response_json=%s WHERE actor_uid=%s AND key=%s',(Jsonb(response),uid,key))

    def task_create(self, uid, data, key):
        import uuid
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role'] not in ('owner','manager','operator'): raise Forbidden()
            prop=data['property_id']
            if actor['role']!='owner' and prop not in actor['properties']: raise Forbidden()
            prior=self._reserve(conn,uid,key,'task.create',data)
            if prior is not None:return prior
            if not conn.execute('SELECT 1 FROM properties WHERE id=%s',(prop,)).fetchone():raise NotFound()
            task_id=uuid.uuid4().hex
            conn.execute('INSERT INTO tasks(id,property_id,title,status,priority) VALUES (%s,%s,%s,%s,%s)',(task_id,prop,data['title'],data['status'],data['priority']))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,'task.create','task',%s,%s)",(uid,task_id,Jsonb({})))
            response={'id':task_id,'version':1}; self._finish(conn,uid,key,response);return response

    def task_delete(self, uid, task_id, expected_version):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role'] not in ('owner','manager'):raise Forbidden()
            task=self._task_for_actor(conn,actor,task_id,lock=True)
            if task['version']!=expected_version:raise Conflict()
            # Existing discussion must not be silently deleted with the task.
            if conn.execute('SELECT 1 FROM task_discussions WHERE task_id=%s LIMIT 1',(task_id,)).fetchone():raise Conflict()
            conn.execute('DELETE FROM tasks WHERE id=%s',(task_id,))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,'task.delete','task',%s,%s)",(uid,task_id,Jsonb({})))
            return {'ok':True}

    def discussion_list(self, uid, task_id, limit, offset):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor:raise Forbidden()
            self._task_for_actor(conn,actor,task_id)
            return conn.execute('''SELECT id,author_uid,body,created_at,source_role,source_status,source_created_nanos,source_present_fields
                FROM task_discussions WHERE task_id=%s AND deleted_at IS NULL ORDER BY created_at,id LIMIT %s OFFSET %s''',(task_id,limit,offset)).fetchall()

    def discussion_create(self, uid, task_id, body, key, role_label=None, status_label=None):
        import uuid
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role'] not in ('owner','manager','operator'):raise Forbidden()
            self._task_for_actor(conn,actor,task_id,lock=True)
            payload={'task_id':task_id,'body':body}
            if role_label is not None:payload['role_label']=role_label
            if status_label is not None:payload['status_label']=status_label
            prior=self._reserve(conn,uid,key,'discussion.create',payload)
            if prior is not None:return prior
            discussion_id=uuid.uuid4().hex
            conn.execute('INSERT INTO task_discussions(id,task_id,author_uid,body,source_role,source_status) VALUES (%s,%s,%s,%s,%s,%s)',(discussion_id,task_id,uid,body,role_label,status_label))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,'discussion.create','task',%s,%s)",(uid,task_id,Jsonb({})))
            response={'id':discussion_id};self._finish(conn,uid,key,response);return response

    def discussion_delete(self, uid, task_id, discussion_id):
        with self.connect() as conn:
            actor=self._identity(conn,uid,lock=True)
            if not actor or actor['role'] not in ('owner','manager','operator'):raise Forbidden()
            self._task_for_actor(conn,actor,task_id,lock=True)
            deleted=conn.execute('UPDATE task_discussions SET deleted_at=now() WHERE id=%s AND task_id=%s AND deleted_at IS NULL RETURNING id',(discussion_id,task_id)).fetchone()
            if not deleted:raise NotFound()
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,'discussion.delete','task',%s,%s)",(uid,task_id,Jsonb({})))
            return {'ok':True}

    @staticmethod
    def _codes_key():
        import os, re
        from backend.storage.secrets import load_key
        version=os.getenv('PADSPLIT_CODES_KEY_VERSION','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',version):raise ValueError('Codes key version required')
        return load_key(),version

    @staticmethod
    def _owner(conn, uid):
        actor=Repository._identity(conn,uid,lock=True)
        if not actor or actor['role']!='owner':raise Forbidden()

    @staticmethod
    def _decode_codes(record, property_id, key, key_version):
        from backend.storage.secrets import decrypt_codes
        if record['key_version']!=key_version:raise ValueError('Required codes key version unavailable')
        return decrypt_codes(bytes(record['ciphertext']),bytes(record['nonce']),property_id=property_id,
                             version=record['version'],key_version=key_version,key=key)

    @staticmethod
    def _codes_audit(conn, uid, action, prop):
        conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,%s,'property',%s,%s)",(uid,action,prop,Jsonb({})))

    @staticmethod
    def _snapshot_codes(conn, prop, record):
        import uuid
        conn.execute('''INSERT INTO code_history(id,property_id,ciphertext,nonce,key_version,source_version,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,now())''',
                     (uuid.uuid4().hex,prop,record['ciphertext'],record['nonce'],record['key_version'],record['version']))

    def codes_metadata(self, uid, prop):
        with self.connect() as conn:
            self._owner(conn,uid)
            key,key_version=self._codes_key()
            row=conn.execute('SELECT ciphertext,nonce,key_version,version FROM access_codes WHERE property_id=%s',(prop,)).fetchone()
            if not row:return {'exists':False,'version':0,'fields':{}}
            values=self._decode_codes(row,prop,key,key_version)
            return {'exists':True,'version':row['version'],'fields':{key:'••••' for key in values}}

    def codes_reveal(self, uid, prop):
        with self.connect() as conn:
            self._owner(conn,uid)
            row=conn.execute('SELECT ciphertext,nonce,key_version,version FROM access_codes WHERE property_id=%s',(prop,)).fetchone()
            if not row:raise NotFound()
            key,key_version=self._codes_key();values=self._decode_codes(row,prop,key,key_version)
            self._codes_audit(conn,uid,'codes.reveal',prop)
            return {'version':row['version'],'fields':values}

    def codes_save(self, uid, prop, fields, expected_version):
        from backend.storage.secrets import encrypt_codes
        with self.connect() as conn:
            self._owner(conn,uid)
            if not conn.execute('SELECT 1 FROM properties WHERE id=%s',(prop,)).fetchone():raise NotFound()
            row=conn.execute('SELECT ciphertext,nonce,key_version,version FROM access_codes WHERE property_id=%s FOR UPDATE',(prop,)).fetchone()
            if (row['version'] if row else 0)!=expected_version:raise Conflict()
            key,key_version=self._codes_key()
            new_version=expected_version+1
            nonce,cipher=encrypt_codes(fields,property_id=prop,version=new_version,key_version=key_version,key=key)
            if row:
                # Ensure the configured key can still decrypt the old version before recording it.
                self._decode_codes(row,prop,key,key_version)
                self._snapshot_codes(conn,prop,row)
                conn.execute('UPDATE access_codes SET ciphertext=%s,nonce=%s,key_version=%s,version=%s,updated_at=now() WHERE property_id=%s',(cipher,nonce,key_version,new_version,prop))
            else:
                created=conn.execute('''INSERT INTO access_codes(property_id,ciphertext,nonce,key_version,version)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING property_id''',(prop,cipher,nonce,key_version,new_version)).fetchone()
                if not created:raise Conflict()
            self._codes_audit(conn,uid,'codes.save',prop)
            return {'version':new_version}

    def codes_history(self, uid, prop, limit, offset):
        with self.connect() as conn:
            self._owner(conn,uid)
            self._codes_key()
            return conn.execute('SELECT id,source_version,created_at FROM code_history WHERE property_id=%s ORDER BY created_at DESC,id LIMIT %s OFFSET %s',(prop,limit,offset)).fetchall()

    def codes_history_reveal(self, uid, prop, history_id):
        with self.connect() as conn:
            self._owner(conn,uid)
            row=conn.execute('SELECT ciphertext,nonce,key_version,source_version AS version FROM code_history WHERE property_id=%s AND id=%s',(prop,history_id)).fetchone()
            if not row:raise NotFound()
            if row['version'] is None:raise Conflict()
            key,key_version=self._codes_key()
            values=self._decode_codes(row,prop,key,key_version)
            self._codes_audit(conn,uid,'codes.history_reveal',prop)
            return {'fields':values}

    def codes_restore(self, uid, prop, history_id, expected_version):
        from backend.storage.secrets import encrypt_codes
        with self.connect() as conn:
            self._owner(conn,uid)
            current=conn.execute('SELECT ciphertext,nonce,key_version,version FROM access_codes WHERE property_id=%s FOR UPDATE',(prop,)).fetchone()
            if not current:raise NotFound()
            if current['version']!=expected_version:raise Conflict()
            prior=conn.execute('SELECT ciphertext,nonce,key_version,source_version AS version FROM code_history WHERE property_id=%s AND id=%s',(prop,history_id)).fetchone()
            if not prior:raise NotFound()
            if prior['version'] is None:raise Conflict()
            key,key_version=self._codes_key()
            self._decode_codes(current,prop,key,key_version)
            restored=self._decode_codes(prior,prop,key,key_version)
            next_version=current['version']+1
            nonce,cipher=encrypt_codes(restored,property_id=prop,version=next_version,key_version=key_version,key=key)
            self._snapshot_codes(conn,prop,current)
            conn.execute('UPDATE access_codes SET ciphertext=%s,nonce=%s,key_version=%s,version=%s,updated_at=now() WHERE property_id=%s',(cipher,nonce,key_version,next_version,prop))
            self._codes_audit(conn,uid,'codes.restore',prop)
            return {'version':next_version}

    @staticmethod
    def _shared_kind(kind):
        if kind not in ('vendors','templates'):raise ValueError('Invalid record type')
        return kind

    def shared_create(self, uid, kind, data, key):
        import uuid
        kind=self._shared_kind(kind)
        with self.connect() as conn:
            self._owner(conn,uid)
            previous=self._reserve(conn,uid,key,kind+'.create',data)
            if previous is not None:return previous
            ident=uuid.uuid4().hex
            if kind=='vendors':
                conn.execute('INSERT INTO vendors(id,name,data) VALUES (%s,%s,%s)',(ident,data['name'],Jsonb({k:v for k,v in data.items() if k!='name'})))
            else:
                conn.execute('INSERT INTO templates(id,name,body) VALUES (%s,%s,%s)',(ident,data['name'],data['body']))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,%s,%s,%s,%s)",(uid,kind+'.create',kind,ident,Jsonb({})))
            result={'id':ident,'version':1};self._finish(conn,uid,key,result);return result

    def shared_update(self, uid, kind, ident, data, expected_version):
        kind=self._shared_kind(kind)
        with self.connect() as conn:
            self._owner(conn,uid)
            if kind=='vendors':
                row=conn.execute('SELECT version,name,data FROM vendors WHERE id=%s FOR UPDATE',(ident,)).fetchone()
            else:
                row=conn.execute('SELECT version FROM templates WHERE id=%s FOR UPDATE',(ident,)).fetchone()
            if not row:raise NotFound()
            if row['version']!=expected_version:raise Conflict()
            if kind=='vendors':
                if not isinstance(row['data'],dict):raise ValueError('Invalid stored vendor data')
                merged=dict(row['data']);merged.update({k:v for k,v in data.items() if k!='name'})
                conn.execute('UPDATE vendors SET name=%s,data=%s,version=version+1 WHERE id=%s',(data.get('name',row['name']),Jsonb(merged),ident))
            else:
                conn.execute('UPDATE templates SET name=%s,body=%s,version=version+1 WHERE id=%s',(data['name'],data['body'],ident))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,%s,%s,%s,%s)",(uid,kind+'.update',kind,ident,Jsonb({})))
            return {'id':ident,'version':expected_version+1}

    def shared_delete(self, uid, kind, ident, expected_version):
        from psycopg import sql
        kind=self._shared_kind(kind)
        with self.connect() as conn:
            self._owner(conn,uid)
            row=conn.execute(sql.SQL('SELECT version FROM {} WHERE id=%s FOR UPDATE').format(sql.Identifier(kind)),(ident,)).fetchone()
            if not row:raise NotFound()
            if row['version']!=expected_version:raise Conflict()
            conn.execute(sql.SQL('DELETE FROM {} WHERE id=%s').format(sql.Identifier(kind)),(ident,))
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,%s,%s,%s,%s)",(uid,kind+'.delete',kind,ident,Jsonb({})))
            return {'ok':True}

    def note_get(self, uid, ident):
        with self.connect() as conn:
            self._owner(conn,uid)
            note=conn.execute('SELECT id,text,version FROM notes WHERE id=%s',(ident,)).fetchone()
            return {**note,'exists':True} if note else {'id':ident,'text':'','version':0,'exists':False}

    @staticmethod
    def _can_report(conn,uid):
        actor=Repository._identity(conn,uid,lock=True)
        if not actor:return False
        if actor['role']=='owner':return True
        return bool(conn.execute('SELECT firebase_uid FROM report_access WHERE firebase_uid=%s FOR SHARE',(uid,)).fetchone())

    def report_access(self,uid):
        with self.connect() as conn:return self._can_report(conn,uid)

    def report(self, uid, ident):
        if ident not in ('latest','monthly_history'): raise NotFound()
        with self.connect() as conn:
            if not self._can_report(conn,uid):raise Forbidden()
            # Return JSON text so the API does not round financial numbers via
            # Python binary floats. The original source remains encrypted.
            row=conn.execute('SELECT id,data::text AS json,observed_at FROM statistics WHERE id=%s',(ident,)).fetchone()
            if not row:raise NotFound()
            return row

    def note_put(self, uid, ident, text, expected_version):
        with self.connect() as conn:
            self._owner(conn,uid)
            existing=conn.execute('SELECT version FROM notes WHERE id=%s FOR UPDATE',(ident,)).fetchone()
            if (existing['version'] if existing else 0)!=expected_version:raise Conflict()
            if existing:
                conn.execute('UPDATE notes SET text=%s,version=version+1 WHERE id=%s',(text,ident))
            else:
                created=conn.execute('INSERT INTO notes(id,text,version) VALUES (%s,%s,1) ON CONFLICT DO NOTHING RETURNING id',(ident,text)).fetchone()
                if not created:raise Conflict()
            conn.execute("INSERT INTO audit_events(actor_uid,action,target_type,target_id,redacted_metadata) VALUES (%s,'notes.save','notes',%s,%s)",(uid,ident,Jsonb({})))
            return {'id':ident,'version':expected_version+1}

    def codes_schema(self, uid):
        from backend.storage.secrets import decrypt_code_schema
        from padsplit_scraper.private_codes_schema import validate_schema
        with self.connect() as conn:
            # Check both tables against the same snapshot, so partial migration never
            # exposes a layout with no corresponding encrypted values (or vice versa).
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            self._owner(conn,uid)
            key,key_version=self._codes_key()
            schemas=conn.execute('SELECT property_id,ciphertext,nonce,key_version,version FROM access_code_schemas ORDER BY property_id LIMIT 101').fetchall()
            codes=conn.execute('SELECT property_id,version FROM access_codes ORDER BY property_id LIMIT 101').fetchall()
            if len(schemas)>100 or len(codes)>100 or [r['property_id'] for r in schemas]!=[r['property_id'] for r in codes]:
                raise Conflict()
            properties=[]
            for row in schemas:
                if row['key_version']!=key_version: raise ValueError('Required codes key version unavailable')
                prop=decrypt_code_schema(bytes(row['ciphertext']),bytes(row['nonce']),property_id=row['property_id'],version=row['version'],key_version=key_version,key=key)
                if prop.get('slug')!=row['property_id']: raise ValueError('Schema identity mismatch')
                properties.append(prop)
            if properties:validate_schema({'properties':properties})
            return {'properties':properties,'versions':{row['property_id']:row['version'] for row in codes}}
