"""
Configuration loader with support for multiple environments
"""

import os
import yaml
from typing import Dict, Any, Optional
from pathlib import Path


class ConfigLoader:
	"""Load configuration from YAML files with environment overrides"""
    
	def __init__(self, config_dir: Optional[str] = None):
		self.config_dir = config_dir or os.path.join(os.path.dirname(__file__), "config")
		self._config: Optional[Dict] = None
    
	def load(self, environment: Optional[str] = None) -> Dict[str, Any]:
		"""Load configuration for specified environment"""
		env = environment or os.getenv("ENVIRONMENT", "development")
        
		# Load base config
		base_config = self._load_yaml("base.yaml")
        
		# Load environment-specific config
		env_config = self._load_yaml(f"{env}.yaml")
        
		# Merge configurations
		config = self._merge_configs(base_config, env_config)
        
		# Override with environment variables
		config = self._apply_env_overrides(config)
        
		self._config = config
		return config
    
	def _load_yaml(self, filename: str) -> Dict[str, Any]:
		"""Load YAML file"""
		filepath = os.path.join(self.config_dir, filename)
		if not os.path.exists(filepath):
			return {}
        
		with open(filepath, 'r') as f:
			return yaml.safe_load(f) or {}
    
	def _merge_configs(self, base: Dict, override: Dict) -> Dict:
		"""Deep merge two configuration dictionaries"""
		result = base.copy()
        
		for key, value in override.items():
			if key in result and isinstance(result[key], dict) and isinstance(value, dict):
				result[key] = self._merge_configs(result[key], value)
			else:
				result[key] = value
        
		return result
    
	def _apply_env_overrides(self, config: Dict) -> Dict:
		"""Apply environment variable overrides"""
		env_prefix = "GEO_"
        
		def _apply(data: Dict, prefix: str = ""):
			for key, value in data.items():
				env_key = f"{env_prefix}{prefix}{key}".upper()
				env_value = os.getenv(env_key)
                
				if env_value is not None:
					# Type conversion based on existing value type
					if isinstance(value, bool):
						data[key] = env_value.lower() == "true"
					elif isinstance(value, int):
						data[key] = int(env_value)
					elif isinstance(value, float):
						data[key] = float(env_value)
					elif isinstance(value, list):
						data[key] = [v.strip() for v in env_value.split(",")]
					else:
						data[key] = env_value
				elif isinstance(value, dict):
					_apply(value, f"{key}_")
        
		_apply(config)
		return config
    
	def get(self, key: str, default=None):
		"""Get configuration value by dot-notation key"""
		if self._config is None:
			self.load()
        
		keys = key.split(".")
		value = self._config
        
		for k in keys:
			if isinstance(value, dict):
				value = value.get(k)
			else:
				return default
        
		return value if value is not None else default

