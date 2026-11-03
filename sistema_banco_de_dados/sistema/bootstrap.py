"""Bibliotecas ficam somente no projeto, sem alterar o Python do computador."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / 'vendor'))
