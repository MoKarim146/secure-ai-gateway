"""Shared ORM base. Every table class inherits from Base, which is how Alembic finds them."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
