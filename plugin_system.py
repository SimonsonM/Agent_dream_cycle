"""
Plugin system for Dream Cycle agents
Allows community contributions and extensibility
"""
import importlib.util
import inspect
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Any, Optional
import yaml


class ResearchTrackPlugin(ABC):
    """Abstract base class for research track plugins"""
    
    @abstractmethod
    def get_name(self) -> str:
        """Return the name of the research track"""
        pass
    
    @abstractmethod
    def get_arxiv_queries(self) -> List[Dict[str, Any]]:
        """Return list of arXiv queries for this track"""
        pass
    
    @abstractmethod
    def get_github_repos(self) -> List[str]:
        """Return list of default GitHub repositories for this track"""
        pass
    
    @abstractmethod
    def get_context(self) -> str:
        """Return context/prompt for this research track"""
        pass
    
    def get_cves_enabled(self) -> bool:
        """Whether this track should fetch CVEs (default: False)"""
        return False
    
    def get_github_trending_enabled(self) -> bool:
        """Whether this track should fetch GitHub trending (default: False)"""
        return False


class AgentPlugin(ABC):
    """Abstract base class for agent plugins"""
    
    @abstractmethod
    def get_agent_name(self) -> str:
        """Return the name of the agent"""
        pass
    
    @abstractmethod
    def get_research_tracks(self) -> List[ResearchTrackPlugin]:
        """Return list of research tracks for this agent"""
        pass
    
    def get_description(self) -> str:
        """Return description of what this agent does"""
        return "A Dream Cycle agent"
    
    def get_version(self) -> str:
        """Return version of the plugin"""
        return "1.0.0"


class PluginManager:
    """Manages loading and execution of plugins"""
    
    def __init__(self, plugins_dir: Optional[Path] = None):
        self.plugins_dir = plugins_dir or Path.home() / ".dream-cycle" / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._loaded_plugins: Dict[str, AgentPlugin] = {}
        self._research_tracks: Dict[str, ResearchTrackPlugin] = {}
    
    def load_plugins(self) -> None:
        """Load all plugins from the plugins directory"""
        # Clear existing plugins
        self._loaded_plugins.clear()
        self._research_tracks.clear()
        
        # Load Python plugins
        for plugin_file in self.plugins_dir.glob("*.py"):
            self._load_python_plugin(plugin_file)
        
        # Load YAML/JSON plugins
        for config_file in self.plugins_dir.glob("*.[yY][aA][mM][lL]"):
            self._load_yaml_plugin(config_file)
        
        for config_file in self.plugins_dir.glob("*.[jJ][sS][oO][nN]"):
            self._load_json_plugin(config_file)
    
    def _load_python_plugin(self, plugin_file: Path) -> None:
        """Load a Python plugin file"""
        try:
            spec = importlib.util.spec_from_file_location("plugin_module", plugin_file)
            if spec is None or spec.loader is None:
                return
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Look for plugin classes
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, AgentPlugin) and 
                    obj is not AgentPlugin):
                    plugin_instance = obj()
                    self._register_agent_plugin(plugin_instance)
                elif (inspect.isclass(obj) and 
                      issubclass(obj, ResearchTrackPlugin) and 
                      obj is not ResearchTrackPlugin):
                    plugin_instance = obj()
                    self._register_research_track(plugin_instance)
        except Exception as e:
            print(f"Warning: Failed to load plugin {plugin_file}: {e}")
    
    def _load_yaml_plugin(self, config_file: Path) -> None:
        """Load a YAML configuration plugin"""
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            
            if isinstance(config, dict) and "agent" in config:
                agent_config = config["agent"]
                plugin = self._create_agent_from_config(agent_config, config_file.stem)
                if plugin:
                    self._register_agent_plugin(plugin)
            elif isinstance(config, dict) and "research_track" in config:
                track_config = config["research_track"]
                plugin = self._create_research_track_from_config(track_config, config_file.stem)
                if plugin:
                    self._register_research_track(plugin)
        except Exception as e:
            print(f"Warning: Failed to load YAML plugin {config_file}: {e}")
    
    def _load_json_plugin(self, config_file: Path) -> None:
        """Load a JSON configuration plugin"""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            if isinstance(config, dict) and "agent" in config:
                agent_config = config["agent"]
                plugin = self._create_agent_from_config(agent_config, config_file.stem)
                if plugin:
                    self._register_agent_plugin(plugin)
            elif isinstance(config, dict) and "research_track" in config:
                track_config = config["research_track"]
                plugin = self._create_research_track_from_config(track_config, config_file.stem)
                if plugin:
                    self._register_research_track(plugin)
        except Exception as e:
            print(f"Warning: Failed to load JSON plugin {config_file}: {e}")
    
    def _create_agent_from_config(self, config: Dict[str, Any], name: str) -> Optional[AgentPlugin]:
        """Create an agent plugin from configuration dictionary"""
        outer_self = self
        try:
            class ConfigAgentPlugin(AgentPlugin):
                def __init__(self, config_dict: Dict[str, Any]):
                    self._config = config_dict

                def get_agent_name(self) -> str:
                    return self._config.get("name", name)

                def get_description(self) -> str:
                    return self._config.get("description", f"A Dream Cycle agent: {name}")

                def get_version(self) -> str:
                    return self._config.get("version", "1.0.0")

                def get_research_tracks(self) -> List[ResearchTrackPlugin]:
                    tracks = []
                    for track_config in self._config.get("research_tracks", []):
                        track = outer_self._create_research_track_from_config(
                            track_config, f"{name}_track_{len(tracks)}"
                        )
                        if track:
                            tracks.append(track)
                    return tracks

            return ConfigAgentPlugin(config)
        except Exception:
            return None
    
    def _create_research_track_from_config(self, config: Dict[str, Any], name: str) -> Optional[ResearchTrackPlugin]:
        """Create a research track plugin from configuration dictionary"""
        try:
            class ConfigResearchTrackPlugin(ResearchTrackPlugin):
                def __init__(self, config_dict: Dict[str, Any]):
                    self._config = config_dict
                
                def get_name(self) -> str:
                    return self._config.get("name", name)
                
                def get_arxiv_queries(self) -> List[Dict[str, Any]]:
                    return self._config.get("arxiv_queries", [])
                
                def get_github_repos(self) -> List[str]:
                    return self._config.get("github_repos", [])
                
                def get_context(self) -> str:
                    return self._config.get("context", "")
                
                def get_cves_enabled(self) -> bool:
                    return self._config.get("fetch_cves", False)
                
                def get_github_trending_enabled(self) -> bool:
                    return self._config.get("fetch_github_trending", False)
            
            return ConfigResearchTrackPlugin(config)
        except Exception:
            return None
    
    def _register_agent_plugin(self, plugin: AgentPlugin) -> None:
        """Register an agent plugin"""
        name = plugin.get_agent_name()
        if name in self._loaded_plugins:
            print(f"Warning: Overwriting existing agent plugin: {name}")
        self._loaded_plugins[name] = plugin
    
    def _register_research_track(self, track: ResearchTrackPlugin) -> None:
        """Register a research track plugin"""
        name = track.get_name()
        if name in self._research_tracks:
            print(f"Warning: Overwriting existing research track: {name}")
        self._research_tracks[name] = track
    
    def get_agent_plugin(self, name: str) -> Optional[AgentPlugin]:
        """Get an agent plugin by name"""
        return self._loaded_plugins.get(name)
    
    def get_research_track(self, name: str) -> Optional[ResearchTrackPlugin]:
        """Get a research track plugin by name"""
        return self._research_tracks.get(name)
    
    def list_agent_plugins(self) -> List[str]:
        """List all loaded agent plugin names"""
        return list(self._loaded_plugins.keys())
    
    def list_research_tracks(self) -> List[str]:
        """List all loaded research track names"""
        return list(self._research_tracks.keys())
    
    def get_all_agent_plugins(self) -> Dict[str, AgentPlugin]:
        """Get all loaded agent plugins"""
        return self._loaded_plugins.copy()
    
    def get_all_research_tracks(self) -> Dict[str, ResearchTrackPlugin]:
        """Get all loaded research tracks"""
        return self._research_tracks.copy()


