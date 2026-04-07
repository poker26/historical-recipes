import uuid

from pydantic import BaseModel


class DictionaryTermCreate(BaseModel):
    category: str
    term_old: str
    term_modern: str
    context: str | None = None
    source_book_id: uuid.UUID | None = None


class DictionaryTermUpdate(BaseModel):
    category: str | None = None
    term_old: str | None = None
    term_modern: str | None = None
    context: str | None = None


class DictionaryTermOut(BaseModel):
    id: uuid.UUID
    category: str
    term_old: str
    term_modern: str
    context: str | None
    source_book_id: uuid.UUID | None

    model_config = {"from_attributes": True}
