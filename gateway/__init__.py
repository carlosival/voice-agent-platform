import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import redis
import jwt

# Inicializar la aplicación FastAPI
app = FastAPI()

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conectar a Redis
redis_client = redis.StrictRedis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)

# Importar rutas
from .routes import router
app.include_router(router)