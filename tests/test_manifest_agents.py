#!/usr/bin/env python3
"""
Unit tests for manifest-based agent registry functionality
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the project root to the path so we can import dream_cycle
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dream_cycle import get_agent_manifest_dirs, load_agent_manifests


class TestManifestAgents(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.agents_dir = Path(self.test_dir) / "agents"
        self.agents_dir.mkdir()
        
    def tearDown(self):
        """Clean up after each test method."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_get_agent_manifest_dirs_linux(self):
        """Test manifest directory detection on Linux."""
        with patch('platform.system', return_value='Linux'):
            dirs = get_agent_manifest_dirs()
            # Should include the Linux-specific directory and local agents dir
            linux_dir = Path.home() / ".dream_cycle" / "agents"
            local_dir = Path(__file__).parent / "agents"
            self.assertIn(linux_dir, dirs)
            self.assertIn(local_dir, dirs)
    
    def test_get_agent_manifest_dirs_macos(self):
        """Test manifest directory detection on macOS."""
        with patch('platform.system', return_value='Darwin'):
            dirs = get_agent_manifest_dirs()
            # Should include both macOS directories and local agents dir
            home_dir = Path.home() / ".dream_cycle" / "agents"
            lib_dir = Path.home() / "Library" / "Application Support" / "dream_cycle" / "agents"
            local_dir = Path(__file__).parent / "agents"
            self.assertIn(home_dir, dirs)
            self.assertIn(lib_dir, dirs)
            self.assertIn(local_dir, dirs)
    
    def test_get_agent_manifest_dirs_windows(self):
        """Test manifest directory detection on Windows."""
        with patch('platform.system', return_value='Windows'):
            with patch('os.getenv', return_value='/fake/appdata'):
                dirs = get_agent_manifest_dirs()
                # Should include Windows APPDATA directory and local agents dir
                win_dir = Path('/fake/appdata') / "dream_cycle" / "agents"
                local_dir = Path(__file__).parent / "agents"
                self.assertIn(win_dir, dirs)
                self.assertIn(local_dir, dirs)
    
    def test_load_agent_manifests_empty(self):
        """Test loading manifests when no agents directory exists."""
        # Temporarily replace the agents directory with a non-existent one
        with patch('dream_cycle.Path.__init__', return_value=None):
            with patch('dream_cycle.Path.parent') as mock_parent:
                mock_parent.return_value.__truediv__.return_value = Path(self.test_dir) / "nonexistent"
                agents = load_agent_manifests()
                self.assertEqual(agents, {})
    
    def test_load_agent_manifests_valid(self):
        """Test loading valid agent manifests."""
        # Create a valid manifest
        manifest_data = {
            "id": "test_agent",
            "name": "Test Agent",
            "version": "1.0.0",
            "type": "test",
            "memory_namespace": "test_ns",
            "scan_targets": ["~/test"],
            "active": True
        }
        
        manifest_file = self.agents_dir / "test_agent.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest_data, f)
        
        # Test with patched agents directory
        with patch('dream_cycle.get_agent_manifest_dirs') as mock_get_dirs:
            mock_get_dirs.return_value = [self.agents_dir]
            agents = load_agent_manifests()
            
            self.assertIn("test_agent", agents)
            self.assertEqual(agents["test_agent"]["name"], "Test Agent")
            self.assertEqual(agents["test_agent"]["version"], "1.0.0")
            self.assertEqual(agents["test_agent"]["memory_namespace"], "test_ns")
    
    def test_load_agent_manifests_inactive(self):
        """Test that inactive agents are not loaded."""
        # Create an inactive manifest
        manifest_data = {
            "id": "inactive_agent",
            "name": "Inactive Agent",
            "version": "1.0.0",
            "type": "test",
            "memory_namespace": "test_ns",
            "scan_targets": ["~/test"],
            "active": False  # This should be skipped
        }
        
        manifest_file = self.agents_dir / "inactive_agent.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest_data, f)
        
        # Test with patched agents directory
        with patch('dream_cycle.get_agent_manifest_dirs') as mock_get_dirs:
            mock_get_dirs.return_value = [self.agents_dir]
            agents = load_agent_manifests()
            
            self.assertNotIn("inactive_agent", agents)
    
    def test_load_agent_manifests_missing_fields(self):
        """Test that manifests with missing required fields are skipped."""
        # Create a manifest missing required fields
        manifest_data = {
            "id": "incomplete_agent",
            "name": "Incomplete Agent"
            # Missing version, type, memory_namespace, scan_targets, active
        }
        
        manifest_file = self.agents_dir / "incomplete_agent.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest_data, f)
        
        # Test with patched agents directory
        with patch('dream_cycle.get_agent_manifest_dirs') as mock_get_dirs:
            mock_get_dirs.return_value = [self.agents_dir]
            agents = load_agent_manifests()
            
            self.assertNotIn("incomplete_agent", agents)
    
    def test_load_agent_manifests_invalid_json(self):
        """Test that invalid JSON files are skipped."""
        # Create an invalid JSON file
        manifest_file = self.agents_dir / "invalid.json"
        with open(manifest_file, 'w') as f:
            f.write("{ invalid json content")
        
        # Test with patched agents directory
        with patch('dream_cycle.get_agent_manifest_dirs') as mock_get_dirs:
            mock_get_dirs.return_value = [self.agents_dir]
            agents = load_agent_manifests()
            
            self.assertEqual(agents, {})  # Should be empty due to invalid JSON


if __name__ == "__main__":
    unittest.main()