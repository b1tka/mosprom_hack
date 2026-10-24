from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import boto3 # Для скачивания из S3 (если нужно)
from botocore.client import Config
from pdf_user_pars import extract_text_from_pdf_bytes, parse_users_from_text

router = APIRouter(prefix="/v1/users", tags=["Users"])

# Схема ответа (чистый JSON для бота)
class ExtractedUser(BaseModel):
    name: str
    surname: str
    email: Optional[str] = None
    phone: Optional[str] = None

class ExtractResponse(BaseModel):
    status: str
    count: int
    users: List[ExtractedUser]

# ==========================================
# ВАРИАНТ 1: Бот загружает файл напрямую (multipart/form-data)
# ==========================================
@router.post("/extract", response_model=ExtractResponse)
async def extract_users_from_file(file: UploadFile = File(...)):
    """
    Принимает PDF-файл, извлекает данные новичков и возвращает JSON.
    Дальнейшая запись в БД — на стороне бота.
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Принимаются только PDF файлы")
    
    # Читаем байты файла
    pdf_bytes = await file.read()
    
    # 1. Достаем текст
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст из PDF (возможно, это скан)")
        
    # 2. Отправляем в LLM
    users = parse_users_from_text(text)
    
    if not users:
         return ExtractResponse(status="empty", count=0, users=[])

    return ExtractResponse(
        status="success",
        count=len(users),
        users=users
    )

# ==========================================
# ВАРИАНТ 2: Если бот сначала кидает файл в S3 (как у вас в архитектуре)
# ==========================================
@router.post("/extract-from-s3", response_model=ExtractResponse)
async def extract_users_from_s3(s3_key: str):
    """
    Принимает ключ файла в S3, скачивает его и парсит.
    """
    # ⚠️ Настройки S3 (бот-команда должна подставить свои)
    s3_client = boto3.client(
        's3',
        endpoint_url='http://s3:9000', # Ваш MinIO/S3
        aws_access_key_id='YOUR_ACCESS_KEY',
        aws_secret_access_key='YOUR_SECRET_KEY',
        config=Config(signature_version='s3v4')
    )
    
    try:
        response = s3_client.get_object(Bucket='your-bucket-name', Key=s3_key)
        pdf_bytes = response['Body'].read()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Ошибка чтения из S3: {e}")
        
    text = extract_text_from_pdf_bytes(pdf_bytes)
    users = parse_users_from_text(text)
    
    return ExtractResponse(status="success", count=len(users), users=users)