from pydantic import BaseModel, Field


class EchoRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)


class EchoResponse(BaseModel):
    reply: str
    trace: list[str]


class SendRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)


class SendResponse(BaseModel):
    reply: str
    trace: list[str]
    model: str


class ModelInfo(BaseModel):
    id: str
    enabled: bool
    max_output: int
    context: int


class ProviderInfo(BaseModel):
    id: str
    name: str
    base_url: str
    has_key: bool
    key_masked: str
    models: list[ModelInfo]


class ModelsResponse(BaseModel):
    default_uid: str
    providers: list[ProviderInfo]


class KeyUpdate(BaseModel):
    api_key: str | None = None


class EnabledUpdate(BaseModel):
    enabled: bool


class DefaultUpdate(BaseModel):
    uid: str
