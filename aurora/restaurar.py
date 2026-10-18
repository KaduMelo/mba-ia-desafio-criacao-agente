"""Restaura reservas e visitantes ao estado inicial de dados/. Uso: uv run python -m aurora.restaurar"""

from aurora import db

if __name__ == "__main__":
    db.restaurar()
    print("Reservas e visitantes restaurados a partir de dados/.")
