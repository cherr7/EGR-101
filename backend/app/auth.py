import os
import secrets

from fastapi import HTTPException, Request


def current_user(request: Request):
    return request.session.get("username")


def require_supervisor(request: Request):
    username = current_user(request)
    if not username:
        raise HTTPException(401, "Supervisor login required.")
    return username


def valid_credentials(username, password):
    return secrets.compare_digest(username, os.getenv("SUPERVISOR_USERNAME", "supervisor")) and secrets.compare_digest(
        password, os.getenv("SUPERVISOR_PASSWORD", "classroom-demo"))
