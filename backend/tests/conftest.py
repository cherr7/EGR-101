import os
from pathlib import Path

import pytest

TEST_URL = os.getenv("TEST_DATABASE_URL")
if TEST_URL:
    from sqlalchemy.engine import make_url
    if not (make_url(TEST_URL).database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point to a separate database whose name ends with _test.")
    os.environ["DATABASE_URL"] = TEST_URL


@pytest.fixture
def database():
    if not TEST_URL:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test.")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from app.db import engine
    # Dropping the isolated test schema also exercises the real migration's audit trigger.
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture
def client(database):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    from app.db import SessionLocal
    with SessionLocal() as session:
        yield session


@pytest.fixture
def stack_factory(client):
    def make_stack(count=20, project="000000001234", material="COTTON-A", treatment="AR", test="ASTM-D5034"):
        response = client.post("/api/prep-drafts", json={"rows": [{"project_number": project,
            "material_ref": material, "test_code": test, "preconditioning_code": treatment, "specimen_count": count}]})
        assert response.status_code == 200, response.text
        draft = response.json()
        commit = client.post(f'/api/prep-drafts/{draft["id"]}/commit')
        assert commit.status_code == 200, commit.text
        result = client.get(f'/api/stacks/{commit.json()["stack_ids"][0]}').json()
        result["draft_id"] = draft["id"]
        return result
    return make_stack


@pytest.fixture
def supervisor(client):
    response = client.post("/api/auth/login", json={"username": os.getenv("SUPERVISOR_USERNAME", "supervisor"),
                                                    "password": os.getenv("SUPERVISOR_PASSWORD", "classroom-demo")})
    assert response.status_code == 200
    return client
