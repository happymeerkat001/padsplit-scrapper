"""Provider adapter contract tested entirely offline with fictional data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT.parent / 'work'

@unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age tools required')
class UploadBackupTests(unittest.TestCase):
    def test_disabled_default_retention_and_upload_contract(self):
        with tempfile.TemporaryDirectory(prefix='upload-rehearsal-', dir=WORK) as folder:
            d=Path(folder); identity=d/'identity'; result=subprocess.run(['age-keygen','-o',str(identity)],capture_output=True,text=True,check=True)
            recipient=next(x.split(': ',1)[1] for x in result.stderr.splitlines() if x.startswith('Public key: '))
            archive=d/'fixture.age'; subprocess.run(['age','-r',recipient,'-o',str(archive)],input=b'fictional',check=True)
            calls=d/'calls'; adapter=d/'adapter'; adapter.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$CALLS"\n[ "$1" != "${FAIL_ACTION:-none}" ]\n');adapter.chmod(0o700)
            script=str(ROOT/'deploy/upload-backup.sh'); env=dict(os.environ,CALLS=str(calls),PADSPLIT_BACKUP_ADAPTER=str(adapter),PADSPLIT_BACKUP_RETENTION_DAYS='30')
            def run(env, path=archive): return subprocess.run([script,str(path),'bucket/fixture.age'],env=env,capture_output=True,text=True)
            self.assertNotEqual(run(env).returncode,0);self.assertFalse(calls.exists())
            env['PADSPLIT_BACKUP_UPLOAD_ENABLE']='1'
            self.assertEqual(run(env).returncode,0)
            self.assertEqual(calls.read_text().splitlines(),['verify-retention','put-if-absent','verify-object'])
            calls.unlink();env['FAIL_ACTION']='verify-retention'
            self.assertNotEqual(run(env).returncode,0);self.assertEqual(calls.read_text().splitlines(),['verify-retention'])
            calls.unlink();plain=d/'plaintext.age';plain.write_text('fictional')
            self.assertNotEqual(run(env,plain).returncode,0);self.assertFalse(calls.exists())
            self.assertNotEqual(subprocess.run([script,str(archive),'https://user:pass@example.invalid/?token=x'],env=env,capture_output=True).returncode,0)

if __name__=='__main__':unittest.main()
