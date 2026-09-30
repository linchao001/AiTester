from pathlib import Path

from fastapi import FastAPI

from aitester.config import Settings, get_settings
from aitester.interaction.router import router
from aitester.services import ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository

DEFAULT_MODEL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "model_config.json"


def create_app(
    model_config_path: Path | None = None, settings: Settings | None = None
) -> FastAPI:
    s = settings or get_settings()
    path = model_config_path or DEFAULT_MODEL_CONFIG_PATH
    model_config = ModelConfigService(FileJsonConfigRepository(path), s)
    application = FastAPI(title="AiTester backend")
    application.state.model_config = model_config
    application.state.chat_service = ChatService(model_config=model_config)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
