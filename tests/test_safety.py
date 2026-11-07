"""Linux integration tests; fixture executables are data, never executed."""
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import watcher
import installer

OLD = b'GDK fixture: not an executable\n'


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b4b-tests-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'tool source'
        self.repo.mkdir()
        # The only substitution is the accepted GDK hash, allowing synthetic old
        # data without requiring an installed game. Bundle pins remain real.
        code = (ROOT / 'watcher.py').read_text().replace(watcher.GDK_HASH, hashlib.sha256(OLD).hexdigest())
        (self.repo / 'watcher.py').write_text(code)
        for name in ('installer.py', 'install.sh'):
            shutil.copyfile(ROOT / name, self.repo / name)
        shutil.copytree(ROOT / 'files', self.repo / 'files')
        self.payload = (ROOT / 'files' / watcher.EXE).read_bytes()
        self.games = self.base / 'Games with spaces'
        self.games.mkdir()
        self.game = self.make_game(self.games / 'Back-4-Blood')
        self.apps = self.base / 'menu entries'

    def make_game(self, path, ids=None, target=True):
        (path / 'EasyAntiCheat').mkdir(parents=True)
        (path / 'EasyAntiCheat/Settings.json').write_text(json.dumps(ids or watcher.IDS))
        (path / 'Gobi/Binaries/WinGDK').mkdir(parents=True)
        (path / 'Gobi/Binaries/WinGDK/Back4Blood.exe').write_bytes(b'game marker')
        if target:
            (path / 'EasyAntiCheat' / watcher.EXE).write_bytes(OLD)
        return path

    def exe(self, path):
        return path / 'EasyAntiCheat' / watcher.EXE

    def start(self, command=None):
        command = command or [sys.executable, str(self.repo / 'watcher.py'), '--game-dir', str(self.game)]
        log = tempfile.TemporaryFile(mode='w+b')
        proc = subprocess.Popen(command, stdout=log, stderr=log)
        def cleanup():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            log.close()
        self.addCleanup(cleanup)
        for _ in range(100):
            log.seek(0)
            output = log.read().decode()
            if 'READY:' in output:
                return proc, log
            if proc.poll() is not None:
                self.fail('watcher failed before READY: ' + output)
            time.sleep(0.05)
        self.fail('watcher never became ready')

    def wait_replaced(self, path):
        for _ in range(100):
            if self.exe(path).read_bytes() == self.payload:
                return
            time.sleep(0.05)
        self.fail('target was not replaced: ' + str(path))

    def install(self, *args, accept=True):
        command = ['sh', str(self.repo / 'install.sh'), '--game-dir', str(self.game),
                   '--applications-dir', str(self.apps)]
        if accept:
            command.append('--accept-bundled-files')
        return subprocess.run(command + list(args), capture_output=True, text=True)

    def test_assembly_messages_are_quiet_and_replacement_still_works(self):
        _, log = self.start()
        (self.games / '.xodus-chain-building.lock').write_bytes(b'')
        chain = self.games / '.xodus-chain-building'
        (chain / 'EasyAntiCheat').mkdir(parents=True)
        time.sleep(0.2)
        (chain / 'EasyAntiCheat/Settings.json').write_text(json.dumps(watcher.IDS))
        time.sleep(0.2)
        (chain / 'Gobi/Binaries/WinGDK').mkdir(parents=True)
        (chain / 'Gobi/Binaries/WinGDK/Back4Blood.exe').write_bytes(b'marker')
        time.sleep(0.2)
        self.exe(chain).write_bytes(OLD)
        self.wait_replaced(chain)
        time.sleep(0.1)
        log.seek(0)
        output = log.read().decode()
        self.assertIn('REPLACED', output)
        self.assertNotIn('SKIP', output)
        self.assertNotIn('WAIT', output)

    def test_incomplete_chain_warns_after_grace_once(self):
        import contextlib
        import io
        chain = self.games / '.xodus-chain-incomplete'
        chain.mkdir()
        w = watcher.Watcher(str(self.game), str(ROOT / 'files'))
        self.addCleanup(w.close)
        output = io.StringIO()
        with watcher.directory(self.games) as fd, contextlib.redirect_stderr(output):
            with mock.patch.object(watcher.time, 'monotonic', return_value=100):
                w.scan(fd)
            self.assertEqual(output.getvalue(), '')
            with mock.patch.object(watcher.time, 'monotonic', return_value=111):
                w.scan(fd)
                w.scan(fd)
        self.assertEqual(output.getvalue().count('WAIT'), 1)
        self.assertIn('incomplete', output.getvalue())

    def test_real_validation_warning_is_not_suppressed(self):
        import contextlib
        import io
        self.make_game(self.games / '.xodus-chain-invalid', dict(watcher.IDS, productid='wrong'))
        w = watcher.Watcher(str(self.game), str(ROOT / 'files'))
        self.addCleanup(w.close)
        output = io.StringIO()
        with watcher.directory(self.games) as fd, contextlib.redirect_stderr(output):
            w.scan(fd)
        self.assertIn('SKIP', output.getvalue())
        self.assertIn('IDs do not match', output.getvalue())

    def test_shipped_bundle_hashes(self):
        self.assertEqual(watcher.bundle(ROOT / 'files')[watcher.EXE], self.payload)

    def test_close_write_waits_for_writer(self):
        chain = self.make_game(self.games / '.xodus-chain-slow', target=False)
        self.start()
        with self.exe(chain).open('wb') as stream:
            stream.write(OLD)
            stream.flush()
            time.sleep(0.8)
            self.assertEqual(self.exe(chain).read_bytes(), OLD)
        self.wait_replaced(chain)
        self.assertEqual(self.exe(self.game).read_bytes(), OLD)

    def test_modify_existing_waits_for_close_write(self):
        chain = self.make_game(self.games / '.xodus-chain-modify')
        self.start()
        self.wait_replaced(chain)
        with self.exe(chain).open('wb') as stream:
            stream.write(OLD)
            stream.flush()
            time.sleep(0.8)
            self.assertEqual(self.exe(chain).read_bytes(), OLD)
        self.wait_replaced(chain)

    def test_parent_rename_stops_watcher(self):
        proc, log = self.start()
        self.games.rename(self.base / 'renamed games')
        self.assertNotEqual(proc.wait(timeout=5), 0)
        log.seek(0)
        self.assertIn(b'ERROR:', log.read())

    def test_moved_to_file_and_repeated_replacement(self):
        chain = self.make_game(self.games / '.xodus-chain-move', target=False)
        self.start()
        for _ in range(2):
            temp = chain / 'EasyAntiCheat/incoming'
            temp.write_bytes(OLD)
            temp.replace(self.exe(chain))
            self.wait_replaced(chain)

    def test_new_and_moved_directory_rescan(self):
        self.start()
        chain = self.make_game(self.games / '.xodus-chain-new')
        self.wait_replaced(chain)
        elsewhere = self.make_game(self.base / 'assembled')
        moved = self.games / '.xodus-chain-moved'
        elsewhere.rename(moved)
        self.wait_replaced(moved)

    def test_existing_chain_and_hardlink_original(self):
        chain = self.make_game(self.games / '.xodus-chain-hardlink')
        self.exe(chain).unlink()
        os.link(self.exe(self.game), self.exe(chain))
        before = self.exe(self.game).stat().st_ino
        self.start()
        self.wait_replaced(chain)
        self.assertEqual(self.exe(self.game).read_bytes(), OLD)
        self.assertEqual(self.exe(self.game).stat().st_ino, before)
        self.assertNotEqual(self.exe(chain).stat().st_ino, before)

    def test_each_wrong_id_unrelated_nested_unknown_untouched(self):
        untouched = [self.game, self.make_game(self.games / 'other-game')]
        untouched.append(self.make_game(self.games / 'nested' / '.xodus-chain-nested'))
        for key in watcher.IDS:
            ids = dict(watcher.IDS, **{key: 'wrong'})
            untouched.append(self.make_game(self.games / ('.xodus-chain-' + key), ids))
        unknown = self.make_game(self.games / '.xodus-chain-unknown')
        self.exe(unknown).write_bytes(b'unknown version')
        missing = self.make_game(self.games / '.xodus-chain-no-marker')
        (missing / 'Gobi/Binaries/WinGDK/Back4Blood.exe').unlink()
        untouched.append(missing)
        self.start()
        time.sleep(0.9)
        for path in untouched:
            self.assertEqual(self.exe(path).read_bytes(), OLD)
        self.assertEqual(self.exe(unknown).read_bytes(), b'unknown version')

    def test_symlink_destinations_parents_settings_marker_chain(self):
        outside = self.make_game(self.base / 'outside')
        for component in ('EasyAntiCheat', 'Gobi', 'EasyAntiCheat/Settings.json',
                          'Gobi/Binaries/WinGDK/Back4Blood.exe', 'EasyAntiCheat/' + watcher.EXE):
            chain = self.make_game(self.games / ('.xodus-chain-link-' + str(len(list(self.games.iterdir())))))
            dest = chain / component
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
            dest.symlink_to(outside / component)
        (self.games / '.xodus-chain-linked').symlink_to(outside, target_is_directory=True)
        self.start()
        time.sleep(0.8)
        self.assertEqual(self.exe(outside).read_bytes(), OLD)
        for chain in self.games.glob('.xodus-chain-*'):
            self.assertEqual(self.exe(chain).read_bytes(), OLD)

    def test_xodus_settings_links_accepted_original_unchanged(self):
        settings = self.game / 'EasyAntiCheat/Settings.json'
        before = {p.relative_to(self.game): (p.read_bytes(), p.stat().st_ino)
                  for p in self.game.rglob('*') if p.is_file()}
        chains = []
        for kind in ('absolute', 'relative'):
            chain = self.make_game(self.games / ('.xodus-chain-' + kind))
            link = chain / 'EasyAntiCheat/Settings.json'
            link.unlink()
            target = str(settings) if kind == 'absolute' else os.path.relpath(settings, link.parent)
            link.symlink_to(target)
            chains.append((chain, target))
        self.start()
        for chain, target in chains:
            self.wait_replaced(chain)
            self.assertEqual(os.readlink(chain / 'EasyAntiCheat/Settings.json'), target)
        after = {p.relative_to(self.game): (p.read_bytes(), p.stat().st_ino)
                 for p in self.game.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_settings_foreign_broken_traversal_links_refused(self):
        foreign = self.make_game(self.games / 'foreign-same-ids')
        settings = self.game / 'EasyAntiCheat/Settings.json'
        targets = [str(foreign / 'EasyAntiCheat/Settings.json'),
                   str(self.games / 'missing/Settings.json'),
                   str(self.game / 'EasyAntiCheat') + '/../EasyAntiCheat/Settings.json',
                   '../../../outside/Settings.json',
                   '../../Back-4-Blood/EasyAntiCheat/../EasyAntiCheat/Settings.json']
        chains = []
        for i, target in enumerate(targets):
            chain = self.make_game(self.games / ('.xodus-chain-bad-settings-' + str(i)))
            link = chain / 'EasyAntiCheat/Settings.json'
            link.unlink()
            link.symlink_to(target)
            chains.append(chain)
        self.start()
        time.sleep(0.9)
        for chain in chains:
            self.assertEqual(self.exe(chain).read_bytes(), OLD)
        self.assertEqual(self.exe(foreign).read_bytes(), OLD)

    def test_original_settings_symlink_remains_forbidden(self):
        settings = self.game / 'EasyAntiCheat/Settings.json'
        copied = self.base / 'copied-settings.json'
        copied.write_bytes(settings.read_bytes())
        settings.unlink()
        settings.symlink_to(copied)
        with watcher.directory(self.game) as fd:
            with self.assertRaises(OSError):
                watcher.identity(fd)
        result = subprocess.run([sys.executable, str(self.repo / 'watcher.py'), '--game-dir', str(self.game)],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('READY:', result.stdout)
        self.assertNotEqual(self.install().returncode, 0)

    def test_chain_settings_target_open_is_nofollow(self):
        chain = self.make_game(self.games / '.xodus-chain-nofollow')
        link = chain / 'EasyAntiCheat/Settings.json'
        settings = self.game / 'EasyAntiCheat/Settings.json'
        link.unlink()
        link.symlink_to(settings)
        saved = self.game / 'saved-eac'
        (self.game / 'EasyAntiCheat').rename(saved)
        (self.game / 'EasyAntiCheat').symlink_to(saved, target_is_directory=True)
        with watcher.directory(chain) as fd:
            with self.assertRaises(OSError):
                watcher.identity(fd, game=str(self.game), chain_path=str(chain))
        (self.game / 'EasyAntiCheat').unlink()
        saved.rename(self.game / 'EasyAntiCheat')
        copied = self.base / 'settings-copy'
        settings.rename(copied)
        settings.symlink_to(copied)
        with watcher.directory(chain) as fd:
            with self.assertRaises(OSError):
                watcher.identity(fd, game=str(self.game), chain_path=str(chain))
        settings.unlink()
        with watcher.directory(chain) as fd:
            with self.assertRaises(FileNotFoundError):
                watcher.identity(fd, game=str(self.game), chain_path=str(chain))

    def test_chain_settings_link_rechecked_after_read(self):
        chain = self.make_game(self.games / '.xodus-chain-race')
        link = chain / 'EasyAntiCheat/Settings.json'
        settings = self.game / 'EasyAntiCheat/Settings.json'
        link.unlink()
        link.symlink_to(settings)
        foreign = self.make_game(self.games / 'foreign') / 'EasyAntiCheat/Settings.json'
        real_read = watcher.read_regular
        def swap_after_read(fd, name, limit):
            data = real_read(fd, name, limit)
            link.unlink()
            link.symlink_to(foreign)
            return data
        with watcher.directory(chain) as fd:
            with mock.patch.object(watcher, 'read_regular', side_effect=swap_after_read):
                with self.assertRaisesRegex(ValueError, 'changed during validation'):
                    watcher.identity(fd, game=str(self.game), chain_path=str(chain))

    def test_fifo_destination_not_opened(self):
        chain = self.make_game(self.games / '.xodus-chain-fifo')
        self.exe(chain).unlink()
        os.mkfifo(self.exe(chain))
        proc, _ = self.start()
        time.sleep(0.5)
        self.assertIsNone(proc.poll())

    def test_missing_and_tampered_source_refuse_startup(self):
        for action in ('missing', 'tampered'):
            target = self.repo / 'files' / watcher.EXE
            if target.exists():
                target.unlink()
            if action == 'tampered':
                target.write_bytes(b'tampered')
            result = subprocess.run([sys.executable, str(self.repo / 'watcher.py'), '--game-dir', str(self.game)],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('READY:', result.stdout)
            self.assertIn('ERROR:', result.stderr)

    def test_symlink_game_path_and_traversal_refused(self):
        link = self.base / 'linked-games'
        link.symlink_to(self.games)
        for path in (link / self.game.name, str(self.games) + '/../' + self.games.name + '/' + self.game.name):
            result = subprocess.run([sys.executable, str(self.repo / 'watcher.py'), '--game-dir', str(path)],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)

    def test_install_acknowledgment_and_missing_source_no_writes(self):
        self.assertNotEqual(self.install(accept=False).returncode, 0)
        self.assertFalse(self.apps.exists())
        (self.repo / 'files/runtime.conf').unlink()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse((self.games / 'b4b-eac-fix').exists())

    def test_install_reinstall_preserve_config_backup_files_launcher(self):
        cert = self.game / 'EasyAntiCheat/Certificates'
        cert.mkdir()
        conf = cert / 'runtime.conf'
        conf.write_bytes(b'user config\x00do not overwrite')
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        installed = self.games / 'b4b-eac-fix'
        helper = installed / 'watcher.py'
        helper.write_text('# user modified helper\n')
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(conf.read_bytes(), b'user config\x00do not overwrite')
        backups = list(installed.glob('watcher.py.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '# user modified helper\n')
        self.assertEqual(self.exe(self.game).read_bytes(), OLD)
        self.start(['/bin/sh', str(self.games / 'b4b-eac-fix.sh')])
        desktop = self.apps / 'b4b-eac-fix.desktop'
        if shutil.which('desktop-file-validate'):
            validated = subprocess.run(['desktop-file-validate', str(desktop)], capture_output=True, text=True)
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_install_missing_certificates_and_symlink_refusal(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.game / 'EasyAntiCheat/Certificates/runtime.conf').read_bytes(),
                         (ROOT / 'files/runtime.conf').read_bytes())
        launcher = self.games / 'b4b-eac-fix.sh'
        launcher.unlink()
        outside = self.base / 'outside-file'
        outside.write_bytes(b'untouched')
        launcher.symlink_to(outside)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_hostile_path_launcher_and_desktop_quoting(self):
        hostile = self.base / 'spaces "quotes" \'single\' $dollar `tick` %f \\backslash'
        hostile.mkdir()
        self.game = self.make_game(hostile / 'Back-4-Blood')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = hostile / 'b4b-eac-fix.sh'
        self.start(['/bin/sh', str(launcher)])
        desktop = (self.apps / 'b4b-eac-fix.desktop').read_text()
        line = next(s[5:] for s in desktop.splitlines() if s.startswith('Exec='))
        # Decode Desktop Entry string escapes, then quoted Exec args (not a shell).
        line = line.replace('\\\\', '\\')
        arg = line[len('/bin/sh "'):-1]
        decoded, i = '', 0
        while i < len(arg):
            if arg[i] == '\\':
                i += 1
            decoded += arg[i]
            i += 1
        self.assertEqual(decoded.replace('%%', '%'), str(launcher))
        if shutil.which('desktop-file-validate'):
            validated = subprocess.run(['desktop-file-validate', str(self.apps / 'b4b-eac-fix.desktop')],
                                       capture_output=True, text=True)
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)


if __name__ == '__main__':
    unittest.main()
