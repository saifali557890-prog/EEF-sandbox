def calculate_scores(
    structure_checks: dict,
    test_results: dict,
    secrets_found: list,
    build_success: bool,
    build_time_seconds: float,
) -> dict:
    """
    Calculates weighted engineering scores for repository evaluation.
    """

    # ---------- Structure ----------
    structure_total = max(len(structure_checks), 1)
    structure_pass = sum(bool(v) for v in structure_checks.values())
    structure_ratio = structure_pass / structure_total

    # ---------- Tests ----------
    test_total = max(len(test_results), 1)
    test_pass = sum(bool(v) for v in test_results.values())
    test_ratio = test_pass / test_total

    # ---------- Core Scores ----------
    feature_completion = round(structure_ratio * 100, 1)

    code_quality = round(
        ((structure_ratio * 0.6) + (test_ratio * 0.4)) * 100,
        1,
    )

    architecture = round(structure_ratio * 100, 1)

    # ---------- Security ----------
    security_penalty = min(len(secrets_found) * 15, 60)
    security = round(max(100 - security_penalty, 20), 1)

    # ---------- API ----------
    api_quality = round(
        100 if test_results.get("api", False) else 40,
        1,
    )

    # ---------- Deployment ----------
    deployment_readiness = round(
        100 if build_success else 30,
        1,
    )

    # ---------- Documentation ----------
    documentation = round(
        100 if structure_checks.get("Required Features", False) else 40,
        1,
    )

    # ---------- Performance ----------
    performance = round(
        100 if test_results.get("performance", False) else 50,
        1,
    )

    # ---------- Build Speed ----------
    if build_success:
        if build_time_seconds <= 30:
            build_score = 100
        elif build_time_seconds <= 60:
            build_score = 90
        elif build_time_seconds <= 120:
            build_score = 75
        else:
            build_score = 60
    else:
        build_score = 30

    # ---------- Engineering ----------
    engineering_maturity = round(
        (
            feature_completion
            + code_quality
            + architecture
            + security
            + documentation
        )
        / 5,
        1,
    )

    # ---------- Weighted Overall ----------
    overall = round(
        (
            feature_completion * 0.15
            + code_quality * 0.20
            + architecture * 0.15
            + security * 0.15
            + api_quality * 0.10
            + deployment_readiness * 0.10
            + engineering_maturity * 0.05
            + documentation * 0.05
            + performance * 0.03
            + build_score * 0.02
        ),
        1,
    )

    # ---------- Grade ----------
    if overall >= 90:
        grade = "A+"
    elif overall >= 85:
        grade = "A"
    elif overall >= 80:
        grade = "A-"
    elif overall >= 75:
        grade = "B+"
    elif overall >= 70:
        grade = "B"
    elif overall >= 65:
        grade = "C+"
    elif overall >= 60:
        grade = "C"
    elif overall >= 50:
        grade = "D"
    else:
        grade = "F"

    return {
        "feature_completion": feature_completion,
        "code_quality": code_quality,
        "architecture": architecture,
        "security": security,
        "api_quality": api_quality,
        "deployment_readiness": deployment_readiness,
        "engineering_maturity": engineering_maturity,
        "documentation": documentation,
        "performance": performance,
        "build_score": build_score,
        "overall_score": overall,
        "grade": grade,
    }