import json
import fitz  # PyMuPDF
from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

# ==========================================
# НАСТРОЙКА ЛОКАЛЬНОЙ LLM (Meno-Lite)
# ==========================================
# Внутри Docker-сети адрес Ollama будет 'http://ollama:11434' 
# (имя сервиса в docker-compose, скорее всего 'ollama')
OLLAMA_BASE_URL = "http://ollama:11434" 
MODEL_NAME = "hf.co/bond005/meno-lite-0.1-gguf:Q4_K_M"

llm = ChatOllama(
    model=MODEL_NAME, 
    base_url=OLLAMA_BASE_URL,
    temperature=0.0
)

extraction_prompt = ChatPromptTemplate.from_messages([
    ("system", """Ты — HR-ассистент. Извлеки данные о сотрудниках.
Верни ТОЛЬКО JSON-массив. Без markdown, без текста.
Формат:
[{{"name": "Имя", "surname": "Фамилия", "email": "почта", "phone": "телефон"}}]
Если данных нет — null. Не выдумывай."""),
    ("human", "Текст:\n{text}")
])

chain = extraction_prompt | llm | StrOutputParser()

# ==========================================
# ФУНКЦИИ
# ==========================================
def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Извлекает текст из PDF, полученного в виде байтов (например, из S3)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    return full_text.strip()

def parse_users_from_text(text: str) -> List[Dict[str, Any]]:
    """Отправляет текст в Meno-Lite и возвращает список сотрудников."""
    if not text:
        return []
    try:
        # Ограничиваем текст для Lite-модели (первые 4000 символов)
        truncated_text = text[:4000] 
        
        result = chain.invoke({"text": truncated_text})
        
        # Очистка от артефактов LLM
        clean_result = result.replace("```json", "").replace("```", "").strip()
        
        start_idx = clean_result.find('[')
        end_idx = clean_result.rfind(']') + 1
        if start_idx != -1 and end_idx != 0:
            clean_result = clean_result[start_idx:end_idx]
            
        parsed_json = json.loads(clean_result)
        
        # Валидация обязательных полей
        valid_users = []
        for user in parsed_json:
            if user.get("name") and user.get("surname"):
                valid_users.append({
                    "name": user.get("name"),
                    "surname": user.get("surname"),
                    "email": user.get("email"),
                    "phone": user.get("phone")
                })
        return valid_users
        
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON от LLM: {e}")
        print(f"Сырой ответ: {result}")
        return []
    except Exception as e:
        print(f"❌ Ошибка при извлечении: {e}")
        return []