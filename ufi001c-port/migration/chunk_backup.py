"""Complete, resumable EDL reads. No device writes or automatic USB retries.

Only acknowledged, exact-length chunks are checkpointed. A broken read is never
filled with zeros or called a backup. Resume revalidates device identity and ALL
previous local bytes before requesting the next sector. Legacy partial rf dumps
have no verified checkpoints and are not imported.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path

SECTOR = 512
CHUNK = 32 * 1024**2


def save(path, obj):
    tmp = path.with_suffix('.json.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def exclusive(folder, filename='backup.lock'):
    # OS locks are released on process exit, including a crash. No stale PID
    # heuristics, and no two installers may append to one checkpoint file.
    with (folder / filename).open('a+b') as f:
        if f.tell() == 0:
            f.write(b'\0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            try: yield
            finally:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try: yield
            finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_exact(edl, start, length, temp):
    if length <= 0 or length % SECTOR or start < 0:
        raise ValueError('Invalid sector read range')
    temp.unlink(missing_ok=True)
    edl('rs', start, length // SECTOR, temp)
    if not temp.is_file() or temp.stat().st_size != length:
        raise ValueError(f'Incomplete EDL chunk at sector {start}; not checkpointed')
    return temp.read_bytes()


def read_range(edl, start, sectors, destination, chunk_bytes=CHUNK):
    """Bounded readback of a write; no resume/retry of a device mutation."""
    destination = Path(destination)
    temp = destination.with_suffix('.chunk')
    with destination.open('wb') as out:
        done = 0
        while done < sectors * SECTOR:
            size = min(chunk_bytes, sectors * SECTOR - done)
            out.write(read_exact(edl, start + done // SECTOR, size, temp))
            done += size
        out.flush(); os.fsync(out.fileno())
    temp.unlink(missing_ok=True)


def full_backup(edl, folder, profile, identity, chunk_bytes=CHUNK):
    folder = Path(folder)
    if not isinstance(chunk_bytes, int) or not SECTOR <= chunk_bytes <= CHUNK or chunk_bytes % SECTOR:
        raise ValueError('Invalid backup chunk size')
    total = profile['disk_bytes']
    if not isinstance(total, int) or total <= 0 or total % SECTOR:
        raise ValueError('Invalid device size')
    with exclusive(folder):
        state_file = folder / 'backup-state.json'
        partial, final, temp = (folder / n for n in ('emmc.partial', 'emmc.bin', 'read-chunk.tmp'))
        # This must perform fresh device reads; caller also validates both GPTs.
        current_identity = identity()
        if state_file.exists():
            if state_file.stat().st_size > 1024**2:
                raise ValueError('Oversized backup state')
            state = json.loads(state_file.read_text(encoding='utf-8'))
            if (state.get('format') != 1 or state.get('profile') != profile['id'] or
                state.get('disk_bytes') != total or state.get('chunk_bytes') != chunk_bytes or
                state.get('identity') != current_identity):
                raise ValueError('Backup identity/profile mismatch; refusing to mix devices')
        else:
            if partial.exists() or final.exists():
                raise ValueError('Existing dump without checkpoints; start a new backup folder')
            state = {'format':1, 'profile':profile['id'], 'disk_bytes':total,
                     'chunk_bytes':chunk_bytes, 'identity':current_identity, 'chunks':[],
                     'status':'reading', 'writes_performed':False}
            save(state_file, state)
            partial.touch(exist_ok=False)
        if partial.exists() and final.exists():
            raise ValueError('Ambiguous partial and complete backup files')
        path = final if final.exists() else partial
        if not path.exists():
            # Crash after state creation but before the first empty file.
            if state.get('chunks'): raise ValueError('Checkpointed data file is missing')
            partial.touch(exist_ok=False); path = partial
        chunks = state.get('chunks')
        if not isinstance(chunks, list) or len(chunks) > (total+chunk_bytes-1)//chunk_bytes:
            raise ValueError('Invalid backup chunk list')
        verified = 0
        with path.open('rb') as f:
            for item in chunks:
                size = min(chunk_bytes, total-verified)
                if item.get('offset') != verified or item.get('bytes') != size or size <= 0:
                    raise ValueError('Non-contiguous backup checkpoint')
                data = f.read(size)
                if len(data) != size or sha(data) != item.get('sha256'):
                    raise ValueError('Saved backup chunk checksum mismatch')
                verified += size
        if path == final and (verified != total or path.stat().st_size != total):
            raise ValueError('Complete backup length/checkpoints mismatch')
        if path == partial:
            with partial.open('r+b') as out:
                # Discard ONLY an uncheckpointed host-side tail after a crash.
                out.truncate(verified); out.seek(verified)
                while verified < total:
                    size = min(chunk_bytes, total-verified)
                    print(f'Бэкап: {verified//1048576}/{total//1048576} МБ; сектор {verified//SECTOR}', flush=True)
                    state['next_sector'] = verified//SECTOR; save(state_file, state)
                    data = read_exact(edl, verified//SECTOR, size, temp)
                    out.write(data); out.flush(); os.fsync(out.fileno())
                    state['chunks'].append({'offset':verified, 'bytes':size, 'sha256':sha(data)})
                    verified += size; state['next_sector'] = verified//SECTOR
                    save(state_file, state)
            # Re-read identity after the entire stream, before claiming complete.
            if identity() != current_identity:
                raise ValueError('Device identity changed during backup')
            # Compare critical regions inside the completed dump with fresh reads.
            with partial.open('rb') as f:
                for item in current_identity['regions']:
                    f.seek(item['offset'])
                    if sha(f.read(item['bytes'])) != item['sha256']:
                        raise ValueError('Backup identity region differs from live device')
            partial.replace(final)
        state['status'] = 'read-complete'; state['next_sector'] = total//SECTOR
        save(state_file, state); temp.unlink(missing_ok=True)
        return final
