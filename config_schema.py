"""
Configuration schema validation for Dream Cycle agents
"""
from typing import Dict, List, TypedDict, NotRequired
import jsonschema
from jsonschema import validate


class ArxivQuery(TypedDict):
    query: str
    max_results: int


class AgentProfileConfig(TypedDict):
    name: str
    tracks: List[str]
    arxiv_queries: List[ArxivQuery]
    default_github_repos: List[str]
    fetch_cves: bool
    fetch_github_trending: bool
    context: str


# JSON Schema for agent profile validation
AGENT_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tracks": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1
        },
        "arxiv_queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1}
                },
                "required": ["query", "max_results"],
                "additionalProperties": False
            },
            "minItems": 1
        },
        "default_github_repos": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$"},
            "minItems": 1
        },
        "fetch_cves": {"type": "boolean"},
        "fetch_github_trending": {"type": "boolean"},
        "context": {"type": "string"}
    },
    "required": ["name", "tracks", "arxiv_queries", "default_github_repos", "fetch_cves", "fetch_github_trending", "context"],
    "additionalProperties": False
}


def validate_agent_profile(profile: Dict) -> bool:
    """
    Validate an agent profile against the schema.
    
    Args:
        profile: Dictionary containing agent profile configuration
        
    Returns:
        True if valid, False otherwise
        
    Raises:
        jsonschema.exceptions.ValidationError: If profile is invalid
    """
    try:
        validate(instance=profile, schema=AGENT_PROFILE_SCHEMA)
        return True
    except jsonschema.exceptions.ValidationError as e:
        raise e


def load_and_validate_config(config_path: str) -> Dict[str, AgentProfileConfig]:
    """
    Load and validate configuration from file.
    
    Args:
        config_path: Path to configuration file (JSON or YAML)
        
    Returns:
        Dictionary of validated agent profiles
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config file format is invalid or validation fails
    """
    import json
    import yaml
    from pathlib import Path
    
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    # Load based on file extension
    if config_file.suffix.lower() == '.json':
        with open(config_file, 'r') as f:
            config = json.load(f)
    elif config_file.suffix.lower() in ['.yaml', '.yml']:
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "pyyaml is required to load YAML config files: pip install pyyaml"
            )
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")
    else:
        raise ValueError(f"Unsupported configuration file format: {config_file.suffix}. Use .json, .yaml, or .yml")
    
    # Validate each agent profile
    validated_profiles = {}
    for agent_name, profile in config.items():
        try:
            validate_agent_profile(profile)
            validated_profiles[agent_name] = profile  # type: ignore
        except jsonschema.exceptions.ValidationError as e:
            raise ValueError(f"Invalid configuration for agent '{agent_name}': {e.message}")
    
    return validated_profiles


# Example default configuration
DEFAULT_CONFIG_EXAMPLE = {
    "ai_research": {
        "name": "AI Research Agent",
        "tracks": [
            "AI/ML — models, agents, frameworks, tooling",
            "Cybersecurity — CVEs, threat intel, vulnerability research",
            "Robotics/CV — OpenCV, MediaPipe, embedded systems",
            "Data Analytics — pipelines, visualization, MLOps"
        ],
        "arxiv_queries": [
            {"query": "large language models transformer attention", "max_results": 8},
            {"query": "reinforcement learning robotics control", "max_results": 5},
            {"query": "diffusion models generative AI", "max_results": 5},
            {"query": "few-shot learning meta-learning", "max_results": 4},
            {"query": "AI safety alignment interpretability", "max_results": 4}
        ],
        "default_github_repos": [
            "huggingface/transformers",
            "torchlightai/torchx",
            "google-research/bert",
            "openai/gym",
            "pytorch/pytorch"
        ],
        "fetch_cves": False,
        "fetch_github_trending": True,
        "context": (
            "Focus on novel architectures, performance improvements, "
            "and practical implementations that advance the state of the art. "
            "Prioritize papers with open-source code releases."
        )
    },
    "security": {
        "name": "Security Research Agent",
        "tracks": [
            "CVE/Vulnerability Research", 
            "Threat Intelligence",
            "Malware Analysis", 
            "Zero-Day Exploits", 
            "Security Tooling"
        ],
        "arxiv_queries": [
            {"query": "cybersecurity vulnerability detection exploit adversarial", "max_results": 8},
            {"query": "malware detection machine learning intrusion", "max_results": 5},
            {"query": "network security anomaly detection zero-day", "max_results": 5},
        ],
        "default_github_repos": [
            "projectdiscovery/nuclei",
            "aquasecurity/trivy",
            "rapid7/metasploit-framework",
            "nmap/nmap",
            "OWASP/owasp-mstg"
        ],
        "fetch_cves": True,
        "fetch_github_trending": False,
        "context": (
            "Focus on exploitability, CVE severity, defensive tooling, "
            "and threat actor TTPs. Cross-reference MITRE ATT&CK where applicable."
        )
    }
}