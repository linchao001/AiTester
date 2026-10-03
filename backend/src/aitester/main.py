from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from aitester.adapters.tools import FileObservationStore
from aitester.config import Settings, get_settings
from aitester.interaction.kb_browse import router as kb_browse_router
from aitester.interaction.pick_dir import router as pick_dir_router
from aitester.interaction.projects import router as projects_router
from aitester.interaction.router import router
from aitester.interaction.sessions import router as sessions_router
from aitester.services import CapabilityConfigService, ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.kb.manager import RemeKbManager
from aitester.services.model_config import ModelConfigService
from aitester.services.project_config import ProjectService
from aitester.services.session_store import SessionStore
from aitester.storage import FileJsonConfigRepository

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def create_app(
    model_config_path: Path | None = None,
    capability_config_path: Path | None = None,
    projects_path: Path | None = None,
    settings: Settings | None = None,
    kb_manager=None,
    sessions_dir: Path | None = None,
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
    project_config = ProjectService(
        FileJsonConfigRepository(projects_path or DATA_DIR / "projects.json")
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
    application.state.settings = s
    application.state.model_config = model_config
    application.state.capability_config = capability_config
    application.state.project_config = project_config
    application.state.file_observations = FileObservationStore()
    application.state.agent_runtime = AgentRuntime(
        capability_config, model_config, application.state.file_observations, kb=kb
    )
    sessions = SessionStore(sessions_dir or DATA_DIR / "sessions")
    application.state.sessions = sessions
    application.state.chat_service = ChatService(
        agent_runtime=application.state.agent_runtime,
        sessions=sessions,
        projects=project_config,
    )
    application.include_router(router)
    application.include_router(pick_dir_router)
    application.include_router(kb_browse_router)
    application.include_router(projects_router)
    application.include_router(sessions_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
