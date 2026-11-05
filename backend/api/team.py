"""Trusted team operations; caller identity must come from verified Firebase token.

This service is not an HTTP endpoint. Its caller must reject any client-provided
actor and pass only the verified token UID. The Firebase user lookup is injected
by the trusted server; missing, failed, or mismatched lookup fails closed.
"""
from __future__ import annotations
import re
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from backend.api.repository import Conflict, Forbidden, NotFound

_LOCK = 731905609  # Shared with migration 009 deferred owner constraints.
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_ROLES = frozenset({'owner', 'manager', 'operator', 'viewer'})


class TeamAccessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['owner','manager','operator','viewer']
    active: StrictBool
    properties: list[StrictStr] = Field(max_length=50)
    expected_version: int = Field(ge=0, strict=True)


class TargetVerificationFailed(Exception):
    """Firebase target lookup failed without exposing provider details."""


class TeamConnectionNotIsolated(Exception):
    """The dedicated team database role is missing or not isolated."""


class LastOwner(Conflict):
    """The operation would remove the last active owner."""


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid identifier')
    return value


def _scope(role, active, properties):
    if not isinstance(role, str) or role not in _ROLES or type(active) is not bool:
        raise ValueError('Invalid access')
    if not isinstance(properties, list) or len(properties) > 50:
        raise ValueError('Invalid access')
    ids = [_identifier(x) for x in properties]
    if len(ids) != len(set(ids)) or (active and role != 'owner' and not ids) or (not active and ids):
        raise ValueError('Invalid access')
    return ids


def _restricted_role_context(conn):
    # Column-level UPDATE on auth_lock_guard is needed for SELECT FOR SHARE;
    # privileges that permit identity, role or scope mutations are forbidden.
    return conn.execute('''SELECT current_database() AS db, current_user AS role, session_user AS session_role,
        inet_server_addr() AS server_ip, inet_server_port() AS server_port, pg_postmaster_start_time() AS server_started,
        to_regclass('app_users')::oid AS users_oid, to_regclass('user_roles')::oid AS roles_oid,
        to_regclass('property_access')::oid AS access_oid, to_regclass('properties')::oid AS properties_oid,
        to_regclass('team_admin_audit')::oid AS audit_oid,
        EXISTS (SELECT 1 FROM pg_roles WHERE rolname=session_user AND
          (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)) AS session_elevated,
        EXISTS (SELECT 1 FROM unnest(ARRAY[current_user::text,session_user::text]) AS principal(name)
          CROSS JOIN unnest(ARRAY['app_users','user_roles','property_access']) AS t(name)
          WHERE has_schema_privilege(principal.name, current_schema(), 'CREATE')
             OR has_table_privilege(principal.name, t.name, 'DELETE')
             OR has_table_privilege(principal.name, t.name, 'TRUNCATE')
             OR has_table_privilege(principal.name, t.name, 'TRIGGER')
             OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=to_regclass(t.name)
                    AND a.attnum > 0 AND NOT a.attisdropped AND
                    (has_column_privilege(principal.name, to_regclass(t.name), a.attname, 'INSERT')
                    OR (a.attname <> 'auth_lock_guard' AND
                        has_column_privilege(principal.name, to_regclass(t.name), a.attname, 'UPDATE'))))) AS unsafe''').fetchone()


