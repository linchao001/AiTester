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
