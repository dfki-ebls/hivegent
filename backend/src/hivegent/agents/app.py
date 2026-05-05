"""Agent assembly for Hivegent."""

from pydantic_ai import Agent

from .common import UserDeps

__all__ = ["base_agent", "user_agent"]

base_agent: Agent[None, str] = Agent(retries=1)
user_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps, retries=1)
