import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app import models


@pytest.fixture()
def client():
    # Fresh in-memory DB per test — StaticPool keeps the same connection
    # alive across the whole test so the schema/data actually persist.
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # seed a notice so /consent/grant has something to reference
    db = TestingSessionLocal()
    db.add(models.ConsentNotice(
        version="v1.0",
        purpose_code="MARKETING",
        purpose_description="Promotional SMS/email",
    ))
    db.add(models.ConsentNotice(
        version="v1.0-loan",
        purpose_code="LOAN_UNDERWRITING",
        purpose_description="Loan evaluation and KYC",
    ))
    db.add(models.ConsentNotice(
        version="v1.0-ecommerce",
        purpose_code="ECOMMERCE_LARGE_PLATFORM_ACCOUNT",
        purpose_description="Large-platform e-commerce account (Third Schedule)",
    ))
    db.commit()
    db.close()

    with TestClient(app) as c:
        c.db_session_factory = TestingSessionLocal  # exposed for tests that need to backdate rows
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def db_session(client):
    """A direct DB session against the same in-memory engine `client` uses,
    for tests that need to seed data or inspect/tamper with rows directly."""
    session = client.db_session_factory()
    yield session
    session.close()
