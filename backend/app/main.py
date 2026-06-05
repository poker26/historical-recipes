import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import books, recipes, plants, dictionaries, search, indexing, pipeline, wizard, compounds, medical, identify

# Configure logging so our logger.info() calls show up in docker logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="Historical Recipes API",
    description="API для работы с коллекцией старинных рецептов настоек и травников",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(books.router, prefix="/api/books", tags=["books"])
app.include_router(recipes.router, prefix="/api/recipes", tags=["recipes"])
app.include_router(plants.router, prefix="/api/plants", tags=["plants"])
app.include_router(dictionaries.router, prefix="/api/dictionaries", tags=["dictionaries"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(indexing.router, prefix="/api/indexing", tags=["indexing"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(wizard.router, prefix="/api/wizard", tags=["wizard"])
app.include_router(compounds.router, prefix="/api/compounds", tags=["compounds"])
app.include_router(medical.router, prefix="/api/medical", tags=["medical"])
app.include_router(identify.router, prefix="/api/identify", tags=["identify"])


@app.get("/health")
async def health():
    return {"status": "ok"}
