import json
import os
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
