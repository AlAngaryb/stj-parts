import pytest
from fastapi.testclient import TestClient

from stjparts import db
from stjparts.api import create_app


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as tc:
        yield tc
    app.state.conn.close()