def _team_role_is_unsafe(conn):
    # Scope revocation needs DELETE on property_access only. Team principals
    # must not own these tables, create objects in their schemas, or manage
    # triggers; otherwise their grants could bypass the last-owner and audit
    # constraints. Check both current_user and the login principal.
    return conn.execute('''SELECT EXISTS (
      SELECT 1 FROM unnest(ARRAY[current_user::text,session_user::text]) AS principal(name)
      CROSS JOIN unnest(ARRAY['app_users','user_roles','property_access','team_admin_audit']) AS t(name)
      JOIN pg_class c ON c.oid=to_regclass(t.name)
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE pg_has_role(principal.name,pg_get_userbyid(c.relowner),'MEMBER')
         OR has_schema_privilege(principal.name,n.nspname,'CREATE')
         OR has_table_privilege(principal.name,t.name,'TRUNCATE')
         OR has_table_privilege(principal.name,t.name,'TRIGGER')
         OR (t.name <> 'property_access' AND has_table_privilege(principal.name,t.name,'DELETE'))
         OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum > 0 AND NOT a.attisdropped AND
              ((has_column_privilege(principal.name,c.oid,a.attname,'UPDATE') AND NOT (
                    (t.name='app_users' AND a.attname='active') OR
                    (t.name='user_roles' AND a.attname IN ('role','team_access_version'))))
               OR (has_column_privilege(principal.name,c.oid,a.attname,'INSERT') AND NOT (
                    (t.name='app_users' AND a.attname IN ('firebase_uid','active')) OR
                    (t.name='user_roles' AND a.attname IN ('firebase_uid','role')) OR
                    (t.name='property_access' AND a.attname IN ('firebase_uid','property_id')) OR
                    (t.name='team_admin_audit' AND a.attname IN ('actor_uid','target_uid','action','role','active','property_count'))))
               OR (t.name='app_users' AND a.attname='email' AND has_column_privilege(principal.name,c.oid,a.attname,'SELECT'))))
    ) AS unsafe''').fetchone()['unsafe']


