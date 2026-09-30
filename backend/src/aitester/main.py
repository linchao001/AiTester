from fastapi import FastAPI

from aitester.interaction.router import router


def create_app() -> FastAPI:
    application = FastAPI(title="AiTester backend")
    application.include_router(router)
    return application


app = create_app()