# Example plugin implementations
class ExampleAITrackPlugin(ResearchTrackPlugin):
    """Example research track plugin for cutting-edge AI"""
    
    def get_name(self) -> str:
        return "Cutting-Edge AI Research"
    
    def get_arxiv_queries(self) -> List[Dict[str, Any]]:
        return [
            {"query": "mixture of experts sparse models", "max_results": 5},
            {"query": "vision language models multimodal", "max_results": 5},
            {"query": "neural architecture search automated ml", "max_results": 4},
            {"query": "foundation models scaling laws", "max_results": 4}
        ]
    
    def get_github_repos(self) -> List[str]:
        return [
            "mistralai/mistral-src",
            "huggingface/peft",
            "google-research/vision_transformer",
            "deepmind/block_transformer"
        ]
    
    def get_context(self) -> str:
        return (
            "Focus on breakthrough architectures that push the boundaries of what's possible. "
            "Prioritize approaches that demonstrate significant improvements in efficiency, "
            "capability, or theoretical understanding."
        )
    
    def get_cves_enabled(self) -> bool:
        return False
    
    def get_github_trending_enabled(self) -> bool:
        return True


class ExampleSecurityTrackPlugin(ResearchTrackPlugin):
    """Example research track plugin for advanced security"""
    
    def get_name(self) -> str:
        return "Advanced Threat Research"
    
    def get_arxiv_queries(self) -> List[Dict[str, Any]]:
        return [
            {"query": "adversarial machine learning attacks defenses", "max_results": 6},
            {"query": "privacy preserving federated learning differential privacy", "max_results": 5},
            {"query": "secure multi-party computation homomorphic encryption", "max_results": 4},
            {"query": "AI safety robustness interpretability", "max_results": 4}
        ]
    
    def get_github_repos(self) -> List[str]:
        return [
            "Trusted-AI/adversarial-robustness-toolbox",
            "privacytechlab/differential-privacy",
            "OpenMined/PySyft",
            "anthropics/constitutional-ai"
        ]
    
    def get_context(self) -> str:
        return (
            "Focus on novel attack vectors, defensive techniques, and privacy-preserving technologies. "
            "Prioritize research with practical implementations and threat models relevant to production AI systems."
        )
    
    def get_cves_enabled(self) -> bool:
        return True
    
    def get_github_trending_enabled(self) -> bool:
        return False


# Example usage
if __name__ == "__main__":
    # Create plugin manager and load plugins
    pm = PluginManager()
    pm.load_plugins()
    
    print("Loaded agent plugins:", pm.list_agent_plugins())
    print("Loaded research tracks:", pm.list_research_tracks())
    
    # Example: Get a specific agent's research tracks
    for agent_name in pm.list_agent_plugins():
        agent = pm.get_agent_plugin(agent_name)
        if agent:
            print(f"\nAgent: {agent.get_agent_name()}")
            print(f"Description: {agent.get_description()}")
            print(f"Version: {agent.get_version()}")
            
            tracks = agent.get_research_tracks()
            for track in tracks:
                print(f"  Track: {track.get_name()}")
                print(f"    Context: {track.get_context()[:100]}...")
                print(f"    arXiv queries: {len(track.get_arxiv_queries())}")
                print(f"    GitHub repos: {len(track.get_github_repos())}")