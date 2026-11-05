"""Offline isolated PostgreSQL + age backup/restore smoke test."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT.parent / 'work'
NEEDED = ('initdb', 'pg_ctl', 'createdb', 'psql', 'pg_dump', 'pg_restore', 'age', 'age-keygen')

class RestoreTargetSafetyTest(unittest.TestCase):
    def test_restore_rejects_ambient_hostaddr_even_with_local_pghost(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            identity = d / 'identity'; identity.write_text('fixture')
            archive = d / 'archive'; archive.write_text('fixture')
            marker = d / 'invoked'
            for name in ('age', 'pg_restore'):
                executable = d / name
                executable.write_text('#!/bin/sh\ntouch "$TEST_MARKER"\n')
                executable.chmod(0o700)
            for override in ({'PGHOSTADDR':'192.0.2.1'}, {'PGSERVICE':'fixture'}, {'PGSERVICEFILE':str(d / 'service.conf')},
                             {'PGHOST':'/tmp/nonexistent,192.0.2.1'}):
                with self.subTest(override=next(iter(override))):
                    env = dict(os.environ, PATH=str(d) + os.pathsep + os.environ['PATH'],
                               PGHOST='127.0.0.1', TEST_MARKER=str(marker))
                    for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'): env.pop(key, None)
                    env.update(override)
                    result = subprocess.run([str(ROOT / 'deploy/restore-db.sh'), str(identity), str(archive),
                                             'padsplit_restore_fixture'], env=env, capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(marker.exists(), 'restore tools must not run with an overridden connection')

@unittest.skipUnless(all(shutil.which(x) for x in NEEDED), 'PostgreSQL and age tools required')
class BackupRestoreTest(unittest.TestCase):
    def test_encrypted_backup_roundtrip_and_failure_handling(self):
        WORK.mkdir(exist_ok=True)
        # macOS limits Unix socket path length, so only the ephemeral socket is
        # created under /tmp; all database files and outputs remain in work/.
        with tempfile.TemporaryDirectory(prefix='backup-rehearsal-', dir=WORK) as folder, tempfile.TemporaryDirectory(prefix='padsplit-sock-', dir='/tmp') as socket_dir:
            d = Path(folder); data = d / 'pg'; sock = Path(socket_dir)
            env = dict(os.environ, PGHOST=str(sock), PGUSER='fixture_owner', PGDATABASE='postgres')
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0)); env['PGPORT'] = str(s.getsockname()[1])
            def run(*cmd, **kw):
                return subprocess.run(cmd, env=kw.pop('env', env), check=kw.pop('check', True),
                                      capture_output=True, text=True, **kw)
            run('initdb', '-D', str(data), '-U', 'fixture_owner', '-A', 'trust', '--no-instructions')
            run('pg_ctl', '-D', str(data), '-l', str(d / 'pg.log'), '-o',
                f"-c listen_addresses='' -k {sock} -p {env['PGPORT']}", '-w', 'start')
            try:
                run('createdb', 'padsplit_fixture')
                run('createdb', 'padsplit_restore_fixture')
                run('psql', '-d', 'padsplit_fixture', '-v', 'ON_ERROR_STOP=1', '-c',
                    "CREATE TABLE sample (id integer PRIMARY KEY, value text); INSERT INTO sample VALUES (1, 'fictional');")
                ident = d / 'identity.txt'; gen = run('age-keygen', '-o', str(ident))
                recipient = next(line.split(': ', 1)[1] for line in gen.stderr.splitlines() if line.startswith('Public key: '))
                archive = d / 'archive.age'; source = dict(env, PGDATABASE='padsplit_fixture')
                run(str(ROOT / 'deploy/backup-db.sh'), recipient, str(archive), env=source)
                self.assertTrue(archive.read_bytes().startswith(b'age-encryption.org/'))
                self.assertNotIn(b'fictional', archive.read_bytes())
                self.assertEqual(archive.stat().st_mode & 0o077, 0)
                run(str(ROOT / 'deploy/restore-db.sh'), str(ident), str(archive), 'padsplit_restore_fixture')
                actual = run('psql', '-d', 'padsplit_restore_fixture', '-Atc', 'SELECT value FROM sample').stdout.strip()
                self.assertEqual(actual, 'fictional')
                bad = dict(env, PGDATABASE='missing_fixture_database'); failed = d / 'failed.age'
                self.assertNotEqual(run(str(ROOT / 'deploy/backup-db.sh'), recipient, str(failed), env=bad, check=False).returncode, 0)
                self.assertFalse(failed.exists())
                self.assertNotEqual(run(str(ROOT / 'deploy/restore-db.sh'), str(ident), str(archive), 'production', check=False).returncode, 0)
            finally:
                run('pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop')

if __name__ == '__main__': unittest.main()
