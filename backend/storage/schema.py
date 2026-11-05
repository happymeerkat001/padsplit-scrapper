"""Apply immutable numbered SQL migrations to an isolated staging database."""
import hashlib
from pathlib import Path
from backend.storage.migrate import staging_connect

MIGRATIONS=Path(__file__).parent/'migrations'


def apply_migrations(conn):
    # psycopg returns SQL_ASCII text as bytes and, more importantly, arbitrary
    # tenant text cannot be represented safely without a UTF-8 database.
    with conn.transaction():
        encoding=conn.execute('SHOW server_encoding').fetchone()[0]
        if isinstance(encoding,bytes): encoding=encoding.decode('ascii')
        if encoding.upper()!='UTF8':
            raise ValueError('Database must use UTF8 server encoding')
    files=sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    if not files: raise ValueError('No schema migrations found')
    applied=0
    for path in files:
        source=path.read_bytes(); digest=hashlib.sha256(source).hexdigest()
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(731905604)')
            conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            row=conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(path.stem,)).fetchone()
            if row:
                if row[0]!=digest: raise ValueError('Applied migration checksum mismatch')
            else:
                conn.execute(source.decode('utf8'))
                conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(path.stem,digest))
                applied+=1
    return applied


def main():
    try:
        with staging_connect() as conn: print('Schema migrations newly applied:',apply_migrations(conn))
    except Exception as exc:
        print(f'Schema migration stopped ({type(exc).__name__}).')
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
