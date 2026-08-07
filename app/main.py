import os
import json
import logging
from typing import Generator
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from app.models import init_db, SessionLocal, Evaluation, User
from app.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.stack_detector import detect_stack
from app.docker_executor import clone_repository, run_in_container, run_real_tests, cleanup_repo
from app.static_analysis import check_project_structure, scan_for_exposed_secrets
from app.test_runner import run_test_suite
from app.scoring import calculate_scores
from app.feedback import generate_feedback
from app.plagiarism import compute_source_hash, check_similarity
from app.system_monitor import get_system_stats
from app.ai_feedback import generate_ai_feedback

load_dotenv()

# Configure global logger
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---- CORS configuration ----
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
if _raw_origins.strip() == "*":
    ALLOWED_ORIGINS = ["*"]
else:
    ALLOWED_ORIGINS = [origin.strip() for origin in _raw_origins.split(",") if origin.strip()]

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Ezitech Auto Evaluation Platform API",
    description="REST API for the Ezitech Enterprise AI Engineering Sandbox.",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials="*" not in ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Database tables
init_db()

# Dependency for DB Session
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> int:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload.get("user_id")


class EvaluateRequest(BaseModel):
    github_url: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""
    role: str = "intern"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serves the bundled dashboard UI."""
    possible_paths = ["leaderboard.html", "../leaderboard.html"]
    for path in possible_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                return file.read()
    raise HTTPException(status_code=404, detail="leaderboard.html was not found in the root directory")


@app.get("/api/v1/health")
def health_check():
    """Lightweight endpoint to confirm the API is reachable."""
    return {"status": "ok", "service": "ezitech-auto-evaluation-api"}


# ---- Authentication ----

@app.post("/api/v1/auth/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists.")

        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

        user = User(
            email=payload.email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            role=payload.role if payload.role in ("intern", "mentor") else "intern",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token({"user_id": user.id, "email": user.email, "role": user.role})
        return {"status": "success", "token": token, "user": user.to_dict()}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("User registration failed for %s", payload.email)
        raise HTTPException(
            status_code=500,
            detail="Internal server error. Please try again later.",
        )


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_access_token({"user_id": user.id, "email": user.email, "role": user.role})
    return {"status": "success", "token": token, "user": user.to_dict()}


@app.get("/api/v1/auth/me")
def get_me(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user.to_dict()


# ---- Evaluation Pipeline ----

@app.post("/api/v1/evaluate")
@limiter.limit("5/minute")
def evaluate_repo(
    request: Request,
    payload: EvaluateRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    # Strict & Normalized GitHub URL Validation
    url_clean = payload.github_url.strip().rstrip("/")
    if not (url_clean.startswith("https://github.com/") or url_clean.startswith("http://github.com/")) or url_clean.count("/") < 4:
        raise HTTPException(
            status_code=400, 
            detail="Invalid GitHub repository URL. Must be a full repository link like 'https://github.com/owner/repo'."
        )

    # Normalize owner/repo path to lowercase to prevent casing duplicates
    owner_repo_normalized = "/".join(url_clean.split("/")[-2:]).lower()

    repo_path = None
    try:
        # Check against existing records (case-insensitive check using ILIKE/like)
        existing = db.query(Evaluation).filter(Evaluation.github_url.ilike(f"%{owner_repo_normalized}")).first()
        if existing:
            return {"status": "already_evaluated", "data": existing.to_dict()}

        repo_path = clone_repository(url_clean)
        stack = detect_stack(repo_path)

        run_result = run_in_container(repo_path, stack)
        structure_checks = check_project_structure(repo_path, stack)
        test_results = run_test_suite(repo_path, stack)
        test_execution = run_real_tests(repo_path, stack)
        secrets_found = scan_for_exposed_secrets(repo_path)

        scores = calculate_scores(
            structure_checks, test_results, secrets_found,
            run_result["success"], run_result["build_time_seconds"]
        )
        feedback = generate_feedback(structure_checks, test_results, secrets_found)

        if test_execution.get("executed"):
            if test_execution["success"]:
                test_results["unit"] = True
                note = "Test suite executed successfully inside the sandbox container"
                if test_execution.get("passed_count") is not None:
                    note += f" — {test_execution['passed_count']} test(s) passed."
                else:
                    note += "."
                feedback["strengths"].insert(0, note)
            else:
                test_results["unit"] = False
                note = "Test suite was executed inside the sandbox container and reported failures"
                if test_execution.get("failed_count"):
                    note += f" — {test_execution['failed_count']} test(s) failed."
                else:
                    note += "."
                feedback["weaknesses"].insert(0, note)

            scores = calculate_scores(
                structure_checks, test_results, secrets_found,
                run_result["success"], run_result["build_time_seconds"]
            )

        repo_name = url_clean.split("/")[-1]
        ai_result = generate_ai_feedback(
            repo_name, stack, structure_checks, test_results, scores
        )
        if ai_result.get("available"):
            feedback["strengths"] = ai_result["strengths"] + feedback["strengths"]
            feedback["weaknesses"] = ai_result["weaknesses"] + feedback["weaknesses"]
            if ai_result.get("roadmap"):
                feedback["weaknesses"].append(f"Improvement roadmap: {ai_result['roadmap']}")

        source_hash = compute_source_hash(repo_path)
        existing_hashes = [row.source_hash for row in db.query(Evaluation.source_hash).all()]
        similarity = check_similarity(source_hash, existing_hashes)
        if similarity == 100.0:
            feedback["weaknesses"].insert(0, "This submission is structurally identical to a previously evaluated repository.")

        record = Evaluation(
            user_id=current_user_id,
            repo_name=repo_name,
            github_url=url_clean,
            stack=stack,
            feature_completion=scores["feature_completion"],
            code_quality=scores["code_quality"],
            architecture=scores["architecture"],
            security=scores["security"],
            api_quality=scores["api_quality"],
            deployment_readiness=scores["deployment_readiness"],
            engineering_maturity=scores["engineering_maturity"],
            documentation=scores["documentation"],
            performance=scores["performance"],
            overall_score=scores["overall_score"],
            grade=scores["grade"],
            build_time_seconds=run_result["build_time_seconds"],
            tests_json=json.dumps(test_results),
            strengths_json=json.dumps(feedback["strengths"]),
            weaknesses_json=json.dumps(feedback["weaknesses"]),
            structure_checks_json=json.dumps(structure_checks),
            cpu_percent=run_result["stats"].get("cpu_percent", 0),
            mem_usage_mb=run_result["stats"].get("mem_usage_mb", 0),
            source_hash=source_hash,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        return {"status": "success", "data": record.to_dict()}

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Repository evaluation failed for %s", payload.github_url)
        raise HTTPException(
            status_code=500,
            detail="Internal server error. Please try again later.",
        )

    finally:
        if repo_path:
            cleanup_repo(repo_path)


@app.get("/api/v1/leaderboard")
def get_leaderboard(db: Session = Depends(get_db)):
    """Public endpoint to fetch the global evaluation leaderboard."""
    records = db.query(Evaluation).order_by(Evaluation.overall_score.desc()).all()
    return [r.to_dict() for r in records]


@app.get("/api/v1/report/{evaluation_id}")
def get_report(evaluation_id: int, db: Session = Depends(get_db)):
    record = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Evaluation report not found.")
    return record.to_dict()


@app.get("/api/v1/my-evaluations")
def get_my_evaluations(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Returns evaluations submitted by the authenticated user."""
    records = db.query(Evaluation).filter(Evaluation.user_id == user_id).order_by(Evaluation.id.desc()).all()
    return [r.to_dict() for r in records]


@app.get("/api/v1/system-stats")
@limiter.limit("120/minute")
def system_stats(request: Request):
    return get_system_stats()