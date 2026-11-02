"""Public input and provider output contracts."""
import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Annotated[str, Field(min_length=1, max_length=250)]
    manufacturer: Annotated[str, Field(max_length=250)] | None = None
    specs: dict[str, str | int | float | None] = Field(default_factory=dict)
    description: Annotated[str, Field(max_length=12000)] | None = None

    @field_validator('name', 'manufacturer', 'description', mode='before')
    @classmethod
    def clean_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('specs', mode='before')
    @classmethod
    def clean_specs(cls, value):
        if not isinstance(value, dict) or len(value) > 60:
            raise ValueError('Характеристики: объект, не более 60 строк.')
        clean = {}
        for name, item in value.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 150:
                raise ValueError('У характеристики должно быть непустое имя до 150 символов.')
            if type(item) not in (str, int, float, type(None)):
                raise ValueError('Значение характеристики: строка, число или null.')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Число должно быть конечным.')
            if isinstance(item, str) and len(item) > 1000:
                raise ValueError('Значение характеристики слишком длинное.')
            name = name.strip()
            if name in clean:
                raise ValueError('Имена характеристик после удаления пробелов совпадают.')
            clean[name] = item.strip() if isinstance(item, str) else item
        return clean


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    value: str
    quote: str


class ModelDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seo_title: str
    seo_description: str
    search_queries: list[str]
    commercial_block: str
    extracted_facts: list[ExtractedFact]
    conflicts: list[ExtractedFact]
    warnings: list[str]
