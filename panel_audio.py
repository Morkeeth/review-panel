"""Private retention of existing mic chunks. No recording or network transport.

Original audio is immutable. A final ACK follows audio, metadata and feedback
fsync. Pending transcription keeps audio, but never earns a final durable ACK.
"""
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

MAX_AUDIO = 4 * 1024 * 1024
MIN_FREE = 3 * 1024 ** 3

MIMES = {'audio/webm': 'webm', 'audio/mp4': 'm4a',
         'audio/ogg': 'ogg', 'audio/wav': 'wav'}


class SaveError(Exception):
    def __init__(self, message, status=507, audio_durable=False):
        super().__init__(message)
        self.status = status
        self.audio_durable = audio_durable

    def response(self):
        return {'ok': False, 'durable': False, 'audio_durable': self.audio_durable,
                'feedback_status': 'pending', 'error': str(self)}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _resync_file(path):
    with path.open('rb') as f:
        os.fsync(f.fileno())


def _private_dir(path):
    if path.is_symlink():
        raise SaveError('Audio storage path is not a private directory.')
    missing = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    # Newly created private directories themselves must survive an ACK, not
    # just the file entries they contain (notably first recovery grant).
    for created in missing:
        _sync_dir(created)
        _sync_dir(created.parent)


def _new_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        # The caller retains its original blob until ACK. No final capture is
        # exposed until the whole staging directory has been committed.
        raise


def _atomic_json(path, value):
    temporary = path.parent / ('.' + path.name + '-' + uuid.uuid4().hex)
    _new_file(temporary, _json(value))
    os.replace(temporary, path)
    _sync_dir(path.parent)


