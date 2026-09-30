from pathlib import Path

from fastapi import FastAPI

from aitester.config import Settings, get_settings
from aitester.interaction.router import router
from aitester.services import CapabilityConfigService, ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def create_app(
    model_config_path: Path | None = None,
    capability_config_path: Path | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    s = settings or get_settings()
    model_config = ModelConfigService(
        FileJsonConfigRepository(model_config_path or DATA_DIR / "model_config.json"), s
    )
    capability_config = CapabilityConfigService(
        FileJsonConfigRepository(
            capability_config_path or DATA_DIR / "capability_config.json"
        ),
        model_config,
    )
    application = FastAPI(title="AiTester backend")
    application.state.model_config = model_config
    application.state.capability_config = capability_config
    application.state.chat_service = ChatService(model_config=model_config)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
