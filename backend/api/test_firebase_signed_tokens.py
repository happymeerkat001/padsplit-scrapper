"""Offline signed-token exercise: real Firebase Admin JWT validation, stubbed Google services.

No real Firebase project, credentials, user records, or network are used. This
is not a Firebase emulator or live authentication test.
"""
import datetime
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import firebase_admin
from firebase_admin import auth, credentials
from google.auth import credentials as google_credentials, crypt, jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from backend.api.app import create_app, firebase_verifier
from backend.api.repository import Forbidden


class OfflineCredentials(credentials.Base):
    def get_credential(self): return google_credentials.AnonymousCredentials()


class FakeRepository:
    """Only the identity provider is emulated here; DB policies have separate tests."""
    roles={'owner':'owner','scoped':'viewer','reporter':'viewer','viewer':'viewer','revoked':'viewer','disabled':'viewer'}
    def identity(self, uid):
        if uid not in self.roles:return None
        return {'uid':uid,'role':self.roles[uid],'properties':['fixture-house'] if uid=='scoped' else []}
    def ready(self):return True
    def properties(self, uid):return [{'id':'fixture-house','name':'Fictional'}] if uid in ('owner','scoped') else []
    def report_access(self, uid):return uid in ('owner','reporter')
    def report(self, uid, ident):
        if not self.report_access(uid):raise Forbidden()
        return {'id':ident,'json':'{"kpis":{"listed":3}}','observed_at':None}
    def codes_schema(self, uid):
        if uid!='owner':raise Forbidden()
        return {'properties':[],'versions':{}}


class SignedFirebaseTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=patch.dict(os.environ,{'FIREBASE_AUTH_EMULATOR_HOST':''});cls.env.start()
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        now=datetime.datetime.now(datetime.timezone.utc)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fictional.local')])
        cls.cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(cls.key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
                  .not_valid_after(now+datetime.timedelta(days=1)).sign(cls.key,hashes.SHA256())
                  .public_bytes(serialization.Encoding.PEM).decode())
        cls.signer=crypt.RSASigner.from_string(cls.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cls.app=firebase_admin.initialize_app(OfflineCredentials(),{'projectId':'fictional-project'},name='operations-api')
        cls.client=auth._get_client(cls.app)
        cls.cert_patch=patch.object(cls.client._token_verifier,'request',lambda *a,**kw:SimpleNamespace(status=200,data=json.dumps({'fictional-kid':cls.cert}).encode()))
        cls.cert_patch.start()
        cls.user_patch=patch.object(cls.client,'get_user',side_effect=lambda uid:SimpleNamespace(
            disabled=uid=='disabled',tokens_valid_after_timestamp=(int(time.time())+60)*1000 if uid=='revoked' else 0))
        cls.user_patch.start()
        cls.verify=staticmethod(firebase_verifier('fictional-project'))

    @classmethod
    def tearDownClass(cls):
        cls.user_patch.stop();cls.cert_patch.stop();firebase_admin.delete_app(cls.app);cls.env.stop()

    def token(self, uid='owner', *, audience='fictional-project', issuer=None, issued=None, expires=None, signer=None):
        now=int(time.time());iat=now-5 if issued is None else issued
        payload={'iss':issuer or 'https://securetoken.google.com/fictional-project','aud':audience,
                 'sub':uid,'iat':iat,'exp':now+300 if expires is None else expires,'auth_time':iat}
        return jwt.encode(signer or self.signer,payload,key_id='fictional-kid').decode()

    def setUp(self):
        self.http=TestClient(create_app(repository=FakeRepository(),verify_token=self.verify,allowed_origins=['https://frontend.example']))
        self.http.__enter__();self.addCleanup(self.http.__exit__,None,None,None)

    def get(self,path, token=None):
        return self.http.get(path,headers={'Authorization':'Bearer '+token} if token is not None else {})

    def test_signed_identity_scope_and_report_only_routes(self):
        self.assertEqual(self.get('/v1/properties').status_code,401)
        owner=self.token('owner');scoped=self.token('scoped');reporter=self.token('reporter');viewer=self.token('viewer')
        self.assertEqual(self.get('/v1/me',owner).json()['role'],'owner')
        self.assertEqual(self.get('/v1/properties',scoped).json()['items'][0]['id'],'fixture-house')
        self.assertEqual(self.get('/v1/properties',viewer).json()['items'],[])
        self.assertEqual(self.get('/v1/reports/latest',reporter).status_code,200)
        self.assertEqual(self.get('/v1/reports/latest',viewer).status_code,403)
        self.assertEqual(self.get('/v1/codes/schema',reporter).status_code,403)
        self.assertEqual(self.get('/v1/codes/schema',owner).status_code,200)

    def test_signature_claim_expiry_revocation_and_disabled_checks(self):
        now=int(time.time());other_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        other_signer=crypt.RSASigner.from_string(other_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        bad=[self.token(expires=now-10),self.token(audience='other-project'),
             self.token(issuer='https://securetoken.google.com/other-project'),self.token(signer=other_signer),
             self.token('revoked'),self.token('disabled'),'not-a-jwt']
        for token in bad:
            with self.subTest(case=bad.index(token)):
                response=self.get('/v1/me',token)
                self.assertEqual(response.status_code,401)
                self.assertNotIn(token,response.text)
                self.assertEqual(response.headers['cache-control'],'no-store')

    def test_certificate_outage_fails_closed_without_leaking_details(self):
        from google.auth.exceptions import TransportError
        with patch.object(self.client._token_verifier,'request',side_effect=TransportError('sensitive simulated outage')):
            response=self.get('/v1/me',self.token())
        self.assertEqual(response.status_code,503)
        self.assertNotIn('sensitive',response.text)


if __name__=='__main__':unittest.main()
