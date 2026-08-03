import os
import json
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

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

# ---- CORS configuration ----
# Reads allowed origins from the ALLOWED_ORIGINS env var (comma-separated).
# Defaults to "*" only if the variable is missing entirely — set this to the
# real Ezitech website domain(s) before going live, e.g.:
#   ALLOWED_ORIGINS=https://ezitech.com,https://portal.ezitech.com
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
if _raw_origins.strip() == "*":
    ALLOWED_ORIGINS = ["*"]
else:
    ALLOWED_ORIGINS = [origin.strip() for origin in _raw_origins.split(",") if origin.strip()]

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Ezitech Auto Evaluation Platform API",
    description="REST API for the Ezitech Enterprise AI Engineering Sandbox. "
                "Can be consumed independently by any external website "
                "(e.g. the Ezitech Internship Portal) — the endpoints below "
                "do not depend on the bundled dashboard UI.",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

bearer_scheme = HTTPBearer()


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> int:
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please log in again.")
    return payload.get("user_id")


class EvaluateRequest(BaseModel):
    github_url: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "intern"


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serves the bundled dashboard UI. External websites integrating via
    the API directly do not need to call this endpoint at all."""
    possible_paths = ["leaderboard.html", "../leaderboard.html"]
    for path in possible_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                return file.read()
    raise HTTPException(status_code=404, detail="leaderboard.html was not found in the root directory")


@app.get("/api/v1/health")
def health_check():
    """Lightweight endpoint external systems can poll to confirm the API is reachable."""
    return {"status": "ok", "service": "ezitech-auto-evaluation-api"}


# ---- Authentication ----

@app.post("/api/v1/auth/register")
def register(payload: RegisterRequest):
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists.")

        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

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
    finally:
        db.close()


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == payload.email).first()
        if not user or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        token = create_access_token({"user_id": user.id, "email": user.email, "role": user.role})
        return {"status": "success", "token": token, "user": user.to_dict()}
    finally:
        db.close()


@app.get("/api/v1/auth/me")
def get_me(user_id: int = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user.to_dict()
    finally:
        db.close()


# ---- Evaluation Pipeline (requires login) ----

@app.post("/api/v1/evaluate")
@limiter.limit("5/minute")
def evaluate_repo(request: Request, payload: EvaluateRequest, current_user_id: int = Depends(get_current_user_id)):
    db = SessionLocal()
    repo_path = None

    try:
        existing = db.query(Evaluation).filter(Evaluation.github_url == payload.github_url).first()
        if existing:
            return {"status": "already_evaluated", "data": existing.to_dict()}

        repo_path = clone_repository(payload.github_url)
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

        ai_result = generate_ai_feedback(
            payload.github_url.rstrip("/").split("/")[-1],
            stack, structure_checks, test_results, scores
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

        repo_name = payload.github_url.rstrip("/").split("/")[-1]

        record = Evaluation(
            user_id=current_user_id,
            repo_name=repo_name,
            github_url=payload.github_url,
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if repo_path:
            cleanup_repo(repo_path)
        db.close()


@app.get("/api/v1/leaderboard")
def get_leaderboard():
    """Public endpoint — no authentication required. Any external website
    (e.g. the Ezitech Internship Portal) can call this directly to display
    the scoreboard in its own UI."""
    db = SessionLocal()
    try:
        records = db.query(Evaluation).order_by(Evaluation.overall_score.desc()).all()
        return [r.to_dict() for r in records]
    finally:
        db.close()


@app.get("/api/v1/report/{evaluation_id}")
def get_report(evaluation_id: int):
    db = SessionLocal()
    try:
        record = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="Evaluation not found")
        return record.to_dict()
    finally:
        db.close()


@app.get("/api/v1/my-evaluations")
def get_my_evaluations(user_id: int = Depends(get_current_user_id)):
    """Returns only the evaluations submitted by the currently authenticated
    user — useful for an external website's 'My Submissions' page."""
    db = SessionLocal()
    try:
        records = db.query(Evaluation).filter(Evaluation.user_id == user_id).order_by(Evaluation.id.desc()).all()
        return [r.to_dict() for r in records]
    finally:
        db.close()


@app.get("/api/v1/system-stats")
@limiter.limit("120/minute")
def system_stats(request: Request):
    return get_system_stats()