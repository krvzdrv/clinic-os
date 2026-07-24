"""
WSGI-точка входа для PythonAnywhere.

PythonAnywhere использует WSGI для запуска Flask-приложений.
При создании веб-приложения на PythonAnywhere укажи путь к этому файлу.
"""
import sys
import os
from dotenv import load_dotenv
load_dotenv()

# Добавляем папку проекта в путь
project_home = os.path.dirname(__file__)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import app as application
