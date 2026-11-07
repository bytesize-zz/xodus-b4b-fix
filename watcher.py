#!/usr/bin/env python3
"""Scoped Linux inotify watcher. Never executes the bundled program."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import struct
import sys
import time
import uuid
from contextlib import contextmanager

EXE = 'EasyAntiCheat_EOS_Setup.exe'
# Observed installed GDK bootstrapper; unknown revisions fail closed.
GDK_HASH = '2cf5f353a0bf669e7e66c93cd8e346c7b32dbc55f1c6961ce91caadfe0d9ed67'
IDS = {'productid': '3bc138d96fb24d9597d4bbff23b9ecee',
       'sandboxid': '57dfd95548214a138218e56cd9e5b9d8',
       'deploymentid': 'e052f3c4fce741bab494ed0304eeaf62'}
PINS = {EXE: '94567bf0dfcf48bfd6a2fa073e854ff6c8fb38ea114d5913dbe2c1d2554bcaae',
        'runtime.conf': '06f8fc9302d4636d30db3866fdccafac79bd646a51e703200caf8a325bcbf5e9'}
DIRFLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILEFLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def absolute(path):
    # Reject traversal rather than silently normalizing it.
    path = os.fspath(path)
    if '..' in Path(path).parts or any(ord(c) < 32 for c in path):
        raise ValueError('Traversal/control characters are not permitted in paths')
    return os.path.abspath(path)


@contextmanager
def directory(path, base=None):
    """Open every component with NOFOLLOW, never realpath through symlinks."""
    path = os.fspath(path)
    if '..' in Path(path).parts:
        raise ValueError('Parent traversal refused')
    if os.path.isabs(path):
        fd = os.open('/', DIRFLAGS)
    else:
        if base is None:
            raise ValueError('Relative directory requires a base descriptor')
        fd = os.dup(base)
    try:
        for part in Path(path).parts:
            if part in ('/', '.'):
                continue
            child = os.open(part, DIRFLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_regular(fd, name, limit=16 * 1024 * 1024):
    f = os.open(name, FILEFLAGS, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(f).st_mode):
            raise ValueError('Not a regular file: ' + name)
        with os.fdopen(os.dup(f), 'rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('File too large: ' + name)
        return data
    finally:
        os.close(f)


def bundle(source):
    with directory(absolute(source)) as fd:
        result = {name: read_regular(fd, name) for name in PINS}
    for name, data in result.items():
        if hashlib.sha256(data).hexdigest() != PINS[name]:
            raise ValueError('Bundle hash mismatch: ' + name)
    return result


def chain_settings(eac, game, chain_path):
    """Read the one permitted launch-view link without ever following it."""
    name = 'Settings.json'
    before = os.stat(name, dir_fd=eac, follow_symlinks=False)
    if not stat.S_ISLNK(before.st_mode):
        return read_regular(eac, name, 65536)
    expected = os.path.join(game, 'EasyAntiCheat', name)
    # Accept the exact absolute spelling or canonical relative spelling only.
    # The latter needs leading '..' components to reach the sibling game, but
    # arbitrary traversal/detours and foreign links (even with equal IDs) fail.
    allowed = (expected, os.path.relpath(expected, os.path.join(chain_path, 'EasyAntiCheat')))
    target = os.readlink(name, dir_fd=eac)
    if target not in allowed:
        raise ValueError('Settings link does not name the selected game settings')
    # Open the trusted selected path component-by-component with NOFOLLOW;
    # never open the link itself, nor use realpath to bless an arbitrary link.
    with directory(game) as original:
        with directory('EasyAntiCheat', original) as original_eac:
            data = read_regular(original_eac, name, 65536)
    after_target = os.readlink(name, dir_fd=eac)
    after = os.stat(name, dir_fd=eac, follow_symlinks=False)
    if (not stat.S_ISLNK(after.st_mode) or signature(before) != signature(after)
            or after_target != target):
        raise ValueError('Settings link changed during validation')
    return data


def identity(fd, *, game=None, chain_path=None):
    # The original installation and installer always use the strict default.
    with directory('EasyAntiCheat', fd) as eac:
        data = (read_regular(eac, 'Settings.json', 65536) if game is None
                else chain_settings(eac, game, chain_path))
        settings = json.loads(data)
    if not isinstance(settings, dict) or any(settings.get(k) != v for k, v in IDS.items()):
        raise ValueError('Back 4 Blood settings IDs do not match')
    with directory('Gobi/Binaries/WinGDK', fd) as marker:
        st = os.stat('Back4Blood.exe', dir_fd=marker, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('Missing regular Back4Blood.exe marker')


def signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def replace_file(fd, name, data, expected=None, mode=0o644):
    """Atomic replacement breaks hardlinks; never writes an existing inode."""
    try:
        old = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        old = None
    if old is not None and not stat.S_ISREG(old.st_mode):
        raise ValueError('Refusing nonregular destination: ' + name)
    if expected is not None and (old is None or signature(old) != expected):
        return False
    temp = '.b4b-fix-' + uuid.uuid4().hex
    f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    try:
        with os.fdopen(f, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck just before rename; rename itself does not follow symlinks.
        try:
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if ((old is None) != (current is None) or
                (old is not None and signature(old) != signature(current))):
            return False
        if old is None:
            # No clobber if another process creates a file after our check.
            os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temp, dir_fd=fd)
        else:
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        return True
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


class Watcher:
    # No recursive parent watch: only root and fixed paths within chain children.
    PATHS = ('', 'EasyAntiCheat', 'Gobi', 'Gobi/Binaries', 'Gobi/Binaries/WinGDK')
    CLOSE_WRITE, MOVED_TO, CREATE, ISDIR = 8, 128, 256, 0x40000000
    # Follow only our /proc/self/fd handle, not untrusted filesystem symlinks.
    MASK = 2 | 8 | 128 | 256 | 512 | 64 | 1024 | 2048

    def __init__(self, game, source, settle=0.3):
        self.game = absolute(game)
        self.root = os.path.dirname(self.game)
        self.data = bundle(source)[EXE]  # Fail before readiness, including missing source.
        with directory(self.game) as fd:
            identity(fd)
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        self.watches = {}
        self.pending = {}
        self.blocked = set()
        self.notices = set()
        self.incomplete = {}
        self.settle = settle

    def close(self):
        os.close(self.fd)

    def notice(self, name, error):
        message = f'SKIP {name!r}: {error}'
        if message not in self.notices:
            print(message, file=sys.stderr, flush=True)
            self.notices.add(message)

    def add_watch(self, fd, label, seen):
        # proc fd refers to our securely opened directory, not a mutable pathname.
        wd = self.libc.inotify_add_watch(self.fd, f'/proc/self/fd/{fd}'.encode(), self.MASK)
        if wd < 0:
            raise RuntimeError(f'inotify_add_watch failed: errno {ctypes.get_errno()}')
        self.watches[wd] = label
        seen.add(wd)

    def scan(self, rootfd):
        # Do not continue writing through a detached/replaced games parent.
        with directory(self.root) as current_root:
            a, b = os.fstat(rootfd), os.fstat(current_root)
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                raise RuntimeError('Games parent changed; restart the watcher')
        seen = set()
        self.add_watch(rootfd, ('', ''), seen)
        candidates = set()
        for name in os.listdir(rootfd):
            if not name.startswith('.xodus-chain-') or len(name) == len('.xodus-chain-'):
                continue
            # Never modify the explicitly selected original game, whatever its name.
            if name == os.path.basename(self.game):
                continue
            try:
                # Xodus keeps a regular companion lock file beside each chain.
                entry = os.stat(name, dir_fd=rootfd, follow_symlinks=False)
                if name.endswith('.lock') and stat.S_ISREG(entry.st_mode):
                    continue
                with directory(name, rootfd) as chain:
                    for sub in self.PATHS:
                        try:
                            with directory(sub, chain) as fd:
                                self.add_watch(fd, (name, sub), seen)
                        except FileNotFoundError:
                            pass
                    identity(chain, game=self.game, chain_path=os.path.join(self.root, name))
                    with directory('EasyAntiCheat', chain) as eac:
                        st = os.stat(EXE, dir_fd=eac, follow_symlinks=False)
                        if not stat.S_ISREG(st.st_mode):
                            raise ValueError('EAC destination is not a regular file')
                        self.incomplete.pop(name, None)
                        sig = signature(st)
                        candidates.add(name)
                        if name in self.blocked:
                            self.pending.pop(name, None)
                            continue
                        previous = self.pending.get(name)
                        if previous is None or previous[0] != sig:
                            self.pending[name] = (sig, time.monotonic())
                            continue
                        if time.monotonic() - previous[1] < self.settle:
                            continue
                        existing = read_regular(eac, EXE)
                        if existing == self.data:
                            continue
                        if hashlib.sha256(existing).hexdigest() != GDK_HASH:
                            raise ValueError('Unknown bootstrapper hash; refusing replacement')
                        # Revalidate identity and location immediately before mutation.
                        identity(chain, game=self.game, chain_path=os.path.join(self.root, name))
                        live = os.stat(name, dir_fd=rootfd, follow_symlinks=False)
                        if (live.st_dev, live.st_ino) != (os.fstat(chain).st_dev, os.fstat(chain).st_ino):
                            continue
                        with directory('EasyAntiCheat', chain) as current:
                            if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(eac).st_dev, os.fstat(eac).st_ino):
                                continue
                            if replace_file(eac, EXE, self.data, sig, stat.S_IMODE(st.st_mode) & 0o777):
                                print(f'REPLACED {name!r}/EasyAntiCheat/{EXE}; online operation not verified', flush=True)
                                self.pending.pop(name, None)
            except FileNotFoundError:
                # Missing files are normal during chain assembly/teardown.
                # Keep a delayed diagnostic so a persistently incomplete chain
                # is not mistaken for successful replacement.
                started, warned = self.incomplete.get(name, (time.monotonic(), False))
                if not warned and time.monotonic() - started >= 10:
                    print(f'WAIT {name!r}: chain still incomplete after 10 seconds; '
                          'waiting for required files', file=sys.stderr, flush=True)
                    warned = True
                self.incomplete[name] = (started, warned)
            except (OSError, ValueError) as exc:
                self.incomplete.pop(name, None)
                self.notice(name, exc)
        self.pending = {k: v for k, v in self.pending.items() if k in candidates}
        active_chains = {self.watches[wd][0] for wd in seen if self.watches[wd][0]}
        self.incomplete = {k: v for k, v in self.incomplete.items() if k in active_chains}
        self.blocked.intersection_update(active_chains)
        for wd in set(self.watches) - seen:
            self.libc.inotify_rm_watch(self.fd, wd)
            del self.watches[wd]

    def events(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            offset = 0
            while offset < len(data):
                wd, mask, cookie, size = struct.unpack_from('iIII', data, offset)
                name = os.fsdecode(data[offset + 16:offset + 16 + size].split(b'\0', 1)[0])
                offset += 16 + size
                if mask & 0x4000:
                    raise RuntimeError('inotify queue overflow; stop and restart before launching')
                label = self.watches.get(wd)
                if label == ('', '') and name.startswith('.xodus-chain-') and mask & (64 | 128 | 256 | 512):
                    self.blocked.discard(name)
                    self.pending.pop(name, None)
                if label and label[1] == 'EasyAntiCheat' and name == EXE:
                    chain = label[0]
                    if mask & (self.CREATE | 2):
                        self.blocked.add(chain)
                        self.pending.pop(chain, None)
                    if mask & (self.CLOSE_WRITE | self.MOVED_TO):
                        self.blocked.discard(chain)
                        self.pending.pop(chain, None)
                if mask & 0x8000:
                    self.watches.pop(wd, None)

    def run(self):
        with directory(self.root) as rootfd:
            self.scan(rootfd)
            print('READY: scoped inotify watches active; launch Back 4 Blood now', flush=True)
            while True:
                select.select([self.fd], [], [], 0.1)
                self.events()
                # Rescan closes the watch-registration gap for newly assembled trees.
                # Existing files must remain unchanged for the settle interval.
                self.scan(rootfd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', required=True)
    parser.add_argument('--source-dir', default=str(Path(__file__).parent / 'files'))
    args = parser.parse_args()
    watcher = None
    try:
        watcher = Watcher(args.game_dir, args.source_dir)
        watcher.run()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    finally:
        if watcher is not None:
            watcher.close()


if __name__ == '__main__':
    sys.exit(main())
