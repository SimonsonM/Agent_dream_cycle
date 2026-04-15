#!/usr/bin/env python3
"""
Unit tests for the experimentation phase functionality
"""

import unittest
from unittest.mock import patch, MagicMock

# Add the project root to the path so we can import dream_cycle
import sys
sys.path.insert(0, '/home/mike/Agent_dream_cycle')

from dream_cycle import (
    phase_experimentation, 
    _determine_experiment_type,
    _define_success_metrics,
    _simulate_experiment_result,
    _get_experiment_metrics
)


class TestExperimentationPhase(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures."""
        self.profile = {
            "name": "Test Agent",
            "tracks": ["AI/ML"],
            "arxiv_queries": [],
            "default_github_repos": [],
            "fetch_cves": False,
            "fetch_github_trending": False,
            "context": "Test context"
        }
        
        self.scan = {
            "top_findings": [],
            "priority_track": "AI/ML",
            "priority_reason": "Test reason"
        }
        
        self.reflect = {
            "observations": [],
            "suggested_improvement": None
        }
        
        self.research = {
            "research": [],
            "synthesis": ""
        }
        
        self.dirs = {
            "staging_dir": MagicMock(),
            "applied_dir": MagicMock(),
            "logs_dir": MagicMock(),
            "agent_dir": MagicMock(),
            "perf_log": MagicMock(),
            "seen_cache": MagicMock()
        }
        
        self.date_str = "2026-04-14"
    
    def test_no_suggested_changes(self):
        """Test experimentation phase with no suggested changes."""
        result = phase_experimentation(
            self.profile, self.scan, self.reflect, self.research, 
            self.dirs, self.date_str
        )
        
        self.assertEqual(result["experiments"], [])
        self.assertEqual(result["validation_results"], {})
        self.assertEqual(result["validated_changes"], [])
    
    def test_with_suggested_changes(self):
        """Test experimentation phase with suggested changes."""
        self.research["research"] = [
            {
                "title": "Test Change",
                "description": "This is a test change to improve something",
                "suggests_change": True,
                "change_description": "We should implement this improvement",
                "applicability": "high",
                "applicable_to": ["testing"],
                "deep_summary": "This change will make things better"
            }
        ]
        
        result = phase_experimentation(
            self.profile, self.scan, self.reflect, self.research, 
            self.dirs, self.date_str
        )
        
        self.assertEqual(len(result["experiments"]), 1)
        # Note: validation depends on simulated score which may vary
        # We'll check that the validation results exist
        self.assertIn("Test Change", result["validation_results"])
        
        # Check experiment structure
        experiment = result["experiments"][0]
        self.assertEqual(experiment["change_title"], "Test Change")
        self.assertEqual(experiment["change_description"], "We should implement this improvement")
        self.assertEqual(experiment["hypothesis"], "Implementing 'Test Change' will improve performance in ['testing']")
        self.assertEqual(experiment["risk_level"], "low")
        
        # Check validation results structure
        validation = result["validation_results"]["Test Change"]
        self.assertIn("score", validation)
        self.assertIn("passed", validation)
        self.assertIn("metrics", validation)
        self.assertIn("recommendation", validation)
        
        # If validation passed, check validated change
        if validation["passed"]:
            self.assertEqual(len(result["validated_changes"]), 1)
            validated = result["validated_changes"][0]
            self.assertEqual(validated["title"], "Test Change")
            self.assertEqual(validated["description"], "We should implement this improvement")
            self.assertIn("validation_score", validated)
            self.assertIn("experiment_type", validated)
    
    def test_determine_experiment_type(self):
        """Test experiment type determination."""
        # Test model/LLM related changes
        change = {"description": "Update the LLM prompt for better results", "title": ""}
        self.assertEqual(_determine_experiment_type(change), "ab_test_prompt")
        
        # Test config related changes
        change = {"description": "Change configuration parameter", "title": ""}
        self.assertEqual(_determine_experiment_type(change), "config_validation")
        
        # Test tool/integration changes
        change = {"description": "Add new API integration", "title": ""}
        self.assertEqual(_determine_experiment_type(change), "mock_integration")
        
        # Test algorithm changes
        change = {"description": "Improve the algorithm approach", "title": ""}
        self.assertEqual(_determine_experiment_type(change), "algorithm_prototype")
        
        # Test default case
        change = {"description": "Some other change", "title": ""}
        self.assertEqual(_determine_experiment_type(change), "logic_validation")
    
    def test_define_success_metrics(self):
        """Test success metrics definition."""
        # Test performance-related change
        change = {"description": "Improve processing speed and efficiency"}
        metrics = _define_success_metrics(change, {})
        self.assertIn("performance_improvement", metrics)
        
        # Test quality-related change
        change = {"description": "Increase accuracy and quality of results"}
        metrics = _define_success_metrics(change, {})
        self.assertIn("quality_improvement", metrics)
        
        # Test cost-related change
        change = {"description": "Reduce token usage and cost"}
        metrics = _define_success_metrics(change, {})
        self.assertIn("cost_reduction", metrics)
        
        # Test user experience change
        change = {"description": "Improve user interface and experience"}
        metrics = _define_success_metrics(change, {})
        self.assertIn("user_satisfaction", metrics)
        
        # Test base metrics always present
        change = {"description": "Some change"}
        metrics = _define_success_metrics(change, {})
        self.assertIn("implementation_feasibility", metrics)
        self.assertIn("risk_assessment", metrics)
    
    def test_simulate_experiment_result(self):
        """Test experiment result simulation."""
        # Test with our updated baseline of 0.55
        baseline_change = {"description": "Some neutral change", "title": ""}
        baseline_score = _simulate_experiment_result(baseline_change, {})
        # Should be close to our baseline of 0.55
        self.assertAlmostEqual(baseline_score, 0.55, places=2)
        
        # Test positive indicators - documentation alone
        change_doc = {"description": "Add documentation", "title": ""}
        score_doc = _simulate_experiment_result(change_doc, {})
        
        # Test positive indicators - comment alone  
        change_comment = {"description": "Add comments", "title": ""}
        score_comment = _simulate_experiment_result(change_comment, {})
        
        # Test positive indicators - error handling
        change_safe = {"description": "Add error handling and validation", "title": ""}
        score_safe = _simulate_experiment_result(change_safe, {})
        
        # Test positive indicators - simplify/refactor
        change_simple = {"description": "Simplify and refactor code", "title": ""}
        score_simple = _simulate_experiment_result(change_simple, {})
        
        # These should have higher scores than baseline
        self.assertGreater(score_doc, baseline_score)
        self.assertGreater(score_comment, baseline_score)
        self.assertGreater(score_safe, baseline_score)
        self.assertGreater(score_simple, baseline_score)
        
        # Test negative indicators
        change_risky = {"description": "Delete and remove old code", "title": ""}
        score_risky = _simulate_experiment_result(change_risky, {})
        
        change_complex = {"description": "Make things more complex and intricate", "title": ""}
        score_complex = _simulate_experiment_result(change_complex, {})
        
        # These should have lower scores than baseline
        self.assertLess(score_risky, baseline_score)
        self.assertLess(score_complex, baseline_score)
        
        # Test clamping
        # Very positive change
        change_very_pos = {"description": "Add documentation comments error handling validation simplify refactor test", "title": ""}
        score_very_pos = _simulate_experiment_result(change_very_pos, {})
        self.assertLessEqual(score_very_pos, 1.0)
        
        # Very negative change
        change_very_neg = {"description": "delete remove destroy rewrite replace overhaul complex complicated intricate experimental beta", "title": ""}
        score_very_neg = _simulate_experiment_result(change_very_neg, {})
        self.assertGreaterEqual(score_very_neg, 0.0)
    
    def test_get_experiment_metrics(self):
        """Test experiment metrics generation."""
        experiment = {"change_title": "Test", "change_description": "Test"}
        metrics = _get_experiment_metrics(experiment)
        
        expected_metrics = [
            "implementation_feasibility", 
            "risk_assessment", 
            "performance_improvement", 
            "quality_improvement", 
            "cost_reduction", 
            "user_satisfaction"
        ]
        
        for metric in expected_metrics:
            self.assertIn(metric, metrics)
            self.assertIsInstance(metrics[metric], float)
            self.assertGreaterEqual(metrics[metric], 0.0)
            self.assertLessEqual(metrics[metric], 1.0)


if __name__ == "__main__":
    unittest.main()