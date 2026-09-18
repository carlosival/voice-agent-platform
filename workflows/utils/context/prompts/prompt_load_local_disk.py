
import logging
from pathlib import Path

logger = logging.getLogger(__name__)   
BASE_DIR = Path(__file__).parent


def get_prompt(uri:str) -> str:
        try:
            path = BASE_DIR / uri
            with open(path, "r", encoding="utf-8") as file:
                content= file.read()
                return content
        except FileNotFoundError:
                print("The file was not found.") # Change for loggers refactor
        except OSError as e:
                print(f"Error reading file: {e}") # Change for loggers refactor