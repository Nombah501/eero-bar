import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import eero_session as session
import fakes  # noqa: F401  (installs the suite-wide network-forbidden guard)


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sandbox = Path(self._tmp.name).resolve()
        self.state_root = self.sandbox / "state"
        self.state_root.mkdir(mode=0o700)
        self.environ = {"EERO_BAR_STATE_DIR": str(self.state_root / session.STATE_DIR_NAME)}
        self.state_dir = self.state_root / session.STATE_DIR_NAME
        self.session_file = self.state_dir / session.SESSION_FILE_NAME

    def test_missing_session_returns_all_none_without_creating_state(self):
        result = session.load_session(self.environ)
        self.assertEqual(result, {"token": None, "pendingUserToken": None, "networkUrl": None})
        self.assertFalse(self.state_dir.exists())

    def test_save_creates_private_dir_and_file_then_round_trips(self):
        session.save_session({"token": "tok-1", "pendingUserToken": None, "networkUrl": "/2.2/networks/1"}, self.environ)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.session_file.stat().st_mode), 0o600)
        loaded = session.load_session(self.environ)
        self.assertEqual(loaded, {"token": "tok-1", "pendingUserToken": None, "networkUrl": "/2.2/networks/1"})

    def test_missing_keys_default_to_null(self):
        session.save_session({"token": "tok-1"}, self.environ)
        loaded = session.load_session(self.environ)
        self.assertEqual(loaded, {"token": "tok-1", "pendingUserToken": None, "networkUrl": None})

    def test_atomic_write_leaves_no_tmp_file_and_changes_inode(self):
        session.save_session({"token": "first"}, self.environ)
        first_inode = self.session_file.stat().st_ino
        session.save_session({"token": "second"}, self.environ)
        self.assertNotEqual(self.session_file.stat().st_ino, first_inode)
        self.assertEqual(session.load_session(self.environ)["token"], "second")
        self.assertEqual(list(self.state_dir.glob(".session.*.tmp")), [])

    def test_update_session_merges_without_clobbering_other_keys(self):
        session.save_session({"token": "tok-1", "networkUrl": "/2.2/networks/1"}, self.environ)
        session.update_session({"token": "tok-2"}, self.environ)
        loaded = session.load_session(self.environ)
        self.assertEqual(loaded, {"token": "tok-2", "pendingUserToken": None, "networkUrl": "/2.2/networks/1"})

    def test_symlinked_session_directory_is_refused(self):
        real = self.state_root / "real-dir"
        real.mkdir(mode=0o700)
        self.state_dir.symlink_to(real, target_is_directory=True)
        with self.assertRaises(session.SessionError):
            session.load_session(self.environ)
        with self.assertRaises(session.SessionError):
            session.save_session({"token": "x"}, self.environ)

    def test_symlinked_session_file_is_refused_and_target_untouched(self):
        self.state_dir.mkdir(mode=0o700)
        target = self.state_root / "elsewhere.json"
        target.write_bytes(b'{"token":"do-not-touch"}\n')
        self.session_file.symlink_to(target)
        with self.assertRaises(session.SessionError):
            session.load_session(self.environ)
        with self.assertRaises(session.SessionError):
            session.save_session({"token": "new"}, self.environ)
        self.assertEqual(target.read_bytes(), b'{"token":"do-not-touch"}\n')

    def test_foreign_owned_session_file_is_refused(self):
        self.state_dir.mkdir(mode=0o700)
        self.session_file.write_bytes(b'{"token":"tok"}\n')
        real_fstat = os.fstat

        def mismatched_owner(fd):
            result = real_fstat(fd)
            if not stat.S_ISREG(result.st_mode):
                return result
            return os.stat_result(
                (result.st_mode, result.st_ino, result.st_dev, result.st_nlink, result.st_uid + 1, result.st_gid,
                 result.st_size, result.st_atime, result.st_mtime, result.st_ctime)
            )

        from unittest import mock

        with mock.patch.object(session.os, "fstat", side_effect=mismatched_owner):
            with self.assertRaises(session.SessionError):
                session.load_session(self.environ)

    def test_delete_session_is_idempotent_when_absent(self):
        session.delete_session(self.environ)  # no directory at all
        self.state_dir.mkdir(mode=0o700)
        session.delete_session(self.environ)  # directory but no file
        session.save_session({"token": "tok"}, self.environ)
        session.delete_session(self.environ)
        self.assertFalse(self.session_file.exists())
        session.delete_session(self.environ)  # already gone: still fine

    def test_delete_session_removes_symlink_without_following_it(self):
        self.state_dir.mkdir(mode=0o700)
        target = self.state_root / "elsewhere.json"
        target.write_bytes(b"keep-me")
        self.session_file.symlink_to(target)
        session.delete_session(self.environ)
        self.assertFalse(self.session_file.exists())
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"keep-me")

    def test_non_string_field_is_rejected(self):
        with self.assertRaises(session.SessionError):
            session.save_session({"token": 12345}, self.environ)

    def test_directory_wrong_mode_is_repaired_not_refused(self):
        self.state_dir.mkdir(mode=0o755)
        session.save_session({"token": "tok"}, self.environ)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)

    def test_delete_session_refuses_symlinked_state_directory(self):
        # nit: a symlinked state dir must be refused, not traversed --
        # os.remove() would otherwise delete a file inside a directory an
        # attacker controls.
        real = self.state_root / "real-dir"
        real.mkdir(mode=0o700)
        self.state_dir.symlink_to(real, target_is_directory=True)
        with self.assertRaises(session.SessionError) as ctx:
            session.delete_session(self.environ)
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_load_raises_state_on_raw_oserror_from_lstat(self):
        # MINOR-4: any raw OSError from a session-store operation (not just
        # FileNotFoundError) must surface as SessionError -> protocol "state",
        # not propagate as an uncaught OSError.
        from unittest import mock

        self.state_dir.mkdir(mode=0o700)
        with mock.patch.object(session.os, "lstat", side_effect=PermissionError("denied")):
            with self.assertRaises(session.SessionError):
                session.load_session(self.environ)

    def test_save_raises_state_on_raw_oserror_from_lstat(self):
        from unittest import mock

        self.state_dir.mkdir(mode=0o700)
        real_lstat = os.lstat

        def flaky_lstat(path, *args, **kwargs):
            if str(path) == str(self.session_file):
                raise PermissionError("denied")
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(session.os, "lstat", side_effect=flaky_lstat):
            with self.assertRaises(session.SessionError):
                session.save_session({"token": "tok"}, self.environ)

    def test_save_raises_state_on_mkstemp_failure(self):
        from unittest import mock

        with mock.patch.object(session.tempfile, "mkstemp", side_effect=OSError("disk full")):
            with self.assertRaises(session.SessionError):
                session.save_session({"token": "tok"}, self.environ)

    def test_delete_raises_state_on_raw_oserror_from_remove(self):
        from unittest import mock

        self.state_dir.mkdir(mode=0o700)
        session.save_session({"token": "tok"}, self.environ)
        with mock.patch.object(session.os, "remove", side_effect=PermissionError("denied")):
            with self.assertRaises(session.SessionError):
                session.delete_session(self.environ)

    def test_load_raises_state_on_raw_oserror_from_read(self):
        from unittest import mock

        session.save_session({"token": "tok"}, self.environ)
        with mock.patch.object(session.os, "read", side_effect=OSError("EIO")):
            with self.assertRaises(session.SessionError):
                session.load_session(self.environ)


if __name__ == "__main__":
    unittest.main()
