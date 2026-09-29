import os
import unittest
from unittest.mock import patch

from evaluation.run_v09_experiments import discover_api_key


class Iteration06ConfigSafetyTests(unittest.TestCase):
    def test_experiment_runner_requires_explicit_environment_key(self):
        with patch.dict(os.environ, {"KEHENG_LLM_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "安全的后端环境变量"):
                discover_api_key()


if __name__ == "__main__":
    unittest.main()
