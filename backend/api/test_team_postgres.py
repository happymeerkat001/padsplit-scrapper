"""Offline, isolated PostgreSQL tests for trusted team administration."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
import uuid

import psycopg
from backend.api.repository import Conflict, Forbidden, NotFound, Repository
from backend.api.team import LastOwner, TargetVerificationFailed, TeamAdministration, TeamConnectionNotIsolated, make_team_router
from backend.storage.schema import apply_migrations

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT.parent / 'work'
BINARIES = ('initdb', 'pg_ctl', 'createdb', 'dropdb')

class RoleRepository(Repository):
    def __init__(self,dsn,role): super().__init__(dsn.replace('user=fixture_owner',f'user={role}'))

class ProxyRoleRepository(Repository):
    def __init__(self,dsn,role,login='fixture_proxy'): super().__init__(dsn.replace('user=fixture_owner',f'user={login}')); self.role=role
    def connect(self):
        from psycopg import sql
        conn=super().connect()
        conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(self.role)))
        conn.commit()
        return conn

@unittest.skipUnless(all(shutil.which(x) for x in BINARIES), 'PostgreSQL tools required')
class TeamPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(exist_ok=True)
        cls.data_temp = tempfile.TemporaryDirectory(prefix='team-pg-', dir=WORK)
        cls.sock_temp = tempfile.TemporaryDirectory(prefix='team-sock-', dir='/tmp')
        cls.data = Path(cls.data_temp.name)/'db'; cls.sock = cls.sock_temp.name
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
        cls.env=dict(os.environ,PGHOST=cls.sock,PGPORT=str(cls.port),PGUSER='fixture_owner',PGDATABASE='postgres')
        cls.run_cmd('initdb','-D',str(cls.data),'-U','fixture_owner','-A','trust','--encoding=UTF8','--no-instructions')
        cls.run_cmd('pg_ctl','-D',str(cls.data),'-l',str(Path(cls.data_temp.name)/'pg.log'),'-o',
                    f"-c listen_addresses='' -k {cls.sock} -p {cls.port}",'-w','start')
        with psycopg.connect(f'host={cls.sock} port={cls.port} user=fixture_owner dbname=postgres') as conn:
            conn.execute('CREATE ROLE fixture_api LOGIN')
            conn.execute('CREATE ROLE fixture_team LOGIN')
            conn.execute('CREATE ROLE fixture_proxy LOGIN')
            conn.execute('CREATE ROLE fixture_leaky LOGIN')
            conn.execute('GRANT fixture_api,fixture_team TO fixture_proxy')
            conn.execute('GRANT fixture_api TO fixture_leaky')
    @classmethod
    def run_cmd(cls,*args):
        return subprocess.run(args,env=cls.env,check=True,capture_output=True,text=True)
    @classmethod
    def tearDownClass(cls):
        cls.run_cmd('pg_ctl','-D',str(cls.data),'-m','immediate','-w','stop')
        cls.sock_temp.cleanup();cls.data_temp.cleanup()
    def setUp(self):
        self.db='team_fixture_'+uuid.uuid4().hex[:10];self.run_cmd('createdb',self.db)
        self.dsn=f'host={self.sock} port={self.port} user=fixture_owner dbname={self.db}'
        with psycopg.connect(self.dsn) as conn:
            apply_migrations(conn)
            conn.execute("INSERT INTO app_users(firebase_uid) VALUES ('owner-a')")
            conn.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('owner-a','owner')")
            conn.execute("INSERT INTO properties(id,name) VALUES ('property-a','Example A'),('property-b','Example B')")
            conn.execute('GRANT USAGE ON SCHEMA public TO fixture_api,fixture_team')
            conn.execute('GRANT SELECT (firebase_uid,active) ON app_users TO fixture_api,fixture_team')
            conn.execute('GRANT SELECT (firebase_uid,role,team_access_version) ON user_roles TO fixture_api,fixture_team')
            conn.execute('GRANT SELECT (firebase_uid,property_id) ON property_access TO fixture_api,fixture_team')
            conn.execute('GRANT SELECT (id,name) ON properties TO fixture_api')
            conn.execute('GRANT SELECT (id) ON properties TO fixture_team')
            for table in ('app_users','user_roles','property_access'):
                conn.execute(f'GRANT UPDATE (auth_lock_guard) ON {table} TO fixture_api')
            conn.execute('GRANT INSERT (firebase_uid,active), UPDATE (active) ON app_users TO fixture_team')
            conn.execute('GRANT INSERT (firebase_uid,role), UPDATE (role,team_access_version) ON user_roles TO fixture_team')
            conn.execute('GRANT INSERT (firebase_uid,property_id), DELETE ON property_access TO fixture_team')
            conn.execute('GRANT INSERT (actor_uid,target_uid,action,role,active,property_count) ON team_admin_audit TO fixture_team')
            conn.execute('GRANT USAGE ON SEQUENCE team_admin_audit_id_seq TO fixture_team')
        self.repo=RoleRepository(self.dsn,'fixture_api')
        self.team_repo=RoleRepository(self.dsn,'fixture_team')
        self.lookup=lambda uid:{'uid':uid,'disabled':False}
        self.team=TeamAdministration(self.repo,self.team_repo,self.lookup)
    def tearDown(self): self.run_cmd('dropdb','--force',self.db)
    def set_member(self,actor,target,role,active=True,properties=None):
        with psycopg.connect(self.dsn) as conn:
            row=conn.execute('SELECT team_access_version FROM user_roles WHERE firebase_uid=%s',(target,)).fetchone()
        return self.team.set_access(actor,target_uid=target,role=role,active=active,properties=properties or [],expected_version=row[0] if row else 0)
    def test_provision_scope_audit_and_immutable_history(self):
        self.assertEqual(self.set_member('owner-a','operator-a','operator',properties=['property-a']),{'ok':True,'version':1})
        self.assertEqual(self.repo.identity('operator-a')['properties'],['property-a'])
        self.set_member('owner-a','operator-a','viewer',properties=['property-b'])
        self.assertEqual(self.repo.identity('operator-a')['properties'],['property-b'])
        with psycopg.connect(self.dsn) as conn:
            events=conn.execute('SELECT action,role,active,property_count FROM team_admin_audit ORDER BY id').fetchall()
            self.assertEqual(events,[('member.set_access','operator',True,1),('member.set_access','viewer',True,1)])
        for sql in ('UPDATE team_admin_audit SET role=\'owner\'','DELETE FROM team_admin_audit', 'TRUNCATE team_admin_audit'):
            with self.assertRaises(psycopg.Error):
                with psycopg.connect(self.dsn) as conn: conn.execute(sql)
    def test_denied_actor_target_validation_and_rollback(self):
        self.set_member('owner-a','viewer-a','viewer',properties=['property-a'])
        lookup_calls=[]
        denied=TeamAdministration(self.repo,self.team_repo,lambda uid:lookup_calls.append(uid))
        with self.assertRaises(Forbidden):denied.set_access('viewer-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        self.assertEqual(lookup_calls,[])
        for result in (None,{'uid':'wrong','disabled':False},{'uid':'other','disabled':True}):
            service=TeamAdministration(self.repo,self.team_repo,lambda _uid,result=result:result)
            with self.assertRaises(TargetVerificationFailed):service.set_access('owner-a',target_uid='other',role='viewer',active=True,properties=['property-a'],expected_version=0)
        with self.assertRaises(TargetVerificationFailed): TeamAdministration(self.repo,self.team_repo,None).set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        with self.assertRaises(NotFound):self.set_member('owner-a','other','viewer',properties=['missing'])
        self.assertIsNone(self.repo.identity('other'))
        with self.assertRaises(ValueError):self.set_member('owner-a','other','operator',properties=[])
    def test_last_owner_and_concurrent_deactivation(self):
        with self.assertRaises(LastOwner):self.set_member('owner-a','owner-a','viewer',properties=['property-a'])
        self.set_member('owner-a','owner-b','owner')
        barrier=threading.Barrier(2)
        def revoke(actor,target):
            local=TeamAdministration(self.repo,self.team_repo,lambda uid:(barrier.wait(timeout=5),{'uid':uid,'disabled':False})[1])
            try:local.set_access(actor,target_uid=target,role='owner',active=False,properties=[],expected_version=1);return 'ok'
            except (Forbidden,LastOwner):return 'denied'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(revoke,'owner-a','owner-b'),pool.submit(revoke,'owner-b','owner-a')]
            outcomes=[x.result(timeout=10) for x in futures]
        self.assertCountEqual(outcomes,['ok','denied'])
        with psycopg.connect(self.dsn) as conn:
            count=conn.execute("SELECT count(*) FROM app_users u JOIN user_roles r USING(firebase_uid) WHERE u.active AND r.role='owner'").fetchone()[0]
            self.assertEqual(count,1)
    def test_direct_concurrent_owner_demotions_cannot_remove_both(self):
        self.set_member('owner-a','owner-b','owner')
        barrier=threading.Barrier(2)
        def demote(uid):
            conn=psycopg.connect(self.dsn)
            try:
                conn.execute("UPDATE user_roles SET role='viewer' WHERE firebase_uid=%s",(uid,))
                barrier.wait(timeout=5)
                conn.commit()
                return 'ok'
            except psycopg.Error:return 'denied'
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(demote,['owner-a','owner-b']))
        self.assertCountEqual(results,['ok','denied'])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM app_users u JOIN user_roles r USING(firebase_uid) WHERE u.active AND r.role='owner'").fetchone()[0],1)

    def test_direct_concurrent_owner_deactivations_cannot_remove_both(self):
        self.set_member('owner-a','owner-b','owner')
        barrier=threading.Barrier(2)
        def deactivate(uid):
            conn=psycopg.connect(self.dsn)
            try:
                conn.execute('UPDATE app_users SET active=false WHERE firebase_uid=%s',(uid,))
                barrier.wait(timeout=5)
                conn.commit()
                return 'ok'
            except psycopg.Error:return 'denied'
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(deactivate,['owner-a','owner-b']))
        self.assertCountEqual(results,['ok','denied'])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM app_users u JOIN user_roles r USING(firebase_uid) WHERE u.active AND r.role='owner'").fetchone()[0],1)
    def test_scope_change_waits_for_inflight_authorization(self):
        self.set_member('owner-a','operator-a','operator',properties=['property-a'])
        with self.repo.connect() as reader:
            self.assertEqual(self.repo._identity(reader,'operator-a',lock=True)['properties'],['property-a'])
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.set_member,'owner-a','operator-a','operator',True,['property-b'])
                with self.assertRaises(TimeoutError):future.result(timeout=.3)
                reader.commit()
                self.assertEqual(future.result(timeout=5),{'ok':True,'version':2})
        self.assertEqual(self.repo.identity('operator-a')['properties'],['property-b'])

    def test_owner_can_read_versioned_roster_and_stale_edits_conflict(self):
        self.set_member('owner-a','operator-a','operator',properties=['property-a'])
        record=self.team.get_member('owner-a','operator-a')
        self.assertEqual(record,{'uid':'operator-a','active':True,'role':'operator','version':1,'properties':['property-a']})
        self.assertNotIn('email',record)
        self.set_member('owner-a','operator-a','viewer',properties=['property-b'])
        with self.assertRaises(Conflict):
            self.team.set_access('owner-a',target_uid='operator-a',role='operator',active=True,properties=['property-a'],expected_version=1)
        self.assertEqual(self.team.get_member('owner-a','operator-a')['version'],2)
        page=self.team.list_members('owner-a',limit=1,offset=0)
        self.assertEqual(len(page['items']),1);self.assertNotIn('email',page['items'][0])
        with self.assertRaises(Forbidden):self.team.list_members('operator-a',limit=50,offset=0)
        with self.assertRaises(Forbidden):self.team.get_member('operator-a','owner-a')
        with self.assertRaises(ValueError):self.team.list_members('owner-a',limit=101,offset=0)

    def test_concurrent_same_version_edits_produce_one_conflict(self):
        self.set_member('owner-a','operator-a','operator',properties=['property-a'])
        barrier=threading.Barrier(2)
        def update(prop):
            team=TeamAdministration(self.repo,self.team_repo,lambda uid:(barrier.wait(timeout=5),{'uid':uid,'disabled':False})[1])
            try:
                team.set_access('owner-a',target_uid='operator-a',role='operator',active=True,properties=[prop],expected_version=1)
                return 'ok'
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(update,['property-a','property-b']))
        self.assertCountEqual(results,['ok','conflict'])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT team_access_version FROM user_roles WHERE firebase_uid='operator-a'").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT count(*) FROM team_admin_audit WHERE target_uid='operator-a'").fetchone()[0],2)

    def test_standard_api_role_cannot_mutate_members_or_be_the_team_connection(self):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.repo.connect() as conn:
                conn.execute("UPDATE user_roles SET role='viewer' WHERE firebase_uid='owner-a'")
        same_role=RoleRepository(self.dsn,'fixture_api')
        for team_repo in (self.repo,same_role,None):
            service=TeamAdministration(self.repo,team_repo,self.lookup)
            with self.assertRaises(TeamConnectionNotIsolated):
                service.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)

    def test_team_role_cannot_change_emails_delete_members_or_rewrite_audit(self):
        self.set_member('owner-a','viewer-a','viewer',properties=['property-a'])
        for statement in ("UPDATE app_users SET email='fictional@example.invalid' WHERE firebase_uid='viewer-a'",
                          "DELETE FROM user_roles WHERE firebase_uid='viewer-a'",
                          "DELETE FROM app_users WHERE firebase_uid='viewer-a'",
                          'DELETE FROM team_admin_audit', 'TRUNCATE team_admin_audit'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.team_repo.connect() as conn:conn.execute(statement)

    def test_excess_api_privileges_and_superuser_team_connection_fail_closed(self):
        privileged=TeamAdministration(self.repo,Repository(self.dsn),self.lookup)
        with self.assertRaises(TeamConnectionNotIsolated):
            privileged.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('GRANT UPDATE (role) ON user_roles TO fixture_api')
        with self.assertRaises(TeamConnectionNotIsolated):
            self.team.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        self.assertIsNone(self.repo.identity('other'))

    def test_team_role_dangerous_misgrants_fail_closed(self):
        grants = [
            ('GRANT TRUNCATE ON app_users TO fixture_team', 'REVOKE TRUNCATE ON app_users FROM fixture_team'),
            ('GRANT TRUNCATE ON team_admin_audit TO fixture_team', 'REVOKE TRUNCATE ON team_admin_audit FROM fixture_team'),
            ('GRANT TRIGGER ON user_roles TO fixture_team', 'REVOKE TRIGGER ON user_roles FROM fixture_team'),
            ('GRANT UPDATE (email) ON app_users TO fixture_team', 'REVOKE UPDATE (email) ON app_users FROM fixture_team'),
            ('GRANT SELECT (email) ON app_users TO fixture_team', 'REVOKE SELECT (email) ON app_users FROM fixture_team'),
            ('GRANT CREATE ON SCHEMA public TO fixture_team', 'REVOKE CREATE ON SCHEMA public FROM fixture_team'),
        ]
        for grant,revoke in grants:
            with self.subTest(grant=grant):
                with psycopg.connect(self.dsn) as conn:conn.execute(grant)
                try:
                    with self.assertRaises(TeamConnectionNotIsolated):
                        self.team.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
                finally:
                    with psycopg.connect(self.dsn) as conn:conn.execute(revoke)

    def test_same_session_or_membership_that_can_assume_team_role_is_rejected(self):
        proxied_api=ProxyRoleRepository(self.dsn,'fixture_api')
        proxied_team=ProxyRoleRepository(self.dsn,'fixture_team')
        for team_repo in (self.team_repo,proxied_team):
            service=TeamAdministration(proxied_api,team_repo,self.lookup)
            with self.assertRaises(TeamConnectionNotIsolated):
                service.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        self.assertIsNone(self.repo.identity('other'))

    def test_session_principal_with_direct_auth_write_privileges_is_rejected(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('GRANT UPDATE (role) ON user_roles TO fixture_leaky')
        api=ProxyRoleRepository(self.dsn,'fixture_api',login='fixture_leaky')
        with self.assertRaises(TeamConnectionNotIsolated):
            TeamAdministration(api,self.team_repo,self.lookup).set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)

    def test_different_table_resolution_between_roles_is_rejected(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('CREATE SCHEMA shadow')
            conn.execute('CREATE TABLE shadow.app_users(dummy integer)')
            conn.execute('GRANT USAGE ON SCHEMA shadow TO fixture_team')
            conn.execute('ALTER ROLE fixture_team SET search_path=shadow,public')
        try:
            with self.assertRaises(TeamConnectionNotIsolated):
                self.team.set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        finally:
            with psycopg.connect(self.dsn) as conn:
                conn.execute('ALTER ROLE fixture_team RESET search_path')

    def test_same_named_database_on_another_postgres_server_is_rejected(self):
        other=Path(self.data_temp.name)/('other-'+uuid.uuid4().hex[:8])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));other_port=sock.getsockname()[1]
        self.run_cmd('initdb','-D',str(other),'-U','fixture_owner','-A','trust','--encoding=UTF8','--no-instructions')
        self.run_cmd('pg_ctl','-D',str(other),'-l',str(other)+'.log','-o',
                     f"-c listen_addresses='' -k {self.sock} -p {other_port}",'-w','start')
        other_dsn=f'host={self.sock} port={other_port} user=fixture_owner dbname=postgres'
        try:
            with psycopg.connect(other_dsn,autocommit=True) as conn:
                conn.execute('CREATE ROLE fixture_team LOGIN')
                conn.execute(f'CREATE DATABASE {self.db}')
            other_team=RoleRepository(other_dsn.replace('dbname=postgres',f'dbname={self.db}'),'fixture_team')
            with self.assertRaises(TeamConnectionNotIsolated):
                TeamAdministration(self.repo,other_team,self.lookup).set_access('owner-a',target_uid='other',role='owner',active=True,properties=[],expected_version=0)
        finally:
            self.run_cmd('pg_ctl','-D',str(other),'-m','immediate','-w','stop')

    def test_http_router_uses_trusted_actor_and_generic_errors(self):
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        app=FastAPI()
        @app.exception_handler(RequestValidationError)
        async def invalid(_request, _exc):return JSONResponse({'error':'Invalid request'},status_code=422)
        def actor(authorization: str | None=Header(default=None)):
            if authorization is None:raise HTTPException(401,'Authentication required')
            return {'uid':authorization}
        def router(lookup, team_repo):
            return make_team_router(actor_dependency=actor,auth_repository_provider=lambda:self.repo,
                                    team_repository_provider=lambda:team_repo,verify_target=lookup)
        app.include_router(router(self.lookup,self.team_repo))
        client=TestClient(app)
        url='/v1/team/members/viewer-a'; data={'role':'viewer','active':True,'properties':['property-a'],'expected_version':0}
        self.assertEqual(client.put(url,json=data).status_code,401)
        self.assertEqual(client.put(url,headers={'authorization':'owner-a'},json=data).status_code,200)
        self.assertEqual(client.get(url,headers={'authorization':'owner-a'}).json()['version'],1)
        self.assertEqual(client.get('/v1/team/members?limit=1&offset=0',headers={'authorization':'owner-a'}).status_code,200)
        self.assertEqual(client.get('/v1/team/members',headers={'authorization':'viewer-a'}).status_code,403)
        self.assertEqual(client.get(url,headers={'authorization':'viewer-a'}).status_code,403)
        self.assertEqual(client.put('/v1/team/members/target',headers={'authorization':'viewer-a'},json=data).status_code,403)
        r=client.put(url,headers={'authorization':'viewer-a'},json={**data,'actor_uid':'synthetic-private-input'})
        self.assertEqual(r.status_code,422); self.assertNotIn('synthetic-private-input',r.text)
        r=client.put('/v1/team/members/owner-a',headers={'authorization':'owner-a'},json={**data,'expected_version':1})
        self.assertEqual(r.status_code,409); self.assertNotIn('owner-a',r.text)
        app2=FastAPI();app2.include_router(router(lambda _uid:(_ for _ in ()).throw(RuntimeError('synthetic-private-provider-error')),self.team_repo))
        r=TestClient(app2).put(url,headers={'authorization':'owner-a'},json=data)
        self.assertEqual(r.status_code,503);self.assertNotIn('synthetic-private-provider-error',r.text)
        app3=FastAPI();app3.include_router(router(self.lookup,self.repo))
        self.assertEqual(TestClient(app3).put(url,headers={'authorization':'owner-a'},json=data).status_code,503)

if __name__=='__main__':unittest.main()
