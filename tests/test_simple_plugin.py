#!/usr/bin/env python3
"""
Simple tests for plugin functionality
"""

import unittest
from plugin_system import ExampleAITrackPlugin, ExampleSecurityTrackPlugin


class TestExamplePlugins(unittest.TestCase):
    
    def test_ai_track_plugin(self):
        """Test the example AI track plugin."""
        plugin = ExampleAITrackPlugin()
        
        self.assertEqual(plugin.get_name(), "Cutting-Edge AI Research")
        self.assertGreater(len(plugin.get_arxiv_queries()), 0)
        self.assertGreater(len(plugin.get_github_repos()), 0)
        self.assertEqual(plugin.get_cves_enabled(), False)
        self.assertEqual(plugin.get_github_trending_enabled(), True)
        
    def test_security_track_plugin(self):
        """Test the example security track plugin."""
        plugin = ExampleSecurityTrackPlugin()
        
        self.assertEqual(plugin.get_name(), "Advanced Threat Research")
        self.assertGreater(len(plugin.get_arxiv_queries()), 0)
        self.assertGreater(len(plugin.get_github_repos()), 0)
        self.assertEqual(plugin.get_cves_enabled(), True)
        self.assertEqual(plugin.get_github_trending_enabled(), False)


if __name__ == "__main__":
    unittest.main()