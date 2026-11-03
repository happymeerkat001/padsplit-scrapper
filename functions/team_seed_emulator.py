"""Interactive helper to create fictional Auth emulator users; never production."""
import argparse
import getpass
import re

import firebase_admin
from firebase_admin import auth

from functions import team_admin, team_admin_http


def seed(uid, email, password, *, app):
    team_admin.require_emulators()
    if app.project_id != team_admin.PROJECT_ID:
        raise RuntimeError('Refusing a non-emulator project.')
    uid = team_admin._identifier(uid)
    if not isinstance(email, str) or not re.fullmatch(r'[A-Za-z0-9._%+-]{1,64}@example\.invalid', email):
        raise ValueError('Use a fictional address at example.invalid.')
    if not isinstance(password, str) or not 10 <= len(password) <= 256:
        raise ValueError('Choose an emulator-only password of 10 to 256 characters.')
    # create_user fails if this UID or email already exists; it never resets credentials.
    auth.create_user(uid=uid, email=email, password=password, app=app)


def main():
    parser = argparse.ArgumentParser(description='Create one fictional user in the Auth emulator')
    parser.add_argument('--emulator', action='store_true', required=True)
    parser.add_argument('--uid', required=True, help='Fictional emulator UID, or the fixed owner UID for bootstrap')
    args = parser.parse_args()
    team_admin.require_emulators()
    email = input('Fictional email at example.invalid: ').strip()
    password = getpass.getpass('New emulator-only password: ')
    app = firebase_admin.initialize_app(team_admin_http._EmulatorCredentials(), {'projectId':team_admin.PROJECT_ID}, name='team-seed-emulator')
    try:
        seed(args.uid, email, password, app=app)
    except (ValueError, auth.UidAlreadyExistsError, auth.EmailAlreadyExistsError):
        print('Could not create the emulator user. Validate the inputs and ensure the UID and email are new.')
        return 1
    finally:
        firebase_admin.delete_app(app)
    print('Fictional emulator user created.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
