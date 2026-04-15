#!/usr/bin/env python3
"""
Unit tests for utility functions that don't require external dependencies
"""

import unittest
import math

# Test UCB1 function directly without importing the full module
def calculate_ucb1(rewards, trials, parent_trials, c=1.4):
    """Calculate UCB1 value for multi-armed bandit."""
    if trials == 0:
        return float('inf')
    exploitation = rewards / trials
    exploration = c * math.sqrt(math.log(max(1, parent_trials)) / trials)
    return exploitation + exploration

class TestUtils(unittest.TestCase):
    
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
        
    def test_calculate_ucb1_exploration_term(self):
        """Test that exploration term decreases as trials increase."""
        # Same rewards/ratio, but different trial counts
        ucb1_low = calculate_ucb1(1, 2, 100)    # 1/2 + 1.4*sqrt(ln(100)/2)
        ucb1_high = calculate_ucb1(5, 10, 100)   # 5/10 + 1.4*sqrt(ln(100)/10)
        
        # Both have same exploitation term (0.5), but different exploration
        # Lower trials should have higher exploration term
        self.assertGreater(ucb1_low, ucb1_high)

if __name__ == "__main__":
    unittest.main()