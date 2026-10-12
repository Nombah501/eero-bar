"""Session store for bin/eero-helper.

Location: `$XDG_STATE_HOME/n501.eero-bar/session.json`, falling back to
`~/.local/state/n501.eero-bar/session.json` when `XDG_STATE_HOME` is unset.
Tests may redirect the whole state directory with `EERO_BAR_STATE_DIR`.

Safety: the directory is created 0700 and the file 0600; writes are atomic
(tempfile in the same directory, then `os.replace` + `fsync`). A symlinked
directory or file, or one not owned by the current user, is refused rather
than followed -- see docs/helper-protocol.md error code "state".
"""

import errno
import json
import os
import stat
import tempfile


STATE_DIR_NAME = "n501.eero-bar"
SESSION_FILE_NAME = "session.json"
SESSION_KEYS = ("token", "pendingUserToken", "networkUrl")
MAX_SESSION_BYTES = 65536


class SessionError(Exception):
    """The session store is unsafe or unusable.

    Every SessionError maps to protocol error code "state" (see eero_cli.py).
    """


def _state_dir(environ):
    override = environ.get("EERO_BAR_STATE_DIR")
    if override:
        return os.path.normpath(override)
    xdg_state_home = environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        base = xdg_state_home
    else:
        home = environ.get("HOME")
        if not home:
            raise SessionError("Could not determine the user's home directory.")
        base = os.path.join(home, ".local", "state")
    return os.path.join(base, STATE_DIR_NAME)


def _session_path(environ):
    return os.path.join(_state_dir(environ), SESSION_FILE_NAME)


def _refuse_unsafe(path_stat, label):
    if stat.S_ISLNK(path_stat.st_mode):
        raise SessionError(f"Refusing a symlinked {label}.")
    if path_stat.st_uid != os.getuid():
        raise SessionError(f"Refusing a {label} not owned by the current user.")


def _ensure_state_dir(directory):
    try:
        directory_stat = os.lstat(directory)
    except FileNotFoundError:
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        except OSError as error:
            raise SessionError(f"Could not create the session directory: {error}") from error
        try:
            directory_stat = os.lstat(directory)
        except OSError as error:
            raise SessionError(f"Could not inspect the session directory: {error}") from error
    except OSError as error:
        raise SessionError(f"Could not inspect the session directory: {error}") from error

    _refuse_unsafe(directory_stat, "session directory")
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise SessionError("The session directory path is not a directory.")
    if stat.S_IMODE(directory_stat.st_mode) != 0o700:
        try:
            os.chmod(directory, 0o700)
        except OSError as error:
            raise SessionError(f"Could not set session directory permissions: {error}") from error


def _empty_session():
    return {key: None for key in SESSION_KEYS}


def _reject_json_constant(name):
    raise ValueError(f"session file contained a non-finite number ({name}).")


def load_session(environ=None):
    """Return {"token", "pendingUserToken", "networkUrl"} (values or None).

    A missing directory or file is not an error: it just means no session
    exists yet. A symlinked or foreign-owned directory/file raises
    SessionError.
    """
    environ = os.environ if environ is None else environ
    directory = _state_dir(environ)
    path = os.path.join(directory, SESSION_FILE_NAME)

    try:
        directory_stat = os.lstat(directory)
    except FileNotFoundError:
        return _empty_session()
    except OSError as error:
        raise SessionError(f"Could not inspect the session directory: {error}") from error
    _refuse_unsafe(directory_stat, "session directory")
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise SessionError("The session directory path is not a directory.")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | cloexec)
    except FileNotFoundError:
        return _empty_session()
    except OSError as error:
        if nofollow and error.errno == errno.ELOOP:
            raise SessionError("Refusing a symlinked session file.") from error
        raise SessionError(f"Could not open the session file: {error}") from error

    try:
        try:
            file_stat = os.fstat(fd)
        except OSError as error:
            raise SessionError(f"Could not inspect the session file: {error}") from error
        _refuse_unsafe(file_stat, "session file")
        if not stat.S_ISREG(file_stat.st_mode):
            raise SessionError("The session file is not a regular file.")
        chunks = []
        total = 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError as error:
                raise SessionError(f"Could not read the session file: {error}") from error
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SESSION_BYTES:
                raise SessionError("The session file is unexpectedly large.")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant) if raw else {}
    except (UnicodeDecodeError, ValueError) as error:
        raise SessionError("The session file contains invalid JSON.") from error
    if not isinstance(data, dict):
        raise SessionError("The session file does not contain a JSON object.")

    session = _empty_session()
    for key in SESSION_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            session[key] = value
    return session


def save_session(session, environ=None):
    """Atomically write `session` (any subset of SESSION_KEYS; others -> null)."""
    environ = os.environ if environ is None else environ
    directory = _state_dir(environ)
    _ensure_state_dir(directory)
    path = os.path.join(directory, SESSION_FILE_NAME)

    try:
        existing_stat = os.lstat(path)
    except FileNotFoundError:
        existing_stat = None
    except OSError as error:
        raise SessionError(f"Could not inspect the session file: {error}") from error
    if existing_stat is not None:
        _refuse_unsafe(existing_stat, "session file")
        if not stat.S_ISREG(existing_stat.st_mode):
            raise SessionError("The session file is not a regular file.")

    canonical = {}
    for key in SESSION_KEYS:
        value = session.get(key)
        if value is not None and not isinstance(value, str):
            raise SessionError(f"Session field {key!r} must be a string or null.")
        canonical[key] = value
    encoded = json.dumps(canonical, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"

    try:
        fd, temp_path = tempfile.mkstemp(prefix=".session.", suffix=".tmp", dir=directory)
    except OSError as error:
        raise SessionError(f"Could not create a temporary session file: {error}") from error
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(directory)
    except OSError as error:
        raise SessionError(f"Could not write the session file: {error}") from error
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _fsync_directory(directory):
    # Best-effort durability: some filesystems (e.g. overlayfs, tmpfs) refuse
    # to fsync a directory descriptor. The atomic os.replace() already
    # happened, so a failure here never leaves the store in a torn state.
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def update_session(updates, environ=None):
    """Load the current session, merge `updates` into it, and save atomically."""
    environ = os.environ if environ is None else environ
    current = load_session(environ)
    for key, value in updates.items():
        if key in SESSION_KEYS:
            current[key] = value
    save_session(current, environ)


def delete_session(environ=None):
    """Delete the session file. Idempotent: never fails when it is absent.

    A missing state directory is treated the same as an absent session (there
    is nothing to delete). A symlinked or foreign-owned state directory is
    refused rather than traversed, same as load_session/save_session -- an
    attacker-controlled directory here could otherwise make os.remove() erase
    an arbitrary file of its choosing.
    """
    environ = os.environ if environ is None else environ
    directory = _state_dir(environ)
    try:
        directory_stat = os.lstat(directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise SessionError(f"Could not inspect the session directory: {error}") from error
    _refuse_unsafe(directory_stat, "session directory")
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise SessionError("The session directory path is not a directory.")

    path = os.path.join(directory, SESSION_FILE_NAME)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except IsADirectoryError:
        pass
    except OSError as error:
        raise SessionError(f"Could not delete the session file: {error}") from error
