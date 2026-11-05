"""Encryption primitive for recoverable access-code maps.

Callers must authorize access and manage keys separately from PostgreSQL and
backup storage. This module does not expose endpoints or print plaintext.
"""
import base64
import json
import os
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def load_key(environ=None):
    env=os.environ if environ is None else environ
    try: key=base64.b64decode(env['PADSPLIT_CODES_KEY'],validate=True)
    except (KeyError,ValueError): raise ValueError('A base64-encoded 32-byte codes key is required') from None
    if len(key)!=32: raise ValueError('A base64-encoded 32-byte codes key is required')
    if env.get('PADSPLIT_MIGRATION_KEY') and env['PADSPLIT_CODES_KEY']==env['PADSPLIT_MIGRATION_KEY']:
        raise ValueError('Codes and migration backup keys must be distinct')
    return key


def _aad(property_id,version,key_version):
    if not isinstance(property_id,str) or not property_id or type(version) is not int or version<1 or not isinstance(key_version,str) or not key_version:
        raise ValueError('Invalid encryption context')
    return json.dumps(['padsplit-codes',property_id,version,key_version],separators=(',',':')).encode()


def encrypt_codes(fields,*,property_id,version,key_version,key):
    if not isinstance(fields,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in fields.items()):
        raise ValueError('Codes must be a string map')
    plaintext=json.dumps(fields,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    if len(plaintext)>100_000: raise ValueError('Code map is too large')
    nonce=secrets.token_bytes(12)
    return nonce,AESGCM(key).encrypt(nonce,plaintext,_aad(property_id,version,key_version))


def decrypt_codes(ciphertext,nonce,*,property_id,version,key_version,key):
    result=json.loads(AESGCM(key).decrypt(nonce,ciphertext,_aad(property_id,version,key_version)))
    if not isinstance(result,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in result.items()):
        raise ValueError('Invalid stored code map')
    return result


def _schema_aad(property_id,version,key_version):
    return b'code-schema\0'+_aad(property_id,version,key_version)


def encrypt_code_schema(schema,*,property_id,version,key_version,key):
    if not isinstance(schema,dict):raise ValueError('Invalid property schema')
    plaintext=json.dumps(schema,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    if len(plaintext)>500_000:raise ValueError('Property schema too large')
    nonce=secrets.token_bytes(12)
    return nonce,AESGCM(key).encrypt(nonce,plaintext,_schema_aad(property_id,version,key_version))


def decrypt_code_schema(ciphertext,nonce,*,property_id,version,key_version,key):
    result=json.loads(AESGCM(key).decrypt(nonce,ciphertext,_schema_aad(property_id,version,key_version)))
    if not isinstance(result,dict):raise ValueError('Invalid stored property schema')
    return result