class TeamAdministration:
    def __init__(self, auth_repository, team_repository, verify_target: Callable[[str], dict] | None):
        self.auth_repository = auth_repository
        self.team_repository = team_repository
        self.verify_target = verify_target

    def set_access(self, verified_actor_uid, *, target_uid, role, active, properties, expected_version):
        """Atomically replace role/scope and record a redacted immutable audit event.

        ``verified_actor_uid`` is server-derived, never a request field. Target
        Firebase identity is looked up before writing; the owner is rechecked
        inside the serialized transaction. Bootstrap of the first owner is a
        separate trusted operator procedure, never an endpoint action.
        """
        actor = _identifier(verified_actor_uid)
        target = _identifier(target_uid)
        ids = _scope(role, active, properties)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Invalid version')
        # Avoid letting unauthorized callers enumerate Firebase accounts. The
        # transaction repeats this check after target lookup to prevent races.
        identity = self.auth_repository.identity(actor)
        if not identity or identity.get('role') != 'owner': raise Forbidden()
        if not callable(self.verify_target): raise TargetVerificationFailed()
        if self.team_repository is None or self.auth_repository is self.team_repository:
            raise TeamConnectionNotIsolated()
        with self.auth_repository.connect() as auth_conn:
            auth_db = _restricted_role_context(auth_conn)
            auth_host = (auth_conn.info.host, auth_conn.info.port)
        with self.team_repository.connect() as conn:
            team_db = conn.execute('''SELECT current_database() AS db, current_user AS role, session_user AS session_role,
                inet_server_addr() AS server_ip, inet_server_port() AS server_port, pg_postmaster_start_time() AS server_started,
                to_regclass('app_users')::oid AS users_oid, to_regclass('user_roles')::oid AS roles_oid,
                to_regclass('property_access')::oid AS access_oid, to_regclass('properties')::oid AS properties_oid,
                to_regclass('team_admin_audit')::oid AS audit_oid,
                (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls) AS elevated,
                EXISTS (SELECT 1 FROM pg_roles s WHERE s.rolname=session_user AND
                  (s.rolsuper OR s.rolcreaterole OR s.rolcreatedb OR s.rolreplication OR s.rolbypassrls)) AS session_elevated
                FROM pg_roles WHERE rolname=current_user''').fetchone()
            if (not auth_db or not team_db or auth_db['db'] != team_db['db'] or
                    not auth_host[0] or auth_host != (conn.info.host, conn.info.port) or
                    not auth_db['server_started'] or any(auth_db[k] != team_db[k] for k in ('server_ip','server_port','server_started')) or
                    any(not auth_db[k] or auth_db[k] != team_db[k] for k in ('users_oid','roles_oid','access_oid','properties_oid','audit_oid')) or
                    auth_db['role'] == team_db['role'] or auth_db['session_role'] == team_db['session_role'] or
                    auth_db['unsafe'] or auth_db['session_elevated'] or team_db['elevated'] or team_db['session_elevated']):
                raise TeamConnectionNotIsolated()
            reachable = conn.execute('''SELECT pg_has_role(%s,current_user,'MEMBER') OR
                pg_has_role(%s,current_user,'MEMBER') OR pg_has_role(%s,session_user,'MEMBER') AS allowed''',
                (auth_db['role'],auth_db['session_role'],auth_db['session_role'])).fetchone()
            if not reachable or reachable['allowed']:
                raise TeamConnectionNotIsolated()
            if _team_role_is_unsafe(conn): raise TeamConnectionNotIsolated()
            try:
                found = self.verify_target(target)
                if (not isinstance(found, dict) or found.get('uid') != target or
                        type(found.get('disabled')) is not bool or (active and found['disabled'])):
                    raise ValueError('Target identity unavailable')
            except Exception:
                raise TargetVerificationFailed() from None
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (_LOCK,))
            actor_row = conn.execute('''SELECT r.role FROM app_users u JOIN user_roles r USING(firebase_uid)
                WHERE u.firebase_uid=%s AND u.active IS TRUE FOR UPDATE OF u,r''', (actor,)).fetchone()
            if not actor_row or actor_row['role'] != 'owner': raise Forbidden()
            user = conn.execute('SELECT firebase_uid,active FROM app_users WHERE firebase_uid=%s FOR UPDATE', (target,)).fetchone()
            old_role = conn.execute('SELECT role,team_access_version FROM user_roles WHERE firebase_uid=%s FOR UPDATE', (target,)).fetchone()
            previous_version = old_role['team_access_version'] if old_role else 0
            if expected_version != previous_version: raise Conflict()
            if user and user['active'] and old_role and old_role['role'] == 'owner' and (not active or role != 'owner'):
                count = conn.execute('''SELECT count(*) AS n FROM app_users u JOIN user_roles r USING(firebase_uid)
                    WHERE u.active IS TRUE AND r.role='owner' ''').fetchone()['n']
                if count <= 1: raise LastOwner()
            if ids:
                found_ids = {r['id'] for r in conn.execute('SELECT id FROM properties WHERE id = ANY(%s)', (ids,)).fetchall()}
                if found_ids != set(ids): raise NotFound()
            if not user:
                conn.execute('INSERT INTO app_users(firebase_uid,active) VALUES (%s,%s)', (target,active))
            else:
                conn.execute('UPDATE app_users SET active=%s WHERE firebase_uid=%s', (active,target))
            if old_role:
                conn.execute('UPDATE user_roles SET role=%s,team_access_version=%s WHERE firebase_uid=%s', (role,previous_version+1,target))
            else:
                conn.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)', (target,role))
            conn.execute('DELETE FROM property_access WHERE firebase_uid=%s', (target,))
            for prop in ids:
                conn.execute('INSERT INTO property_access(firebase_uid,property_id) VALUES (%s,%s)', (target,prop))
            conn.execute('''INSERT INTO team_admin_audit(actor_uid,target_uid,action,role,active,property_count)
                VALUES (%s,%s,'member.set_access',%s,%s,%s)''', (actor,target,role,active,len(ids)))
        return {'ok': True, 'version':previous_version+1}

    def get_member(self, verified_actor_uid, target_uid):
        actor, target = _identifier(verified_actor_uid), _identifier(target_uid)
        with self.auth_repository.connect() as conn:
            identity = self.auth_repository._identity(conn,actor,lock=True)
            if not identity or identity.get('role') != 'owner': raise Forbidden()
            row = conn.execute('''SELECT u.firebase_uid AS uid,u.active,r.role,r.team_access_version AS version,
                ARRAY(SELECT p.property_id FROM property_access p WHERE p.firebase_uid=u.firebase_uid ORDER BY p.property_id) AS properties
                FROM app_users u JOIN user_roles r USING(firebase_uid) WHERE u.firebase_uid=%s''',(target,)).fetchone()
            if not row: raise NotFound()
            return dict(row)

    def list_members(self, verified_actor_uid, *, limit, offset):
        actor = _identifier(verified_actor_uid)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid pagination')
        with self.auth_repository.connect() as conn:
            identity = self.auth_repository._identity(conn,actor,lock=True)
            if not identity or identity.get('role') != 'owner': raise Forbidden()
            rows = conn.execute('''SELECT u.firebase_uid AS uid,u.active,r.role,r.team_access_version AS version,
                ARRAY(SELECT p.property_id FROM property_access p WHERE p.firebase_uid=u.firebase_uid ORDER BY p.property_id) AS properties
                FROM app_users u JOIN user_roles r USING(firebase_uid) ORDER BY u.firebase_uid LIMIT %s OFFSET %s''',(limit,offset)).fetchall()
            return {'items':[dict(row) for row in rows], 'limit':limit, 'offset':offset}


