#!/usr/bin/env python3
"""
Unit tests for dream_cycle.py - focusing on core functionality
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

# Add the project root to the path so we can import dream_cycle
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dream_cycle import (
    AGENT_PROFILES, 
    BASE_DIR, 
    LOCAL_MODEL, 
    CLAUDE_MODEL,
    UCB1_C_DEFAULT,
    get_arxiv_papers,
    calculate_ucb1,
    select_research_track
)


class TestDreamCycle(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.base_dir_patch = patch('dream_cycle.BASE_DIR', Path(self.test_dir))
        self.base_dir_patch.start()
        
    def tearDown(self):
        """Clean up after each test method."""
        self.base_dir_patch.stop()
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_agent_profiles_structure(self):
        """Test that AGENT_PROFILES has the expected structure."""
        # Check that we have the expected agents
        expected_agents = {"security", "marketing", "programming", "ai_research"}
        self.assertTrue(expected_agents.issubset(set(AGENT_PROFILES.keys())))
        
        # Check that each agent has required fields
        for agent_name, profile in AGENT_PROFILES.items():
            self.assertIn("name", profile)
            self.assertIn("tracks", profile)
            self.assertIn("arxiv_queries", profile)
            self.assertIn("default_github_repos", profile)
            self.assertIn("fetch_cves", profile)
            self.assertIn("fetch_github_trending", profile)
            self.assertIn("context", profile)
            
            # Check data types
            self.assertIsInstance(profile["name"], str)
            self.assertIsInstance(profile["tracks"], list)
            self.assertIsInstance(profile["arxiv_queries"], list)
            self.assertIsInstance(profile["default_github_repos"], list)
            self.assertIsInstance(profile["fetch_cves"], bool)
            self.assertIsInstance(profile["fetch_github_trending"], bool)
            self.assertIsInstance(profile["context"], str)
    
    def test_calculate_ucb1_basic(self):
        """Test UCB1 calculation with basic values."""
        # Test case: node with 5 rewards out of 10 trials, parent with 100 trials
        # UCB1 = 5/10 + 1.4 * sqrt(ln(100)/10) = 0.5 + 1.4 * sqrt(4.605/10) = 0.5 + 1.4 * sqrt(0.4605) = 0.5 + 1.4 * 0.6786 = 0.5 + 0.95 = 1.45
        ucb1 = calculate_ucb1(5, 10, 100)
        self.assertAlmostEqual(ucb1, 1.45, places=2)
        
    def test_calculate_ucb1_unvisited(self):
        """Test UCB1 calculation for unvisited node (should be infinity)."""
        ucb1 = calculate_ucb1(0, 0, 10)
        self.assertEqual(ucb1, float('inf'))
        
    def test_calculate_ucb1_zero_parent(self):
        """Test UCB1 calculation with zero parent trials."""
        ucb1 = calculate_ucb1(0, 5, 0)
        # When parent_trials is 0, log(0) is undefined, but we're using max(1, parent_trials)
        # So it should be log(1) = 0, making the exploration term 0
        self.assertEqual(ucb1, 0.0)  # 0/5 + 1.4 * sqrt(ln(1)/5) = 0 + 1.4 * 0 = 0
        
    @patch('dream_cycle.requests.get')
    def test_get_arxiv_papers_success(self, mock_get):
        """Test successful arXiv paper retrieval."""
        # Mock response with sample Atom XML
        sample_xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <id>http://arxiv.org/abs/2301.00001v1</id>
                <title>Test Paper Title</title>
                <summary>This is a test abstract.</summary>
                <author><name>Test Author</name></author>
                <published>2023-01-01T00:00:00Z</published>
            </entry>
        </feed>'''
        
        mock_response = MagicMock()
        mock_response.content = sample_xml.encode('utf-8')
        mock_get.return_value = mock_response
        
        papers = get_arxiv_papers("test query", max_results=1)
        
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]['title'], 'Test Paper Title')
        self.assertEqual(papers[0]['summary'], 'This is a test abstract.')
        self.assertEqual(papers[0]['authors'], ['Test Author'])
        
    @patch('dream_cycle.requests.get')
    def test_get_arxiv_papers_failure(self, mock_get):
        """Test arXiv paper retrieval failure handling."""
        mock_get.side_effect = Exception("Network error")
        
        papers = get_arxiv_papers("test query", max_results=10)
        
        # Should return empty list on failure
        self.assertEqual(papers, [])
        
    @patch('dream_cycle.get_arxiv_papers')
    def test_select_research_track(self, mock_get_arxiv):
        """Test research track selection logic."""
        # Mock arXiv responses for different tracks
        def side_effect(query, max_results):
            if "cybersecurity" in query:
                return [{'title': 'Paper 1'}, {'title': 'Paper 2'}]  # 2 papers
            elif "machine learning" in query:
                return [{'title': 'Paper 1'}, {'title': 'Paper 2'}, {'title': 'Paper 3'}, {'title': 'Paper 4'}, {'title': 'Paper 5'}]  # 5 papers
            else:
                return []  # No papers
                
        mock_get_arxiv.side_effect = side_effect
        
        # Test with a simple agent profile
        test_profile = {
            "name": "Test Agent",
            "arxiv_queries": [
                ("cybersecurity test", 2),
                ("machine learning test", 5)
            ]
        }
        
        selected_track = select_research_track(test_profile)
        
        # Should select the track with more papers (machine learning)
        self.assertEqual(selected_track, ("machine learning test", 5))


if __name__ == "__main__":
    unittest.main()