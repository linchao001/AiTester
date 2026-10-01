from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from aitester.adapters.tools import FileObservationStore
from aitester.config import Settings, get_settings
from aitester.interaction.router import router
from aitester.services import CapabilityConfigService, ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.kb.manager import RemeKbManager
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def create_app(
    model_config_path: Path | None = None,
    capability_config_path: Path | None = None,
    settings: Settings | None = None,
    kb_manager=None,
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
    kb = (
        kb_manager
        if kb_manager is not None
        else RemeKbManager(settings=s, data_dir=DATA_DIR)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        kb.start()
        try:
            yield
        finally:
            kb.close_all()

    application = FastAPI(title="AiTester backend", lifespan=lifespan)
    application.state.kb_manager = kb
    application.state.model_config = model_config
    application.state.capability_config = capability_config
    application.state.file_observations = FileObservationStore()
    application.state.agent_runtime = AgentRuntime(
        capability_config, model_config, application.state.file_observations
    )
    application.state.chat_service = ChatService(agent_runtime=application.state.agent_runtime)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
