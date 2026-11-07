#!/usr/bin/env python3
"""Install the scoped watcher; never run the bundled executable or use sudo."""
import argparse
import os
from pathlib import Path
import shlex
import stat
import sys
import uuid

from watcher import absolute, bundle, directory, identity, read_regular, replace_file, signature


def ensure_directory(path):
    path = absolute(path)
    with directory('/') as root:
        fd = os.dup(root)
        try:
            for part in Path(path).parts[1:]:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
        finally:
            os.close(fd)


def desktop_quote(value):
    """Desktop Entry string unescaping then Exec argument unescaping."""
    value = absolute(value)
    value = value.replace('%', '%%')
    value = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return '"' + value.replace('\\', '\\\\') + '"'


def install_file(parent, name, data, mode=0o644):
    with directory(parent) as fd:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            if not stat.S_ISREG(st.st_mode):
                raise ValueError('Refusing nonregular installation destination: ' + name)
            previous = read_regular(fd, name)
            backup = name + '.backup-' + uuid.uuid4().hex
            if not replace_file(fd, backup, previous, mode=stat.S_IMODE(st.st_mode) & 0o777):
                raise RuntimeError('Could not back up ' + name)
            print('BACKUP: ' + str(Path(parent) / backup))
        if not replace_file(fd, name, data, expected=signature(st) if st else None, mode=mode):
            raise RuntimeError('Destination changed while installing: ' + name)
        print('INSTALLED: ' + str(Path(parent) / name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', required=True)
    parser.add_argument('--accept-bundled-files', action='store_true',
                        help='acknowledge unverified runtime.conf authenticity and compatibility/account risks')
    parser.add_argument('--applications-dir', default=os.path.expanduser('~/.local/share/applications'))
    args = parser.parse_args()
    try:
        if not args.accept_bundled_files:
            raise ValueError('Explicit --accept-bundled-files required: executable signature was independently '
                             'verified, but runtime.conf authenticity and game compatibility are not established. '
                             'No online-play or account-safety guarantee.')
        game = absolute(args.game_dir)
        source = Path(__file__).absolute().parent
        payload = bundle(source / 'files')
        with directory(game) as fd:
            identity(fd)
        root = os.path.dirname(game)
        install = str(Path(root) / 'b4b-eac-fix')
        applications = absolute(args.applications_dir)
        # Check existing destination files before any mutation.
        targets = [(root, 'b4b-eac-fix.sh'), (applications, 'b4b-eac-fix.desktop'),
                   (install, 'watcher.py')]
        targets += [(str(Path(install) / 'files'), name) for name in payload]
        cert = str(Path(game) / 'EasyAntiCheat' / 'Certificates')
        targets += [(cert, 'runtime.conf')]
        for parent, name in targets:
            try:
                with directory(parent) as fd:
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        raise ValueError('Refusing nonregular destination: ' + str(Path(parent) / name))
            except FileNotFoundError:
                pass
        ensure_directory(install)
        ensure_directory(str(Path(install) / 'files'))
        ensure_directory(applications)
        ensure_directory(cert)
        # Preserve runtime.conf byte-for-byte and do not chmod it.
        with directory(cert) as fd:
            try:
                read_regular(fd, 'runtime.conf')
                print('PRESERVED: ' + str(Path(cert) / 'runtime.conf'))
            except FileNotFoundError:
                if not replace_file(fd, 'runtime.conf', payload['runtime.conf']):
                    raise RuntimeError('runtime.conf changed during install')
                print('INSTALLED: ' + str(Path(cert) / 'runtime.conf'))
        with directory(source) as fd:
            helper = read_regular(fd, 'watcher.py')
        install_file(install, 'watcher.py', helper)
        for name, data in payload.items():
            install_file(str(Path(install) / 'files'), name, data)
        launcher = str(Path(root) / 'b4b-eac-fix.sh')
        command = [sys.executable, str(Path(install) / 'watcher.py'), '--game-dir', game,
                   '--source-dir', str(Path(install) / 'files')]
        install_file(root, 'b4b-eac-fix.sh', ('#!/bin/sh\nexec ' + shlex.join(command) + '\n').encode(), 0o755)
        desktop = ('[Desktop Entry]\nType=Application\nName=B4B EAC Watcher\n'
                   'Comment=Watch only identified Back 4 Blood chains; online operation not verified\n'
                   'Exec=/bin/sh ' + desktop_quote(launcher) + '\nTerminal=true\n'
                   'Icon=applications-games\nCategories=Game;\n')
        install_file(applications, 'b4b-eac-fix.desktop', desktop.encode())
        print('Installation complete. Start the watcher and wait for READY before launching the game.\n'
              'This does not verify online play. Stop any older broad watcher before using this one.')
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
