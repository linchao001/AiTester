from fastapi import FastAPI

from aitester.interaction.router import router
from aitester.services import ChatService


def create_app() -> FastAPI:
    application = FastAPI(title="AiTester backend")
    application.state.chat_service = ChatService()
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from aitester.config import get_settings

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
