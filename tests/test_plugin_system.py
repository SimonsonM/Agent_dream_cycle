#!/usr/bin/env python3
"""
Unit tests for plugin_system.py
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open

# Add the project root to the path so we can import plugin_system
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plugin_system import (
    ResearchTrackPlugin, 
    AgentPlugin, 
    PluginManager,
    ExampleAITrackPlugin,
    ExampleSecurityTrackPlugin
)


class TestPluginSystem(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        """Clean up after each test method."""
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_example_ai_track_plugin(self):
        """Test the example AI track plugin."""
        plugin = ExampleAITrackPlugin()
        
        self.assertEqual(plugin.get_name(), "Cutting-Edge AI Research")
        self.assertEqual(len(plugin.get_arxiv_queries()), 4)
        self.assertEqual(len(plugin.get_github_repos()), 4)
        self.assertIn("mixture of experts", plugin.get_arxiv_queries()[0]["query"])
        self.assertEqual(plugin.get_cves_enabled(), False)
        self.assertEqual(plugin.get_github_trending_enabled(), True)
        
    def test_example_security_track_plugin(self):
        """Test the example security track plugin."""
        plugin = ExampleSecurityTrackPlugin()
        
        self.assertEqual(plugin.get_name(), "Advanced Threat Research")
        self.assertEqual(len(plugin.get_arxiv_queries()), 4)
        self.assertEqual(len(plugin.get_github_repos()), 4)
        self.assertIn("adversarial machine learning", plugin.get_arxiv_queries()[0]["query"])
        self.assertEqual(plugin.get_cves_enabled(), True)
        self.assertEqual(plugin.get_github_trending_enabled(), False)
    
    @patch('plugin_system.Path.mkdir')
    @patch('plugin_system.importlib.util.spec_from_file_location')
    @patch('plugin_system.importlib.util.module_from_spec')
    def test_load_python_plugin(self, mock_module_from_spec, mock_spec_from_file_location, mock_mkdir):
        """Test loading a Python plugin."""
        # Setup mocks
        from unittest.mock import Mock
        mock_spec = mock_spec_from_file_location.return_value
        mock_spec.loader = Mock()
        mock_module = mock_module_from_spec.return_value
        
        # Create a mock plugin class
        class MockTrackPlugin(ResearchTrackPlugin):
            def get_name(self):
                return "Mock Track"
            def get_arxiv_queries(self):
                return []
            def get_github_repos(self):
                return []
            def get_context(self):
                return "Mock context"
        
        # Add the mock class to the module
        mock_module.MockTrackPlugin = MockTrackPlugin
        
        # Create plugin manager and test
        pm = PluginManager(Path(self.test_dir) / "plugins")
        pm._loaded_plugins = {}
        pm._research_tracks = {}
        
        # Mock the glob to return our test file
        with patch('plugin_system.Path.glob') as mock_glob:
            mock_glob.return_value = [Path(self.test_dir) / "test_plugin.py"]
            pm.load_plugins()
            
        # Verify the plugin was loaded
        self.assertIn("Mock Track", pm.list_research_tracks())
    
    def test_create_agent_from_config(self):
        """Test creating an agent from configuration."""
        pm = PluginManager()
        
        config = {
            "name": "Test Agent",
            "description": "A test agent",
            "version": "2.0.0",
            "research_tracks": [
                {
                    "name": "Test Track",
                    "arxiv_queries": [{"query": "test", "max_results": 5}],
                    "github_repos": ["test/repo"],
                    "context": "Test context",
                    "fetch_cves": True,
                    "fetch_github_trending": False
                }
            ]
        }
        
        plugin = pm._create_agent_from_config(config, "test_agent")
        self.assertIsNotNone(plugin)
        
        if plugin:
            self.assertEqual(plugin.get_agent_name(), "Test Agent")
            self.assertEqual(plugin.get_description(), "A test agent")
            self.assertEqual(plugin.get_version(), "2.0.0")
            
            tracks = plugin.get_research_tracks()
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].get_name(), "Test Track")
            self.assertEqual(tracks[0].get_cves_enabled(), True)
    
    def test_create_research_track_from_config(self):
        """Test creating a research track from configuration."""
        pm = PluginManager()
        
        config = {
            "name": "Test Track",
            "arxiv_queries": [{"query": "test query", "max_results": 10}],
            "github_repos": ["test/repo1", "test/repo2"],
            "context": "This is a test context",
            "fetch_cves": False,
            "fetch_github_trending": True
        }
        
        plugin = pm._create_research_track_from_config(config, "test_track")
        self.assertIsNotNone(plugin)
        
        if plugin:
            self.assertEqual(plugin.get_name(), "Test Track")
            self.assertEqual(len(plugin.get_arxiv_queries()), 1)
            self.assertEqual(plugin.get_arxiv_queries()[0]["max_results"], 10)
            self.assertEqual(len(plugin.get_github_repos()), 2)
            self.assertEqual(plugin.get_context(), "This is a test context")
            self.assertEqual(plugin.get_cves_enabled(), False)
            self.assertEqual(plugin.get_github_trending_enabled(), True)
    
    @patch('plugin_system.Path.glob')
    @patch('builtins.open', new_callable=mock_open)
    @patch('plugin_system.yaml.safe_load')
    def test_load_yaml_plugin(self, mock_yaml_load, mock_file, mock_glob):
        """Test loading a YAML plugin."""
        # Setup mocks
        mock_glob.return_value = [Path(self.test_dir) / "config.yaml"]
        mock_yaml_load.return_value = {
            "agent": {
                "name": "YAML Agent",
                "description": "Agent from YAML",
                "version": "1.5.0",
                "research_tracks": [
                    {
                        "name": "YAML Track",
                        "arxiv_queries": [{"query": "yaml test", "max_results": 3}],
                        "github_repos": ["yaml/repo"],
                        "context": "YAML context",
                        "fetch_cves": True,
                        "fetch_github_trending": False
                    }
                ]
            }
        }
        
        # Create plugin manager and test
        pm = PluginManager(Path(self.test_dir) / "plugins")
        pm._loaded_plugins = {}
        pm._research_tracks = {}
        
        pm.load_plugins()
        
        # Verify the plugin was loaded
        self.assertIn("YAML Agent", pm.list_agent_plugins())
        if "YAML Agent" in pm.list_agent_plugins():
            agent = pm.get_agent_plugin("YAML Agent")
            self.assertEqual(agent.get_description(), "Agent from YAML")
            self.assertEqual(agent.get_version(), "1.5.0")
            
            tracks = agent.get_research_tracks()
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].get_name(), "YAML Track")
    
    @patch('plugin_system.Path.glob')
    @patch('builtins.open', new_callable=mock_open)
    @patch('plugin_system.json.load')
    def test_load_json_plugin(self, mock_json_load, mock_file, mock_glob):
        """Test loading a JSON plugin."""
        # Setup mocks
        mock_glob.return_value = [Path(self.test_dir) / "config.json"]
        mock_json_load.return_value = {
            "research_track": {
                "name": "JSON Track",
                "arxiv_queries": [{"query": "json test", "max_results": 7}],
                "github_repos": ["json/repo1", "json/repo2", "json/repo3"],
                "context": "JSON context",
                "fetch_cves": False,
                "fetch_github_trending": True
            }
        }
        
        # Create plugin manager and test
        pm = PluginManager(Path(self.test_dir) / "plugins")
        pm._loaded_plugins = {}
        pm._research_tracks = {}
        
        pm.load_plugins()
        
        # Verify the plugin was loaded
        self.assertIn("JSON Track", pm.list_research_tracks())
        if "JSON Track" in pm.list_research_tracks():
            track = pm.get_research_track("JSON Track")
            self.assertEqual(track.get_name(), "JSON Track")
            self.assertEqual(len(track.get_arxiv_queries()), 1)
            self.assertEqual(track.get_arxiv_queries()[0]["max_results"], 7)
            self.assertEqual(len(track.get_github_repos()), 3)
            self.assertEqual(track.get_context(), "JSON context")
            self.assertEqual(track.get_cves_enabled(), False)
            self.assertEqual(track.get_github_trending_enabled(), True)


if __name__ == "__main__":
    unittest.main()