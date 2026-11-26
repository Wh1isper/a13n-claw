"""Claw control-plane routers."""

from .routes import claws, connectors, deliveries, schedules

ROUTERS = (claws.router, deliveries.router, schedules.router, connectors.router)
