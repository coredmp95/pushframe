from typing import Optional

from pydantic import BaseModel, create_model


def make_partial(model: type[BaseModel], name: str) -> type[BaseModel]:
    fields = {
        fname: (Optional[finfo.annotation], None)
        for fname, finfo in model.model_fields.items()
    }
    return create_model(name, __base__=model, **fields)
