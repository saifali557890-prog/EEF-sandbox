import os
import json

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    ForeignKey,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String(255), unique=True, index=True, nullable=False)

    hashed_password = Column(String, nullable=False)

    full_name = Column(String(255), default="")

    role = Column(String(20), default="intern")

    evaluations = relationship(
        "Evaluation",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
        }


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    user = relationship(
        "User",
        back_populates="evaluations",
    )

    repo_name = Column(String, index=True)

    github_url = Column(
        String,
        unique=True,
        index=True,
    )

    stack = Column(String)

    feature_completion = Column(Float, default=0)

    code_quality = Column(Float, default=0)

    architecture = Column(Float, default=0)

    security = Column(Float, default=0)

    api_quality = Column(Float, default=0)

    deployment_readiness = Column(Float, default=0)

    engineering_maturity = Column(Float, default=0)

    documentation = Column(Float, default=0)

    performance = Column(Float, default=0)

    overall_score = Column(Float, default=0)

    grade = Column(String(10), default="C")

    build_time_seconds = Column(Float, default=0)

    tests_json = Column(Text, default="{}")

    strengths_json = Column(Text, default="[]")

    weaknesses_json = Column(Text, default="[]")

    structure_checks_json = Column(Text, default="{}")

    cpu_percent = Column(Float, default=0)

    mem_usage_mb = Column(Float, default=0)

    source_hash = Column(String, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "repo_name": self.repo_name,
            "github_url": self.github_url,
            "stack": self.stack,
            "feature_completion": self.feature_completion,
            "code_quality": self.code_quality,
            "architecture": self.architecture,
            "security": self.security,
            "api_quality": self.api_quality,
            "deployment_readiness": self.deployment_readiness,
            "engineering_maturity": self.engineering_maturity,
            "documentation": self.documentation,
            "performance": self.performance,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "build_time": f"{int(self.build_time_seconds // 60)}m {int(self.build_time_seconds % 60)}s",
            "tests": json.loads(self.tests_json or "{}"),
            "strengths": json.loads(self.strengths_json or "[]"),
            "weaknesses": json.loads(self.weaknesses_json or "[]"),
            "structure_checks": json.loads(
                self.structure_checks_json or "{}"
            ),
            "cpu_percent": self.cpu_percent,
            "mem_usage_mb": self.mem_usage_mb,
        }


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./sandbox.db",
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1,
    )

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
    }

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def init_db():
    Base.metadata.create_all(bind=engine)