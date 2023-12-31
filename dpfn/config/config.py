"""Config parser for entire project."""
from ast import literal_eval
import configparser
from dpfn import logger
from typing import Any, Dict


def clean_hierarchy(config_dict: Dict[str, Any]) -> Dict[str, Any]:
  """Cleans the config hierarchy.

  For example, WandB sweep might set the value 'model.a' in the top level.
  This function will move property 'a' to the 'model' dict in the hierarchy.
  """
  for prefix in ["model", "data", "settings"]:
    # Wrap with list() otherwise dict changes during iteration
    prefix_ = f"{prefix}."
    keys_move = list(
      filter(lambda x: x.startswith(prefix_), config_dict.keys()))  # pylint: disable=cell-var-from-loop

    for key in keys_move:
      key_new = key.replace(prefix_, "")
      if key_new not in config_dict[prefix]:
        if not key_new.startswith("convertlog_"):
          raise ValueError(
            f"Only defined keys can be modified. Did not find key {key_new}"
            f" in {list(config_dict['model'].keys())}")
      config_dict[prefix][key_new] = config_dict[key]
      del config_dict[key]
  return config_dict


class ConfigBase(configparser.ConfigParser):
  """Configurations used for model and data."""

  def __init__(self, fname, **kwargs):
    super().__init__(**kwargs)
    self.read(fname)
    logger.info(f"Reading config: {fname}")

  def get_value(self, x, fallback=None):
    """Gets the value of a config property."""
    try:
      return literal_eval(self.get("DEFAULT", x))
    except configparser.NoOptionError:
      return fallback

  def to_dict(self):
    """Converts the config to a dictionary."""
    config_dict = dict(self.__getitem__("DEFAULT"))  # pylint: disable=unnecessary-dunder-call
    return {k: literal_eval(v) for k, v in config_dict.items()}
