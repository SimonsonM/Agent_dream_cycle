#!/usr/bin/env python3
"""
Unit tests for perf_log.py
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open

# Add the project root to the path so we can import perf_log
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from perf_log import get_log_file, log_event, AGENT_NAMES, BASE_DIR


class TestPerfLog(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.base_dir_patch = patch('perf_log.BASE_DIR', Path(self.test_dir))
        self.base_dir_patch.start()
        
    def tearDown(self):
        """Clean up after each test method."""
        self.base_dir_patch.stop()
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_get_log_file_global(self):
        """Test get_log_file with no agent specified (global)."""
        log_file = get_log_file(None)
        expected = Path(self.test_dir) / "performance.jsonl"
        self.assertEqual(log_file, expected)
        
    def test_get_log_file_specific_agent(self):
        """Test get_log_file with a specific agent."""
        log_file = get_log_file("security")
        expected = Path(self.test_dir) / "security" / "performance.jsonl"
        self.assertEqual(log_file, expected)
        
    def test_get_log_file_invalid_agent(self):
        """Test get_log_file with invalid agent falls back to global."""
        log_file = get_log_file("invalid_agent")
        expected = Path(self.test_dir) / "performance.jsonl"
        self.assertEqual(log_file, expected)
        
    def test_log_event_global(self):
        """Test logging an event without specifying an agent."""
        with patch("builtins.open", mock_open()) as mock_file:
            log_event(
                task="test task",
                outcome="success",
                model="test_model",
                duration=1.5,
                note="test note"
            )
            
            # Verify file was opened for appending
            mock_file.assert_called_once()
            handle = mock_file()
            
            # Verify JSON was written
            handle.write.assert_called_once()
            written_data = handle.write.call_args[0][0]
            
            # Parse and verify the JSON
            data = json.loads(written_data.strip())
            self.assertEqual(data["task"], "test task")
            self.assertEqual(data["outcome"], "success")
            self.assertEqual(data["model"], "test_model")
            self.assertEqual(data["duration_sec"], 1.5)
            self.assertEqual(data["note"], "test note")
            self.assertEqual(data["agent"], "global")
            
    def test_log_event_with_agent(self):
        """Test logging an event with a specific agent."""
        with patch("builtins.open", mock_open()) as mock_file:
            log_event(
                task="security task",
                outcome="failed",
                model="security_model",
                duration=2.0,
                note="security note",
                agent="security"
            )
            
            # Verify file was opened for appending
            mock_file.assert_called_once()
            handle = mock_file()
            
            # Verify JSON was written
            handle.write.assert_called_once()
            written_data = handle.write.call_args[0][0]
            
            # Parse and verify the JSON
            data = json.loads(written_data.strip())
            self.assertEqual(data["task"], "security task")
            self.assertEqual(data["outcome"], "failed")
            self.assertEqual(data["model"], "security_model")
            self.assertEqual(data["duration_sec"], 2.0)
            self.assertEqual(data["note"], "security note")
            self.assertEqual(data["agent"], "security")
            
    def test_log_event_creates_directories(self):
        """Test that log_event creates necessary directories."""
        with patch("builtins.open", mock_open()) as mock_file:
            with patch("pathlib.Path.mkdir") as mock_mkdir:
                log_event(
                    task="test task",
                    outcome="success",
                    model="test_model",
                    agent="marketing"
                )
                
                # Verify mkdir was called for the agent directory
                mock_mkdir.assert_called_with(parents=True, exist_ok=True)


if __name__ == "__main__":
    unittest.main()