"""
Skill extraction — scans title + description_text for known skills.
Returns a deduplicated sorted list of skill tags.
"""

import re

# Skills dictionary organized by category
# Each entry: (display_name, [patterns_to_match])
SKILLS = {
    # Programming languages
    "Python": [r"\bpython\b"],
    "Java": [r"\bjava\b(?!\s*script)"],
    "JavaScript": [r"\bjavascript\b", r"\bjs\b"],
    "TypeScript": [r"\btypescript\b", r"\bts\b(?:\s+/|\b)"],
    "Go": [r"\bgolang\b", r"\bgo\b(?:\s+(?:lang|programming|developer|engineer))"],
    "Rust": [r"\brust\b"],
    "C++": [r"\bc\+\+\b", r"\bcpp\b"],
    "C#": [r"\bc#\b", r"\bcsharp\b", r"\bc\s*sharp\b"],
    "Ruby": [r"\bruby\b"],
    "PHP": [r"\bphp\b"],
    "Swift": [r"\bswift\b"],
    "Kotlin": [r"\bkotlin\b"],
    "Scala": [r"\bscala\b"],
    "R": [r"\br\b(?:\s+(?:programming|language|studio))"],
    "Perl": [r"\bperl\b"],
    "Elixir": [r"\belixir\b"],
    "Haskell": [r"\bhaskell\b"],
    "Lua": [r"\blua\b"],
    "Dart": [r"\bdart\b"],
    "Objective-C": [r"\bobjective[\s-]?c\b"],
    # Web frameworks
    "React": [r"\breact(?:\.?js)?\b"],
    "Angular": [r"\bangular\b"],
    "Vue": [r"\bvue(?:\.?js)?\b"],
    "Next.js": [r"\bnext\.?js\b"],
    "Node.js": [r"\bnode\.?js\b", r"\bnode\b"],
    "Express": [r"\bexpress(?:\.?js)?\b"],
    "Django": [r"\bdjango\b"],
    "Flask": [r"\bflask\b"],
    "FastAPI": [r"\bfastapi\b"],
    "Spring": [r"\bspring\s*boot\b", r"\bspring\b(?:\s+framework)?"],
    "Rails": [r"\brails\b", r"\bruby on rails\b"],
    "Laravel": [r"\blaravel\b"],
    ".NET": [r"\b\.net\b", r"\bdotnet\b", r"\basp\.net\b"],
    "GraphQL": [r"\bgraphql\b"],
    # Cloud
    "AWS": [r"\baws\b", r"\bamazon web services\b"],
    "GCP": [r"\bgcp\b", r"\bgoogle cloud\b"],
    "Azure": [r"\bazure\b", r"\bmicrosoft azure\b"],
    # Data / ML
    "Databricks": [r"\bdatabricks\b"],
    "Snowflake": [r"\bsnowflake\b"],
    "BigQuery": [r"\bbigquery\b", r"\bbig query\b"],
    "Redshift": [r"\bredshift\b"],
    "Spark": [r"\bspark\b", r"\bpyspark\b", r"\bapache spark\b"],
    "Airflow": [r"\bairflow\b"],
    "dbt": [r"\bdbt\b"],
    "Kafka": [r"\bkafka\b"],
    "Flink": [r"\bflink\b"],
    "Hadoop": [r"\bhadoop\b"],
    "TensorFlow": [r"\btensorflow\b"],
    "PyTorch": [r"\bpytorch\b"],
    "scikit-learn": [r"\bscikit[\s-]?learn\b", r"\bsklearn\b"],
    "Pandas": [r"\bpandas\b"],
    "NumPy": [r"\bnumpy\b"],
    "LLM": [r"\bllm(?:s)?\b", r"\blarge language model\b"],
    "NLP": [r"\bnlp\b", r"\bnatural language processing\b"],
    "Computer Vision": [r"\bcomputer vision\b", r"\bcv\b(?:\s+(?:model|pipeline))"],
    # Databases
    "SQL": [r"\bsql\b"],
    "PostgreSQL": [r"\bpostgres(?:ql)?\b"],
    "MySQL": [r"\bmysql\b"],
    "MongoDB": [r"\bmongodb?\b"],
    "Redis": [r"\bredis\b"],
    "Elasticsearch": [r"\belasticsearch\b", r"\belastic\b"],
    "DynamoDB": [r"\bdynamodb\b"],
    "Cassandra": [r"\bcassandra\b"],
    "Neo4j": [r"\bneo4j\b"],
    # DevOps / Infra
    "Docker": [r"\bdocker\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b"],
    "Terraform": [r"\bterraform\b"],
    "Ansible": [r"\bansible\b"],
    "Jenkins": [r"\bjenkins\b"],
    "CI/CD": [r"\bci\s*/?\s*cd\b"],
    "Git": [r"\bgit\b(?!hub)"],
    "GitHub Actions": [r"\bgithub actions\b"],
    "ArgoCD": [r"\bargocd\b", r"\bargo\s*cd\b"],
    "Prometheus": [r"\bprometheus\b"],
    "Grafana": [r"\bgrafana\b"],
    "Datadog": [r"\bdatadog\b"],
    "Splunk": [r"\bsplunk\b"],
    # Mobile
    "iOS": [r"\bios\b"],
    "Android": [r"\bandroid\b"],
    "React Native": [r"\breact native\b"],
    "Flutter": [r"\bflutter\b"],
    # Other
    "Linux": [r"\blinux\b"],
    "REST API": [r"\brest\s*(?:ful)?\s*api\b"],
    "gRPC": [r"\bgrpc\b"],
    "RabbitMQ": [r"\brabbitmq\b"],
    "Figma": [r"\bfigma\b"],
    "Tableau": [r"\btableau\b"],
    "Power BI": [r"\bpower\s*bi\b"],
    "Looker": [r"\blooker\b"],
}

# Pre-compile all patterns
_COMPILED_SKILLS: list[tuple[str, list[re.Pattern]]] = []
for skill_name, patterns in SKILLS.items():
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    _COMPILED_SKILLS.append((skill_name, compiled))


def extract_skills(title: str, description_text: str = "") -> list[str]:
    """
    Extract skills from title and description.
    Returns sorted deduplicated list of skill names.
    """
    text = f"{title}\n{description_text}".lower()
    found = []

    for skill_name, patterns in _COMPILED_SKILLS:
        for pattern in patterns:
            if pattern.search(text):
                found.append(skill_name)
                break

    return sorted(set(found))
