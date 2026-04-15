#!/usr/bin/env python3
"""
Simple unit tests for manifest-based agent registry functionality
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Add the project root to the path so we can import dream_cycle
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dream_cycle import load_agent_manifests


class TestManifestAgentsSimple(unittest.TestCase):
    
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
        
        # Test with patched agents directory - we'll monkey patch the function
        import dream_cycle
        original_get_dirs = dream_cycle.get_agent_manifest_dirs
        dream_cycle.get_agent_manifest_dirs = lambda: [self.agents_dir]
        
        try:
            agents = load_agent_manifests()
            
            self.assertIn("test_agent", agents)
            self.assertEqual(agents["test_agent"]["name"], "Test Agent")
            self.assertEqual(agents["test_agent"]["version"], "1.0.0")
            self.assertEqual(agents["test_agent"]["memory_namespace"], "test_ns")
        finally:
            # Restore original function
            dream_cycle.get_agent_manifest_dirs = original_get_dirs
    
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
        import dream_cycle
        original_get_dirs = dream_cycle.get_agent_manifest_dirs
        dream_cycle.get_agent_manifest_dirs = lambda: [self.agents_dir]
        
        try:
            agents = load_agent_manifests()
            
            self.assertNotIn("inactive_agent", agents)
        finally:
            # Restore original function
            dream_cycle.get_agent_manifest_dirs = original_get_dirs
    
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
        import dream_cycle
        original_get_dirs = dream_cycle.get_agent_manifest_dirs
        dream_cycle.get_agent_manifest_dirs = lambda: [self.agents_dir]
        
        try:
            agents = load_agent_manifests()
            
            self.assertNotIn("incomplete_agent", agents)
        finally:
            # Restore original function
            dream_cycle.get_agent_manifest_dirs = original_get_dirs
    
    def test_load_agent_manifests_invalid_json(self):
        """Test that invalid JSON files are skipped."""
        # Create an invalid JSON file
        manifest_file = self.agents_dir / "invalid.json"
        with open(manifest_file, 'w') as f:
            f.write("{ invalid json content")
        
        # Test with patched agents directory
        import dream_cycle
        original_get_dirs = dream_cycle.get_agent_manifest_dirs
        dream_cycle.get_agent_manifest_dirs = lambda: [self.agents_dir]
        
        try:
            agents = load_agent_manifests()
            
            self.assertEqual(agents, {})  # Should be empty due to invalid JSON
        finally:
            # Restore original function
            dream_cycle.get_agent_manifest_dirs = original_get_dirs
    
    def test_load_agent_manifests_multiple(self):
        """Test loading multiple valid manifests."""
        # Create two valid manifests
        manifest1 = {
            "id": "agent_one",
            "name": "First Agent",
            "version": "1.0.0",
            "type": "research",
            "memory_namespace": "first",
            "scan_targets": ["~/research"],
            "active": True
        }
        
        manifest2 = {
            "id": "agent_two", 
            "name": "Second Agent",
            "version": "2.1.0",
            "type": "security",
            "memory_namespace": "second",
            "scan_targets": ["~/security"],
            "active": True
        }
        
        (self.agents_dir / "agent_one.json").write_text(json.dumps(manifest1))
        (self.agents_dir / "agent_two.json").write_text(json.dumps(manifest2))
        
        # Test with patched agents directory
        import dream_cycle
        original_get_dirs = dream_cycle.get_agent_manifest_dirs
        dream_cycle.get_agent_manifest_dirs = lambda: [self.agents_dir]
        
        try:
            agents = load_agent_manifests()
            
            self.assertIn("agent_one", agents)
            self.assertIn("agent_two", agents)
            self.assertEqual(len(agents), 2)
            self.assertEqual(agents["agent_one"]["name"], "First Agent")
            self.assertEqual(agents["agent_two"]["name"], "Second Agent")
        finally:
            # Restore original function
            dream_cycle.get_agent_manifest_dirs = original_get_dirs


if __name__ == "__main__":
    unittest.main()