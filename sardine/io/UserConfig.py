from dataclasses import dataclass
import json
from pathlib import Path
from typing import Union

from appdirs import *
from rich import print


@dataclass
class Config:
    midi: Union[str, None]
    parameters: list
    ppqn: int
    bpm: int


def create_template_configuration_file(file_path: str) -> None:
    """ If no configuration file is found, create a template """
    template_configuration = {
            'config': {
                'midi': None,
                'bpm': 125,
                'ppqn': 48,
                'parameters': []
                }
            }
    with open(file_path, 'w') as file:
        json.dump(template_configuration, file)


def read_configuration_file(file_path: str) -> Union[Config, None]:
    """ Read config JSON File """
    with open(file_path, 'r') as f:
        data = json.load(f)
        data = data['config']
        try:
            print("[green][3/3] Returning configuration[/green]")
            return Config(
                    midi = data.get('midi'),
                    parameters = data.get('parameters'),
                    ppqn = data.get('ppqn'),
                    bpm = data.get('bpm'))
        except Exception as e:
            print(f"{e}")


def read_user_configuration() -> Union[Config, None]:
    """ Read or create user configuration file """
    appname, appauthor = "Sardine", "Bubobubobubo"
    user_dir = Path(user_data_dir(appname, appauthor))
    config_file = user_dir / "config.json"

    # Check if the configuration folder exists
    if user_dir.is_dir():
        print(f"[green][1/3] Found configuration folder at {user_dir}[/green]")

        if config_file.exists():
            print(f"[green][2/3] Found configuration file[/green]")
            return read_configuration_file(config_file)
        else:
            print(f"[green][2/3] Created template configuration file[/green]")
            create_template_configuration_file(config_file)
            return read_configuration_file(config_file)

    # If the configuration folder doesn't exist, create it and create config
    else:
        print(f"[green][1/3] Creating configuration folder[/green]")
        user_dir.mkdir(parents=True)
        print(f"[green][2/3] Creating configuration file[/green]")
        create_template_configuration_file(config_file)
        return read_configuration_file(config_file)

if __name__ == "__main__":
    config = read_user_configuration()