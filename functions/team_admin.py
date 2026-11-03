"""Emulator-only, authenticated prototype for scoped team administration.

The separate ``team_admin_http`` adapter exposes only a loopback emulator
endpoint. Any future production endpoint must forward an actual verified
Firebase ID token, never a client-supplied UID or role. Legacy collections and
the all-property snapshot remain under their existing owner-only rules.
"""
from __future__ import annotations

import os
import re
import uuid
from urllib.parse import urlsplit

from firebase_admin import auth, firestore

OWNER_UID = '8jOJNgLoxpfyseZ0RY1PDZ1DXbi2'
PROJECT_ID = 'demo-padsplit'
ROLES = {'manager', 'operator', 'viewer'}
STATUSES = {'open', 'in_progress', 'done'}
_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


def require_emulators():
    """Refuse to run unless both Firebase emulators are explicitly loopback-only."""
    project = os.environ.get('GCLOUD_PROJECT') or os.environ.get('GOOGLE_CLOUD_PROJECT')
    if project != PROJECT_ID:
        raise RuntimeError('Team administration requires the demo-padsplit emulator project.')
    for name in ('FIRESTORE_EMULATOR_HOST', 'FIREBASE_AUTH_EMULATOR_HOST'):
        value = os.environ.get(name, '')
        try:
            parsed = urlsplit('http://' + value)
            if parsed.hostname not in {'127.0.0.1', 'localhost'} or not parsed.port or parsed.path or parsed.query or parsed.username:
                raise ValueError
        except ValueError:
            raise RuntimeError(f'{name} must point to an explicit loopback emulator.') from None


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid identifier.')
    return value


def _team_fields(command):
    role, properties = command.get('role'), command.get('properties')
    if not isinstance(role, str) or role not in ROLES or not isinstance(properties, list) or not 1 <= len(properties) <= 50:
        raise ValueError('A supported role and one to fifty properties are required.')
    values = [_identifier(value) for value in properties]
    if len(values) != len(set(values)):
        raise ValueError('Properties must be unique.')
    return role, values


def _expected_keys(command, keys):
    if not isinstance(command, dict) or set(command) != set(keys):
        raise ValueError('Invalid command fields.')


def _task_fields(command):
    title, status = command.get('title'), command.get('status')
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200 or not isinstance(status, str) or status not in STATUSES:
        raise ValueError('A title and supported status are required.')
    return title.strip(), status


def run_action(id_token, command, *, db, app):
    """Verify the caller's token and atomically apply a permitted action and audit event."""
    require_emulators()
    if db.project != PROJECT_ID or app.project_id != PROJECT_ID:
        raise RuntimeError('Refusing a non-emulator project.')
    identity = auth.verify_id_token(id_token, app=app, check_revoked=True)
    actor = _identifier(identity.get('uid'))
    if not isinstance(command, dict):
        raise ValueError('Invalid command.')
    action = command.get('action')
    allowed = {
        'invite': ('action', 'uid', 'role', 'properties'),
        'accept_invitation': ('action', 'uid'),
        'update_access': ('action', 'uid', 'role', 'properties'),
        'deactivate': ('action', 'uid'),
        'create_task': ('action', 'property', 'task', 'title', 'status'),
        'update_task': ('action', 'property', 'task', 'title', 'status', 'expected_version'),
        'delete_task': ('action', 'property', 'task', 'expected_version'),
    }
    if not isinstance(action, str) or action not in allowed:
        raise ValueError('Unsupported action.')
    _expected_keys(command, allowed[action])
    team_action = action in {'invite', 'accept_invitation', 'update_access', 'deactivate'}
    if team_action:
        target = _identifier(command['uid'])
        if action == 'accept_invitation':
            if actor != target:
                raise PermissionError('Only the invited account may accept.')
        elif actor != OWNER_UID:
            raise PermissionError('Only the owner can administer membership.')
        if target == OWNER_UID:
            raise ValueError('The permanent owner cannot be changed.')
        if action in {'invite', 'update_access'}:
            role, properties = _team_fields(command)
        if action == 'invite':
            auth.get_user(target, app=app)  # Invitation targets a real emulator identity, not an arbitrary email.
    else:
        prop, task_id = _identifier(command['property']), _identifier(command['task'])
        target = f'{prop}/{task_id}'
        if action in {'create_task', 'update_task'}:
            title, status = _task_fields(command)
        if action != 'create_task' and (type(command['expected_version']) is not int or command['expected_version'] < 1):
            raise ValueError('An expected version is required.')

    event_ref = db.collection('team_admin_audit').document(uuid.uuid4().hex)

    @firestore.transactional
    def apply(transaction):
        if team_action:
            member_ref = db.collection('team_members').document(target)
            invite_ref = db.collection('team_invitations').document(target)
            member = member_ref.get(transaction=transaction)
            invite = invite_ref.get(transaction=transaction)
            member_data = member.to_dict() if member.exists else {}
            invite_data = invite.to_dict() if invite.exists else {}
            if action == 'invite':
                if member.exists or invite.exists:
                    raise ValueError('Account already has membership or an invitation.')
                transaction.create(invite_ref, {'role': role, 'properties': properties, 'status': 'pending'})
            elif action == 'accept_invitation':
                if not invite.exists or invite_data.get('status') != 'pending' or member.exists:
                    raise ValueError('No pending invitation exists.')
                transaction.create(member_ref, {'role': invite_data['role'], 'properties': invite_data['properties'], 'active': True})
                transaction.update(invite_ref, {'status': 'accepted'})
            elif action == 'update_access':
                if not member.exists or not member_data.get('active'):
                    raise ValueError('Active membership is required.')
                transaction.update(member_ref, {'role': role, 'properties': properties})
            else:
                if not member.exists or not member_data.get('active'):
                    raise ValueError('Active membership is required.')
                transaction.update(member_ref, {'active': False})
        else:
            member_ref = db.collection('team_members').document(actor)
            member = member_ref.get(transaction=transaction) if actor != OWNER_UID else None
            data = member.to_dict() if member and member.exists else {}
            permitted_roles = {'manager'} if action == 'delete_task' else {'manager', 'operator'}
            if actor != OWNER_UID and not (data.get('active') is True and data.get('role') in permitted_roles and prop in data.get('properties', [])):
                raise PermissionError('No permission for this property and action.')
            task_ref = db.collection('property_workspace').document(prop).collection('tasks').document(task_id)
            existing = task_ref.get(transaction=transaction)
            if action == 'create_task':
                if existing.exists:
                    raise ValueError('Task already exists.')
                transaction.create(task_ref, {'title': title, 'status': status, 'version': 1})
            else:
                if not existing.exists or existing.to_dict().get('version') != command['expected_version']:
                    raise ValueError('Task version conflict.')
                if action == 'update_task':
                    transaction.update(task_ref, {'title': title, 'status': status, 'version': command['expected_version'] + 1})
                else:
                    transaction.delete(task_ref)
        transaction.create(event_ref, {'actor': actor, 'action': action, 'target': target,
                                       'at': firestore.SERVER_TIMESTAMP, 'outcome': 'success'})
        return {'ok': True}

    return apply(db.transaction())
