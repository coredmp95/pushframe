import json
import os
from pathlib import Path
from typing import Union

from pydantic import BaseModel


def build_path(*args, make_dir: bool = True):
    path = os.path.join(*args)
    if make_dir:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _pydantic_default(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def write_model(model: Union[BaseModel, list[BaseModel]], path: str) -> None:
    with open(path, 'w') as out:
        json.dump(model, out, default=_pydantic_default)


def ensure_private_dir(path, *, tighten: bool = False) -> Path:
    """Create `path` and every missing parent as 0700, regardless of umask.

    `Path.mkdir(mode=..., parents=True)` applies `mode` to the LEAF only --
    intermediate parents get the umask default (prod: 775) -- so this walks
    the missing chain and creates + chmods each new component itself.
    Components that already existed are never touched, except the leaf when
    `tighten=True` and it is owned by the current user with group/other bits
    set. `tighten` is reserved for pushframe's OWN default dirs; a directory
    the operator chose must never be chmod-ed retroactively.
    """
    path = Path(path).expanduser()
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    for component in reversed(missing):
        component.mkdir(mode=0o700, exist_ok=True)
        os.chmod(component, 0o700)
    if tighten and not missing:
        st = path.stat()
        if st.st_uid == os.getuid() and st.st_mode & 0o077:
            os.chmod(path, 0o700)
    return path


def write_private_bytes(path, data: bytes) -> None:
    """Write `data` to `path` as a 0600 file, even when it pre-existed looser."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # O_CREAT keeps a pre-existing file's old mode
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
