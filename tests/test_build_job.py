#!/usr/bin/env python3
"""
Unit tests for build_job.py
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

# Add the project root to the path so we can import build_job
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_job import apply_action, run_agent_build, log


class TestBuildJob(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.base_dir_patch = patch('build_job.BASE_DIR', Path(self.test_dir))
        self.logs_dir_patch = patch('build_job.LOGS_DIR', Path(self.test_dir) / "logs")
        self.base_dir_patch.start()
        self.logs_dir_patch.start()
        
    def tearDown(self):
        """Clean up after each test method."""
        self.base_dir_patch.stop()
        self.logs_dir_patch.stop()
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    @patch('build_job.subprocess.run')
    @patch('build_job.shutil.copy2')
    @patch('build_job.open', new_callable=mock_open)
    @patch('build_job.os.chmod')
    @patch('build_job.shutil.move')
    def test_apply_action_model_pull_success(self, mock_move, mock_chmod, mock_file, mock_copy, mock_run):
        """Test apply_action with successful model pull."""
        # Mock successful subprocess run
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        action = {
            "action_type": "model_pull",
            "content": "ollama pull test_model:latest",
            "rollback_command": "",
            "title": "Test Model Pull"
        }
        
        staged_file = Path(self.test_dir) / "test.staged"
        applied_dir = Path(self.test_dir) / "applied"
        applied_dir.mkdir()
        
        result = apply_action(action, staged_file, applied_dir)
        
        self.assertTrue(result)
        mock_run.assert_called()
        mock_chmod.assert_called()
        mock_move.assert_called()
        
    @patch('build_job.subprocess.run')
    def test_apply_action_model_pull_failure(self, mock_run):
        """Test apply_action with failed model pull."""
        # Mock failed subprocess run
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "model not found"
        mock_run.return_value = mock_result
        
        action = {
            "action_type": "model_pull",
            "content": "ollama pull nonexistent_model:latest",
            "rollback_command": "",
            "title": "Failed Model Pull"
        }
        
        staged_file = Path(self.test_dir) / "test.staged"
        applied_dir = Path(self.test_dir) / "applied"
        applied_dir.mkdir()
        
        result = apply_action(action, staged_file, applied_dir)
        
        self.assertFalse(result)
        
    @patch('build_job.Path.mkdir')
    @patch('build_job.shutil.copy2')
    @patch('build_job.open', new_callable=mock_open)
    @patch('build_job.os.chmod')
    @patch('build_job.shutil.move')
    @patch('build_job.Path.exists')
    def test_apply_action_documentation(self, mock_exists, mock_move, mock_chmod, mock_file, mock_copy, mock_mkdir):
        """Test apply_action with documentation action."""
        action = {
            "action_type": "documentation",
            "content": "# Test Documentation\nThis is a test.",
            "file_path": "~/test/doc.md",
            "rollback_command": "",
            "title": "Test Documentation"
        }
    
        staged_file = Path(self.test_dir) / "test.staged"
        applied_dir = Path(self.test_dir) / "applied"
        applied_dir.mkdir()
        
        # Mock that the target file doesn't exist (so no backup is made)
        mock_exists.return_value = False
    
        result = apply_action(action, staged_file, applied_dir)
    
        self.assertTrue(result)
        mock_mkdir.assert_called()
        # copy2 is only called if the file exists and needs backup
        mock_file.assert_called()
        mock_chmod.assert_called()
        mock_move.assert_called()
        
    @patch('build_job.Path.mkdir')
    @patch('build_job.open', new_callable=mock_open)
    @patch('build_job.os.chmod')
    @patch('build_job.shutil.move')
    def test_apply_action_script(self, mock_move, mock_chmod, mock_file, mock_mkdir):
        """Test apply_action with script action."""
        action = {
            "action_type": "script",
            "content": "#!/bin/bash\necho 'Hello World'",
            "file_path": "~/test/script.sh",
            "rollback_command": "",
            "title": "Test Script"
        }
        
        staged_file = Path(self.test_dir) / "test.staged"
        applied_dir = Path(self.test_dir) / "applied"
        applied_dir.mkdir()
        
        result = apply_action(action, staged_file, applied_dir)
        
        self.assertTrue(result)
        mock_mkdir.assert_called()
        mock_file.assert_called()
        mock_chmod.assert_called()
        mock_move.assert_called()
        
    @patch('build_job.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('build_job.Path.glob')
    @patch('build_job.apply_action')
    def test_run_agent_build_low_risk(self, mock_apply, mock_glob, mock_file, mock_exists):
        """Test run_agent_build with low risk actions."""
        # Mock file existence
        mock_exists.return_value = True
        
        # Mock manifest file
        manifest_data = [
            {
                "risk": "low",
                "title": "Low Risk Action",
                "file": str(Path(self.test_dir) / "low_risk.stged")
            }
        ]
        mock_file.return_value.read.return_value = json.dumps(manifest_data)
        
        # Mock glob to return manifest
        mock_manifest = MagicMock()
        mock_manifest.__str__ = lambda self: str(Path(self.test_dir) / "2026-01-01_manifest.json")
        mock_glob.return_value = [mock_manifest]
        
        # Mock apply_action to return True
        mock_apply.return_value = True
        
        applied, review_needed = run_agent_build("test_agent", "2026-01-01")
        
        self.assertEqual(len(applied), 1)
        self.assertEqual(len(review_needed), 0)
        self.assertEqual(applied[0], "Low Risk Action")
        
    @patch('build_job.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('build_job.Path.glob')
    @patch('build_job.apply_action')
    def test_run_agent_build_high_risk(self, mock_apply, mock_glob, mock_file, mock_exists):
        """Test run_agent_build with high risk actions."""
        # Mock file existence
        mock_exists.return_value = True
        
        # Mock manifest file
        manifest_data = [
            {
                "risk": "high",
                "title": "High Risk Action",
                "file": str(Path(self.test_dir) / "high_risk.stged")
            }
        ]
        mock_file.return_value.read.return_value = json.dumps(manifest_data)
        
        # Mock glob to return manifest
        mock_manifest = MagicMock()
        mock_manifest.__str__ = lambda self: str(Path(self.test_dir) / "2026-01-01_manifest.json")
        mock_glob.return_value = [mock_manifest]
        
        applied, review_needed = run_agent_build("test_agent", "2026-01-01")
        
        self.assertEqual(len(applied), 0)
        self.assertEqual(len(review_needed), 1)
        self.assertEqual(review_needed[0]["title"], "High Risk Action")
        self.assertEqual(review_needed[0]["risk"], "high")


if __name__ == "__main__":
    unittest.main()