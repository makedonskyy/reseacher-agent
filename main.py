from langchain_gigachat import GigaChat  # новый импорт
from dotenv import load_dotenv
from config import GIGACHAT_CREDENTIALS

load_dotenv()

llm = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    verify_ssl_certs=False,
    scope="GIGACHAT_API_PERS",
)

response = llm.invoke("Привет! Ты готов помогать анализировать научную литературу?")
print(response.content)