def make_team_router(*, actor_dependency, auth_repository_provider, team_repository_provider, verify_target):
    """Build a router for an existing API with verified, rate-limited actor dependency.

    Providers return the restricted API repository and separately credentialed
    trusted team repository. Mount only in an app with bounded request bodies,
    redacted validation errors, no-store responses and strict CORS. The caller
    must pass its verified, rate-limited actor dependency, not a request body UID.
    """
    from fastapi import APIRouter, Depends, HTTPException, Query

    router = APIRouter()

    @router.put('/v1/team/members/{target_uid}')
    def set_team_access(target_uid: str, body: TeamAccessRequest, identity=Depends(actor_dependency)):
        try:
            if not isinstance(identity, dict) or not isinstance(identity.get('uid'), str):
                raise Forbidden()
            service = TeamAdministration(auth_repository_provider(), team_repository_provider(), verify_target)
            return service.set_access(identity['uid'], target_uid=target_uid, role=body.role,
                                      active=body.active, properties=body.properties, expected_version=body.expected_version)
        except Forbidden: raise HTTPException(403, 'Access denied') from None
        except NotFound: raise HTTPException(404, 'Not found') from None
        except Conflict: raise HTTPException(409, 'Access change conflict') from None
        except ValueError: raise HTTPException(422, 'Invalid request') from None
        except Exception: raise HTTPException(503, 'Service unavailable') from None

    @router.get('/v1/team/members')
    def list_team(limit: int=Query(default=50,ge=1,le=100), offset: int=Query(default=0,ge=0,le=10000), identity=Depends(actor_dependency)):
        try:
            if not isinstance(identity, dict) or not isinstance(identity.get('uid'), str): raise Forbidden()
            return TeamAdministration(auth_repository_provider(),None,None).list_members(identity['uid'],limit=limit,offset=offset)
        except Forbidden: raise HTTPException(403,'Access denied') from None
        except Exception: raise HTTPException(503,'Service unavailable') from None

    @router.get('/v1/team/members/{target_uid}')
    def get_team_member(target_uid: str, identity=Depends(actor_dependency)):
        try:
            if not isinstance(identity, dict) or not isinstance(identity.get('uid'), str): raise Forbidden()
            return TeamAdministration(auth_repository_provider(),None,None).get_member(identity['uid'],target_uid)
        except Forbidden: raise HTTPException(403,'Access denied') from None
        except NotFound: raise HTTPException(404,'Not found') from None
        except ValueError: raise HTTPException(422,'Invalid request') from None
        except Exception: raise HTTPException(503,'Service unavailable') from None

    return router
