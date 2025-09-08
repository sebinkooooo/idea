from fastapi import FastAPI
from sqlalchemy import create_engine

from backend import models, db

# Import routers
from backend.auth import main as auth_router
from backend.ideas import main as ideas_router
from backend.chat import main as chat_router
from backend.assets import main as assets_router
from backend.feed import main as feed_router
from backend.home import main as home_router
from backend.share import main as share_router


# Optional extras if you build them out
# from backend.feed import main as feed_router
# from backend.home import main as home_router
# from backend.users import main as users_router

# Create DB tables (if not exist)
models.Base.metadata.create_all(bind=db.engine)

# FastAPI app
app = FastAPI(title="Living Ideas API", version="0.2")

# Register routers
app.include_router(auth_router.router, prefix="/auth", tags=["Auth"])
app.include_router(ideas_router.router, prefix="/ideas", tags=["Ideas"])
app.include_router(chat_router.router, prefix="/chat", tags=["Chat"])
app.include_router(assets_router.router, prefix="/assets", tags=["Assets"])
app.include_router(feed_router.router, prefix="/feed", tags=["Feed"])
app.include_router(home_router.router, prefix="/home", tags=["Home"])
app.include_router(share_router.router, prefix="/share", tags=["Share"])


@app.get("/")
def root():
    return {"message": "Welcome to the Living Ideas API 🚀"}