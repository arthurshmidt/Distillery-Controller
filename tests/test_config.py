from pathlib import Path

import pytest

from still.config import load_config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "still.yaml"


def test_load_shipped_config():
    config = load_config(CONFIG_PATH)
    assert config.default_profile in config.profiles
    assert set(config.profiles) >= {"whiskey", "gin"}
    assert config.channels.ai["deph_return"] == 0
    assert config.channels.ao["dephlegmator"] == 0
    assert config.profiles["whiskey"].output_limits == (30, 100)
    assert config.profiles["gin"].output_limits == (40, 100)


def test_missing_default_profile_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
thermistor: {r_fixed: 10000, beta: 3380, adc_max: 4095, calibration_factor: 3.0}
channels:
  ai: {deph_return: 0, deph_supply: 2, cond_return: 1, cond_supply: 3}
  ao: {dephlegmator: 0, condenser: 1}
default_profile: missing
profiles:
  whiskey:
    setpoint_f: 130
    pid: {p: -1.0, i: -0.01, d: 0.0}
    output_limits: [30, 100]
"""
    )
    with pytest.raises(ValueError):
        load_config(bad)
