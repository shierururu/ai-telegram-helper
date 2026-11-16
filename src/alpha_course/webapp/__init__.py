"""Telegram Mini App «Бизнес-навигатор»: JSON API and static front end (docs/webapp-api.md)."""

from alpha_course.webapp.api import Services
from alpha_course.webapp.server import create_app, run_webapp, start_webapp

__all__ = ["Services", "create_app", "run_webapp", "start_webapp"]