class CaptureStore:
    def __init__(self, voice_log, preferred=None, fallback=None,
                 preferred_available=None, free_bytes=None, min_free=MIN_FREE):
        self.voice_log = Path(voice_log)
        self.preferred = Path(preferred or self.voice_log.parent / 'audio')
        self.fallback = Path(fallback or self.voice_log.parent / 'audio-spool')
        self._preferred_checked = 0
        self._preferred_healthy = False
        self.preferred_available = (preferred_available if preferred_available is not None
                                    else self._preferred_ready)
        self.free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self.min_free = min_free
        self.lock = threading.RLock()

    def _preferred_ready(self):
        return True

    def _headroom(self):
        return self.free_bytes(self.voice_log.parent) >= self.min_free

    def _roots(self):
        # The configured archive and fallback both retain originals.
        roots = [self.preferred] if self.preferred_available() else []
        return roots + [self.fallback]

    def _identity(self, request, row):
        try:
            cid = str(uuid.UUID(str(request.get('chunk_id', ''))))
        except (ValueError, AttributeError):
            raise SaveError('chunk_id must be a UUID.', 400)
        if cid != request.get('chunk_id'):
            raise SaveError('chunk_id must be a canonical UUID.', 400)
        encoded = request.get('audio_base64')
        if not isinstance(encoded, str) or len(encoded) > ((MAX_AUDIO + 2) // 3) * 4:
            raise SaveError('Audio chunk exceeds the allowed size.', 413)
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise SaveError('Audio chunk is not valid base64.', 400)
        if not audio or len(audio) > MAX_AUDIO:
            raise SaveError('Audio chunk must contain between 1 byte and 4 MiB.', 413)
        mime = str(request.get('audio_type', '')).split(';', 1)[0].lower()
        if mime not in MIMES:
            raise SaveError('Unsupported audio type.', 400)
        card = request.get('card')
        if not isinstance(card, str) or not card or len(card) > 200 or row.get('card') != card:
            raise SaveError('Capture card does not match feedback card.', 400)
        shown = request.get('shown_sha256') or ''
        if shown and (not isinstance(shown, str) or len(shown) != 64 or
                      any(c not in '0123456789abcdef' for c in shown)):
            raise SaveError('Invalid displayed card hash.', 400)
        info = {'chunk_id': cid, 'card': card, 'shown_sha256': shown,
                'audio_sha256': _sha(audio), 'audio_bytes': len(audio),
                'audio_type': mime, 'mic_session_id': str(request.get('mic_session_id') or '')[:200],
                'segment_started_at': str(request.get('segment_started_at') or '')[:100],
                'segment_ended_at': str(request.get('segment_ended_at') or '')[:100]}
        for key in ('segment_started_at', 'segment_ended_at'):
            if not info[key]:
                raise SaveError('Segment start and end times are required.', 400)
            try:
                value = dt.datetime.fromisoformat(info[key].replace('Z', '+00:00'))
                if value.tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise SaveError('Segment times must include a time zone.', 400)
        if info['segment_started_at'] and (dt.datetime.fromisoformat(info['segment_started_at'].replace('Z', '+00:00')) >
                                          dt.datetime.fromisoformat(info['segment_ended_at'].replace('Z', '+00:00'))):
            raise SaveError('Segment end precedes its start.', 400)
        context = request.get('capture_context')
        if context is not None:
            if not isinstance(context, dict) or len(_json(context)) > 64000:
                raise SaveError('Invalid capture context.', 400)
            transitions = context.get('transitions')
            if (not isinstance(transitions, list) or len(transitions) > 500 or
                    not isinstance(context.get('mixed'), bool) or
                    context['mixed'] != bool(transitions)):
                raise SaveError('Invalid capture transitions.', 400)
            start = context.get('start')
            if not isinstance(start, dict) or start.get('card') != card or start.get('shown_sha256', '') != shown:
                raise SaveError('Capture context does not match its original card.', 400)
            for point in [start, context.get('end'), *transitions]:
                if (not isinstance(point, dict) or not isinstance(point.get('card'), str) or
                        len(point['card']) > 200 or not isinstance(point.get('shown_sha256', ''), str)):
                    raise SaveError('Invalid capture context card.', 400)
            # Preserve the submitted timeline exactly; it describes observed page
            # context, not verified speaker intent or sentence-to-card assignment.
            info['capture_context'] = json.loads(_json(context))
        return info, audio

    def _existing(self, info):
        found = []
        for root in self._roots():
            path = root / info['chunk_id']
            if path.is_symlink():
                raise SaveError('Capture storage is not a private directory.')
            if not path.exists():
                continue
            manifest_path = path / 'manifest.json'
            if manifest_path.is_symlink():
                raise SaveError('Capture metadata is not a regular file.')
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('capture') != info:
                raise SaveError('Capture ID already belongs to different evidence.', 409)
            original = path / manifest['audio_filename']
            if original.is_symlink() or _sha(original.read_bytes()) != info['audio_sha256']:
                raise SaveError('Stored original audio failed its integrity check.')
            _resync_file(original)
            _resync_file(manifest_path)
            _sync_dir(path)
            _sync_dir(root)
            found.append(path)
        return found[0] if found else None

    def _archive(self, info, audio, row, admitted=False):
        existing = self._existing(info)
        if existing:
            return existing
        for root in self._roots():
            if root == self.fallback and not self._headroom():
                continue
            staging = None
            try:
                _private_dir(root)
                staging = Path(tempfile.mkdtemp(prefix='.capture-', dir=root))
                os.chmod(staging, 0o700)
                filename = 'original.' + MIMES[info['audio_type']]
                _new_file(staging / filename, audio)
                _new_file(staging / 'manifest.json', _json({
                    'schema': 'panel.audio-capture/1', 'capture': info,
                    'capture_admitted': bool(admitted),
                    'audio_filename': filename,
                    'server_received_at': dt.datetime.now().astimezone().isoformat(),
                    'server_card_sha256': row.get('card_sha256'),
                    'server_card_basis': row.get('card_basis'),
                    'attribution_basis': 'single-user panel log',
                    'speaker_verification': 'not_verified',
                    'review_authorship_status': 'unadjudicated'}))
                _sync_dir(staging)
                final = root / info['chunk_id']
                os.rename(staging, final)
                _sync_dir(root)
                return final
            except FileExistsError:
                return self._existing(info)
            except OSError:
                # Another worker can have committed the same immutable capture
                # while this worker was preparing its staging directory.
                existing = self._existing(info)
                if existing:
                    return existing
                # A failed preferred write may fall back to the private spool.
                # The original browser blob remains unacknowledged on failure.
                continue
            finally:
                if staging is not None and staging.exists():
                    shutil.rmtree(staging)
        raise SaveError('Original audio could not be saved. Keep this recording and retry.')

    @staticmethod
    def _ack(info, final):
        return {'ok': True, 'durable': bool(final), 'audio_durable': True,
                'archive_status': 'durable',
                'feedback_status': 'durable' if final else 'pending',
                'chunk_id': info['chunk_id'], 'audio_sha256': info['audio_sha256']}

    def _finish(self, entry, info, request, row):
        text = request.get('text', '')
        if not isinstance(text, str) or len(text) > 8000:
            raise SaveError('Transcript text exceeds the allowed size.', 413, True)
        if not text.strip():
            return self._ack(info, False)
        if row.get('text') != text:
            raise SaveError('Transcript text differs from feedback text.', 400, True)
        raw_asr = request.get('raw_asr', text)
        if not isinstance(raw_asr, str) or len(raw_asr) > 8000:
            raise SaveError('Raw transcription exceeds the allowed size.', 413, True)
        fingerprint = _sha(_json({'capture': info, 'text': text, 'raw_asr': raw_asr}))
        # One receipt namespace is shared across external and fallback storage,
        # so a mount transition cannot create a second feedback event.
        receipts = self.voice_log.parent / 'audio-capture-receipts'
        final_path = receipts / (info['chunk_id'] + '.json')
        if final_path.exists():
            self._verify_receipt(final_path, fingerprint, info)
            return self._ack(info, True)
        if not self._headroom():
            raise SaveError('Feedback storage has insufficient headroom. Audio is retained; retry later.',
                            507, True)
        self.voice_log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_dir(receipts)
        row = dict(row, chunk_id=info['chunk_id'], audio_sha256=info['audio_sha256'],
                   audio_type=info['audio_type'], audio_bytes=info['audio_bytes'],
                   audio=str(entry / ('original.' + MIMES[info['audio_type']])),
                   audio_manifest=str(entry / 'manifest.json'), audio_archive_status='durable',
                   mic_session_id=info['mic_session_id'],
                   segment_started_at=info['segment_started_at'], segment_ended_at=info['segment_ended_at'],
                   attribution_basis='single-user panel log', speaker_verification='not_verified',
                   review_authorship_status='unadjudicated')
        row['raw_asr'] = raw_asr
        if 'capture_context' in info:
            row['capture_context'] = info['capture_context']
        pending_path = receipts / (info['chunk_id'] + '.pending.json')
        with self.voice_log.open('a+b') as log:
            os.chmod(self.voice_log, 0o600)
            fcntl.flock(log.fileno(), fcntl.LOCK_EX)
            try:
                if final_path.exists():
                    self._verify_receipt(final_path, fingerprint, info)
                    return self._ack(info, True)
                if pending_path.exists():
                    pending = json.loads(pending_path.read_text())
                    if pending['fingerprint'] != fingerprint:
                        raise SaveError('Capture has a different pending transcript.', 409, True)
                    # Recover only this unfinished append, starting at its saved
                    # byte offset. No old human feedback is rewritten or erased.
                    row = pending['row']
                    log.seek(pending['offset'])
                    while True:
                        recovered_offset = log.tell()
                        raw = log.readline()
                        if not raw:
                            break
                        try:
                            prior = json.loads(raw)
                        except (ValueError, UnicodeError):
                            continue
                        if prior.get('chunk_id') == info['chunk_id']:
                            if prior.get('text') != text:
                                raise SaveError('Conflicting feedback already exists.', 409, True)
                            log.flush()
                            os.fsync(log.fileno())
                            _atomic_json(final_path, {'fingerprint': fingerprint,
                                                     'source': str(self.voice_log),
                                                     'byte_offset': recovered_offset,
                                                     'row_bytes': len(raw),
                                                     'row_sha256': _sha(raw),
                                                     'recovered': True})
                            _atomic_json(entry / 'feedback-link.json',
                                         {'receipt': str(final_path)})
                            return self._ack(info, True)
                log.seek(0, os.SEEK_END)
                offset = log.tell()
                if offset:
                    log.seek(-1, os.SEEK_END)
                    if log.read(1) != b'\n':
                        log.write(b'\n')  # preserve a partial historical line
                        log.flush()
                    log.seek(0, os.SEEK_END)
                    offset = log.tell()
                _atomic_json(pending_path, {'fingerprint': fingerprint, 'offset': offset,
                                            'row': row})
                encoded = _json(row) + b'\n'
                log.write(encoded)
                log.flush()
                os.fsync(log.fileno())
                _atomic_json(final_path, {'fingerprint': fingerprint,
                                         'source': str(self.voice_log),
                                         'byte_offset': offset, 'row_bytes': len(encoded),
                                         'row_sha256': _sha(encoded)})
                _atomic_json(entry / 'feedback-link.json', {'receipt': str(final_path)})
                return self._ack(info, True)
            finally:
                fcntl.flock(log.fileno(), fcntl.LOCK_UN)

    def _verify_receipt(self, path, fingerprint, info):
        committed = json.loads(path.read_text())
        if committed['fingerprint'] != fingerprint:
            raise SaveError('Capture already has a different transcript.', 409, True)
        with self.voice_log.open('rb') as f:
            f.seek(committed['byte_offset'])
            raw = f.read(committed['row_bytes'])
            os.fsync(f.fileno())
        if _sha(raw) != committed['row_sha256'] or json.loads(raw).get('chunk_id') != info['chunk_id']:
            raise SaveError('Saved feedback failed its integrity check. Original audio is retained.',
                            507, True)
        # An earlier request may have lost its response or failed after rename
        # but before directory fsync. Retry does not turn visibility into durability.
        _resync_file(path)
        _sync_dir(path.parent)

    def retained_original(self, chunk_id, body, recovery_token=''):
        """Read-only proof for retrying ASR after capture permission expired."""
        try:
            if str(uuid.UUID(chunk_id)) != chunk_id:
                return False
            for root in self._roots():
                entry = root / chunk_id
                manifest_path = entry / 'manifest.json'
                if entry.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text())
                original = entry / manifest['audio_filename']
                if original.is_symlink():
                    return False
                digest = manifest['capture']['audio_sha256']
                admitted = self.admission(chunk_id) is True and manifest.get('capture_admitted') is True
                if not admitted and not self._recovery_valid(chunk_id, digest, recovery_token):
                    return False
                if _sha(body) == digest and _sha(original.read_bytes()) == digest:
                    return True
        except (ValueError, OSError, KeyError, TypeError):
            pass
        return False

    def _recovery_valid(self, chunk_id, digest, token):
        if not isinstance(token, str) or len(token) < 32:
            return False
        path = self.voice_log.parent / 'audio-capture-recovery' / (chunk_id + '.json')
        try:
            if path.is_symlink():
                return False
            grant = json.loads(path.read_text())
            return (grant['chunk_id'] == chunk_id and grant['audio_sha256'] == digest and
                    secrets.compare_digest(grant['recovery_token'], token))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def grant_recovery(self, request):
        """A separate, audited authorization for one already archived original.

        The UI wires this endpoint only to an explicit trusted button click.
        Same-origin/session checks are enforced by the handler. This is channel
        provenance, not server-verifiable proof of a human speaker or intent.
        Never modifies admission or the microphone guard.
        """
        with self.lock:
            cid = request.get('chunk_id', '')
            gid = request.get('grant_request_id', '')
            digest = request.get('audio_sha256', '')
            try:
                if str(uuid.UUID(cid)) != cid or str(uuid.UUID(gid)) != gid:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise SaveError('Recovery IDs must be canonical UUIDs.', 400)
            if request.get('explicit_human_action') is not True:
                raise SaveError('Use the explicit Recover saved recording button.', 400)
            if not isinstance(digest, str) or len(digest) != 64:
                raise SaveError('Recovery requires the original audio hash.', 400)
            preserved = None
            for root in self._roots():
                entry = root / cid
                manifest_path = entry / 'manifest.json'
                if entry.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text())
                original = entry / manifest['audio_filename']
                if original.is_symlink():
                    raise SaveError('Original recording is unavailable.')
                archived_audio = original.read_bytes()
                if (manifest['capture']['audio_sha256'] != digest or
                        _sha(archived_audio) != digest):
                    raise SaveError('Recovery hash does not match the original.', 409)
                preserved = manifest
                break
            if preserved is None:
                raise SaveError('Connect the recording archive and retry recovery.', 409)
            if not self._headroom():
                raise SaveError('Recovery needs storage headroom. Original is retained.')
            grants = self.voice_log.parent / 'audio-capture-recovery'
            _private_dir(grants)
            path = grants / (cid + '.json')
            grant = {'schema': 'panel.explicit-recovery/1', 'chunk_id': cid,
                     'audio_sha256': digest, 'grant_request_id': gid,
                     'recovery_token': secrets.token_urlsafe(32),
                     'authorized_at': dt.datetime.now().astimezone().isoformat(),
                     'authorization_channel': 'explicit-panel-recovery-button',
                     'authorization_verification': 'existing origin check; remote passkey gate; UI trusted click (not server-attested human intent)',
                     'capture_admitted_unchanged': preserved.get('capture_admitted') is True}
            # Serialize across workers. Only an explicit recovery call can
            # create/repair a grant; ordinary ASR/retry merely reads it.
            lock_fd = os.open(grants / ('.' + cid + '.lock'), os.O_RDWR | os.O_CREAT |
                              getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if path.is_symlink():
                    raise SaveError('Recovery grant is not a private regular file.')
                prior = None
                if path.exists():
                    try:
                        prior = json.loads(path.read_text())
                    except (ValueError, UnicodeError):
                        pass
                    if isinstance(prior, dict):
                        if (prior.get('chunk_id', cid) != cid or
                                prior.get('audio_sha256', digest) != digest):
                            raise SaveError('This recording already has a different recovery identity.', 409)
                        token = prior.get('recovery_token')
                        if (prior.get('schema') == 'panel.explicit-recovery/1' and
                                prior.get('chunk_id') == cid and prior.get('audio_sha256') == digest and
                                isinstance(token, str) and len(token) >= 32 and
                                isinstance(prior.get('grant_request_id'), str) and
                                isinstance(prior.get('authorized_at'), str)):
                            # A committed token is immutable, including lost-ACK
                            # retries and another explicit request nonce.
                            grant = prior
                        else:
                            prior = None
                    else:
                        prior = None
                    if prior is None:
                        # Preserve interrupted v2/faulted bytes for audit; only
                        # this explicit request can replace the unusable record.
                        interrupted = grants / ('interrupted-' + cid + '-' + uuid.uuid4().hex + '.json')
                        os.rename(path, interrupted)
                        _sync_dir(grants)
                        grant['replaces_interrupted_record'] = interrupted.name
                if path.exists():
                    _resync_file(path)
                    _sync_dir(grants)
                else:
                    temporary = grants / ('.grant-' + cid + '-' + uuid.uuid4().hex)
                    try:
                        _new_file(temporary, _json(grant))
                        os.replace(temporary, path)
                        _sync_dir(grants)
                    finally:
                        # A partial write never becomes the canonical UUID.json.
                        # Remove only this request's unpublished temporary file.
                        if temporary.exists():
                            temporary.unlink()
            finally:
                os.close(lock_fd)
            # Explicit, hash-bound readback repairs an unreadable browser Blob.
            # Never expose this through a public/static media route or silently
            # fetch originals during automatic retries.
            return {'ok': True, 'durable': True, 'chunk_id': cid,
                    'audio_sha256': digest, 'recovery_token': grant['recovery_token'],
                    'audio_base64': base64.b64encode(archived_audio).decode('ascii'),
                    'audio_type': preserved['capture']['audio_type']}

    def _manifest_admission(self, chunk_id):
        found = []
        for root in self._roots():
            entry = root / chunk_id
            path = entry / 'manifest.json'
            if entry.is_symlink() or path.is_symlink():
                raise SaveError('Invalid stored capture path.')
            if not path.is_file():
                continue
            manifest = json.loads(path.read_text())
            original = entry / manifest['audio_filename']
            info = manifest['capture']
            if (info['chunk_id'] != chunk_id or original.is_symlink() or
                    _sha(original.read_bytes()) != info['audio_sha256'] or
                    not isinstance(manifest.get('capture_admitted'), bool)):
                raise SaveError('Original admission evidence failed verification.')
            found.append(manifest['capture_admitted'])
        if found and any(value != found[0] for value in found):
            raise SaveError('Original admission evidence conflicts; nothing was promoted.')
        return found[0] if found else None

    def admission(self, chunk_id):
        """Read immutable admission; an interrupted cache cannot promote it."""
        try:
            if str(uuid.UUID(chunk_id)) != chunk_id:
                return None
            path = self.voice_log.parent / 'audio-capture-admissions' / (chunk_id + '.json')
            if path.is_symlink():
                raise SaveError('Invalid admission record path.')
            corrupt = False
            if path.is_file():
                try:
                    decision = json.loads(path.read_text())
                    if (isinstance(decision, dict) and decision.get('chunk_id') == chunk_id and
                            isinstance(decision.get('admitted'), bool)):
                        return decision['admitted']
                except (ValueError, UnicodeError):
                    pass
                corrupt = True
            # Read-only fallback to original bytes+manifest. No background
            # rewrite, new admission, grant, or Guard.action is performed.
            original = self._manifest_admission(chunk_id)
            if original is not None:
                return original
            if corrupt:
                raise SaveError('Reconnect the original archive to recover its admission record.')
            return None
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise SaveError('Capture admission could not be read. Keep it and retry.') from exc

    def _record_admission(self, entry, info):
        """Commit the existing original decision atomically, never recompute it."""
        admissions = self.voice_log.parent / 'audio-capture-admissions'
        _private_dir(admissions)
        cid = info['chunk_id']
        decision = admissions / (cid + '.json')
        actual = self._manifest_admission(cid)
        if actual is None:
            raise SaveError('Original admission evidence is unavailable.')
        fd = os.open(admissions / ('.' + cid + '.lock'), os.O_RDWR | os.O_CREAT |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if decision.is_symlink():
                raise SaveError('Invalid admission record path.')
            valid = False
            if decision.exists():
                try:
                    prior = json.loads(decision.read_text())
                    valid = (isinstance(prior, dict) and prior.get('chunk_id') == cid and
                             isinstance(prior.get('admitted'), bool))
                except (ValueError, UnicodeError):
                    pass
                if valid:
                    if prior['admitted'] is not actual:
                        raise SaveError('Capture admission conflicts with preserved evidence.', 409, True)
                    evidence = prior.get('capture')
                    if evidence is not None and evidence != info:
                        raise SaveError('Capture admission identity conflicts with the original.', 409, True)
                    if evidence == info:
                        _resync_file(decision)
                        _sync_dir(admissions)
                        return
                    # Valid legacy decision: preserve its bytes, then atomically
                    # add verified identity without changing the decision itself.
                    backup = admissions / ('legacy-binding-' + cid + '-' + uuid.uuid4().hex + '.json')
                    _new_file(backup, decision.read_bytes())
                    _sync_dir(admissions)
                else:
                    interrupted = admissions / ('interrupted-' + cid + '-' + uuid.uuid4().hex + '.json')
                    os.rename(decision, interrupted)
                    _sync_dir(admissions)
            temporary = admissions / ('.admission-' + cid + '-' + uuid.uuid4().hex)
            try:
                _new_file(temporary, _json({'admitted': actual, 'chunk_id': cid, 'capture': info}))
                os.replace(temporary, decision)
                _sync_dir(admissions)
            finally:
                if temporary.exists():
                    temporary.unlink()
        finally:
            os.close(fd)

    def _bind_cached_identity(self, info):
        """Resolve a legacy/incomplete cache BEFORE creating any archive copy."""
        cid = info['chunk_id']
        cached = self.voice_log.parent / 'audio-capture-admissions' / (cid + '.json')
        if cached.is_symlink():
            raise SaveError('Invalid admission record path.')
        if not cached.exists():
            return
        try:
            prior = json.loads(cached.read_text())
            evidence = prior.get('capture') if isinstance(prior, dict) else None
        except (ValueError, UnicodeError):
            evidence = None
        if evidence is not None:
            if evidence != info:
                raise SaveError('Capture ID already belongs to different evidence.', 409)
            return
        verified = None
        for root in self._roots():
            entry = root / cid
            manifest_path = entry / 'manifest.json'
            if entry.is_symlink() or manifest_path.is_symlink():
                raise SaveError('Invalid stored capture path.')
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text())
            original = entry / manifest['audio_filename']
            if (manifest.get('capture') != info or original.is_symlink() or
                    _sha(original.read_bytes()) != info['audio_sha256']):
                raise SaveError('Legacy capture does not match the verified original.', 409)
            verified = entry
        if verified is None:
            raise SaveError('Reconnect the original archive before saving this legacy capture.', 409)
        # Pin the exact original identity atomically before _archive can run.
        # A valid prior decision is preserved; a mismatch is never promoted.
        self._record_admission(verified, info)

    def save(self, request, row, admitted=False):
        """Return an ACK only for this exact immutable capture and transcript."""
        with self.lock:
            try:
                info, audio = self._identity(request, row)
                self._bind_cached_identity(info)
                entry = self._archive(info, audio, row, admitted=admitted)
                self._record_admission(entry, info)
                lock_path = entry / '.lock'
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    return self._finish(entry, info, request, row)
                finally:
                    os.close(fd)
            except SaveError:
                raise
            except (OSError, ValueError, KeyError) as exc:
                raise SaveError('Recording save did not finish durably. Keep it and retry.',
                                audio_durable='entry' in locals()) from exc


def append_legacy(voice_log, row):
    """Keep old clients usable, without claiming their audio was retained."""
    path = Path(voice_log)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('ab') as log:
        fcntl.flock(log.fileno(), fcntl.LOCK_EX)
        try:
            log.write(_json(row) + b'\n')
            log.flush()
            os.fsync(log.fileno())
        finally:
            fcntl.flock(log.fileno(), fcntl.LOCK_UN)
    return {'ok': True, 'durable': False, 'feedback_status': 'durable',
            'audio_durable': False, 'legacy_text_only': True}
