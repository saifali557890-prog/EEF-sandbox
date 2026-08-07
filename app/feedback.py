import logging

logger = logging.getLogger(__name__)

MAX_FEEDBACK_ITEMS = 6


def generate_feedback(
    structure_checks: dict,
    test_results: dict,
    secrets_found: list,
) -> dict:
    """
    Generates structured strengths and weaknesses based on
    project structure, testing, and security analysis.
    """

    strengths = []
    weaknesses = []

    # ----------------------------
    # Structure Evaluation
    # ----------------------------
    for check, passed in structure_checks.items():

        if passed:
            strengths.append(f"{check} is properly implemented.")
        else:
            weaknesses.append(f"{check} is missing or incomplete.")

    # ----------------------------
    # Test Evaluation
    # ----------------------------
    for test_name, passed in test_results.items():

        readable = test_name.replace("_", " ").title()

        if passed:
            strengths.append(f"{readable} tests are available.")
        else:
            weaknesses.append(
                f"{readable} tests are missing. Add coverage for this area."
            )

    # ----------------------------
    # Security
    # ----------------------------
    if secrets_found:

        preview = ", ".join(secrets_found[:3])

        if len(secrets_found) > 3:
            preview += " ..."

        weaknesses.append(
            f"Potential hardcoded secrets detected in: {preview}. "
            "Move sensitive values to environment variables."
        )

    else:
        strengths.append(
            "No hardcoded secrets were detected during static analysis."
        )

    # ----------------------------
    # General recommendations
    # ----------------------------
    if structure_checks.get("Required Features", False):
        strengths.append(
            "Project documentation is available."
        )
    else:
        weaknesses.append(
            "README documentation should be improved."
        )

    if structure_checks.get("Error Handling", False):
        strengths.append(
            "Basic exception handling has been implemented."
        )

    if structure_checks.get("Security Configuration", False):
        strengths.append(
            "Security configuration files are present."
        )

    # ----------------------------
    # Remove duplicate entries
    # ----------------------------
    strengths = list(dict.fromkeys(strengths))
    weaknesses = list(dict.fromkeys(weaknesses))

    # ----------------------------
    # Fallbacks
    # ----------------------------
    if not strengths:
        strengths.append(
            "Repository structure is sufficient for automated evaluation."
        )

    if not weaknesses:
        weaknesses.append(
            "No major issues were detected during this evaluation."
        )

    # ----------------------------
    # Sort by length (more informative first)
    # ----------------------------
    strengths.sort(key=len, reverse=True)
    weaknesses.sort(key=len, reverse=True)

    return {
        "strengths": strengths[:MAX_FEEDBACK_ITEMS],
        "weaknesses": weaknesses[:MAX_FEEDBACK_ITEMS],
    }