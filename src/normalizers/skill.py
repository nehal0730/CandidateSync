"""
normalizers/skill.py
====================
Normalize skill names to a canonical lowercase form using a predefined alias map.

Design:
  - Input is lowercased and stripped before lookup.
  - Aliases resolve to canonical names (e.g. 'js' → 'javascript').
  - Unknown skills pass through as-is (lowercased) — never dropped.
"""
from typing import Optional

# Predefined alias map: alias (lowercase) → canonical name (lowercase)
SKILL_ALIAS_MAP: dict[str, str] = {
    # JavaScript ecosystem
    "js":           "javascript",
    "es6":          "javascript",
    "es2015":       "javascript",
    "react.js":     "react",
    "reactjs":      "react",
    "react native": "react native",
    "vue.js":       "vue",
    "vuejs":        "vue",
    "node.js":      "node.js",
    "nodejs":       "node.js",
    "next.js":      "next.js",
    "nextjs":       "next.js",
    "express.js":   "express",
    "expressjs":    "express",
    # TypeScript
    "ts":           "typescript",
    # Python
    "py":           "python",
    "py3":          "python",
    # ML / AI
    "ml":           "machine learning",
    "ai":           "artificial intelligence",
    "dl":           "deep learning",
    "nlp":          "natural language processing",
    "cv":           "computer vision",
    "rl":           "reinforcement learning",
    "genai":        "generative ai",
    # Frameworks
    "tf":           "tensorflow",
    "torch":        "pytorch",
    "sk-learn":     "scikit-learn",
    "sklearn":      "scikit-learn",
    "xgb":          "xgboost",
    "lgbm":         "lightgbm",
    # Data
    "pg":           "postgresql",
    "postgres":     "postgresql",
    "mongo":        "mongodb",
    "es":           "elasticsearch",
    "elastic":      "elasticsearch",
    "redis cache":  "redis",
    # Infrastructure
    "k8s":          "kubernetes",
    "kube":         "kubernetes",
    "docker-compose":"docker",
    "aws":          "aws",
    "gcp":          "gcp",
    "azure":        "azure",
    "gcloud":       "gcp",
    # APIs / patterns
    "rest api":     "rest",
    "restful":      "rest",
    "rest apis":    "rest",
    "graphql api":  "graphql",
    "grpc":         "grpc",
    "ci/cd":        "ci/cd",
    "cicd":         "ci/cd",
    # Languages
    "c++":          "c++",
    "cpp":          "c++",
    "csharp":       "c#",
    "c#":           "c#",
    ".net":         ".net",
    "dotnet":       ".net",
    "golang":       "go",
    "ruby on rails":"rails",
    "ror":          "rails",
    "r language":   "r",
    # Data science
    "data analysis":"data analysis",
    "data analytics":"data analysis",
    "data viz":     "data visualization",
    "data visualisation": "data visualization",
    "stats":        "statistics",
    # Cloud / Devops
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    "terraform":    "terraform",
    "ansible":      "ansible",
    "jenkins":      "jenkins",
    "github actions":"github actions",
    "gh actions":   "github actions",
}


def normalize_skill(raw: str) -> Optional[str]:
    """
    Returns canonical skill name (lowercase) or None if input is empty.

    Examples
    --------
    normalize_skill("JS")         → "javascript"
    normalize_skill("React.js")   → "react"
    normalize_skill("FastAPI")    → "fastapi"   (unknown → pass-through)
    """
    if not raw or not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return SKILL_ALIAS_MAP.get(key, key)